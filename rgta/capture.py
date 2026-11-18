"""Captura de una zona de la pantalla a la maxima velocidad posible.

mss (BitBlt) es el backend por defecto: ~4-8 ms para media pantalla a 1080p.
Si dxcam esta instalado se usa DXGI, que baja a ~2-4 ms y no depende del
compositor de Windows.
"""

from __future__ import annotations

import time

import numpy as np

from .config import Config


class Grabber:
    def __init__(self, cfg: Config, region: tuple[int, int, int, int] | None = None):
        self.cfg = cfg
        self.backend = "none"
        self._mss = None
        self._cam = None
        self.region = (0, 0, 0, 0)
        self.monitor_size = (0, 0)
        self._frame = None
        self._init(region)

    # ------------------------------------------------------------------
    def _init(self, override) -> None:
        import mss

        self._mss = mss.mss()
        index = self.cfg.monitor
        if index >= len(self._mss.monitors):
            index = 1 if len(self._mss.monitors) > 1 else 0
        mon = self._mss.monitors[index]
        self.monitor_size = (int(mon["width"]), int(mon["height"]))

        if override is not None:
            self.region = tuple(int(v) for v in override)
        else:
            left, top = int(mon["left"]), int(mon["top"])
            w, h = self.monitor_size
            self.region = (
                left + int(round(w * self.cfg.region_x0)),
                top + int(round(h * self.cfg.region_y0)),
                left + int(round(w * self.cfg.region_x1)),
                top + int(round(h * self.cfg.region_y1)),
            )
        x0, y0, x1, y1 = self.region
        if x1 - x0 < 32 or y1 - y0 < 32:
            raise SystemExit(f"Zona de captura invalida: {self.region}")

        if self.cfg.backend in ("auto", "dxcam"):
            try:
                import dxcam  # type: ignore

                self._cam = dxcam.create(output_idx=index - 1 if index else 0, output_color="BGR")
                # primer frame para confirmar que funciona
                self._cam.grab(region=self.region)
                self.backend = "dxcam"
            except Exception:
                self._cam = None
                if self.cfg.backend == "dxcam":
                    raise

        if self._cam is None:
            self.backend = "mss"

    # ------------------------------------------------------------------
    def grab(self) -> np.ndarray | None:
        if self.backend == "dxcam":
            frame = self._cam.grab(region=self.region)
            return frame
        shot = self._mss.grab(self.region)
        arr = np.frombuffer(shot.bgra, dtype=np.uint8)
        arr = arr.reshape((shot.height, shot.width, 4))
        return np.ascontiguousarray(arr[:, :, :3])

    def fps_probe(self, seconds: float = 1.0) -> float:
        end = time.perf_counter() + seconds
        n = 0
        while time.perf_counter() < end:
            if self.grab() is not None:
                n += 1
        return n / seconds

    def describe(self) -> str:
        x0, y0, x1, y1 = self.region
        return (
            f"backend={self.backend} monitor={self.monitor_size[0]}x{self.monitor_size[1]} "
            f"region=({x0},{y0})-({x1},{y1}) = {x1 - x0}x{y1 - y0}px"
        )
