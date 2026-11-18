"""Configuracion de rgta: dataclass con defaults + carga de config.toml."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    # captura
    backend: str = "auto"
    monitor: int = 1
    region_x0: float = 0.45
    region_y0: float = 0.28
    region_x1: float = 1.0
    region_y1: float = 1.0
    fps: float = 30.0

    # deteccion
    detect_scale: float = 0.5
    min_text_h: int = 8
    max_text_h: int = 150
    min_aspect: float = 2.5
    max_aspect: float = 45.0
    detect_threshold: int = 0
    max_boxes: int = 4

    # ocr
    ocr_backend: str = "auto"
    ocr_target_h: int = 44
    ocr_max_side: int = 1000
    min_score: float = 0.35

    # validacion
    xbox_len: int = 25
    ps_len: int = 12
    charset_hint: str = "23456789BCDFGHJKMPQRTVWXY"
    max_variants: int = 6
    retry_after_ms: int = 400

    # navegadores
    stream_port: int = 9222
    xbox_port: int = 9223
    ps_port: int = 9224
    xbox_url_hint: str = "xbox.com"
    ps_url_hint: str = "store.playstation.com"

    xbox_input: str = ""
    xbox_next: str = ""
    xbox_confirm: str = ""
    ps_input: str = ""
    ps_next: str = ""
    ps_confirm: str = ""

    xbox_url: str = "https://www.xbox.com/es-CO/redeem"
    ps_url: str = "https://store.playstation.com/es-co/pages/latest"

    submit_timeout: float = 8.0

    # varios
    log_dir: str = "logs"
    debug: bool = False


def load(path: str | Path = "config.toml") -> Config:
    cfg = Config()
    p = Path(path)
    if not p.is_file():
        return cfg
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"No puedo leer {p}: {exc}") from exc

    flat: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value

    known = {f.name: f for f in dataclasses.fields(Config)}
    for key, value in flat.items():
        if key not in known:
            print(f"[config] clave desconocida ignorada: {key}")
            continue
        current = getattr(cfg, key)
        try:
            if isinstance(current, bool):
                value = bool(value)
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            elif isinstance(current, str):
                value = str(value)
        except (TypeError, ValueError):
            print(f"[config] valor invalido para {key}: {value!r}, se usa {current!r}")
            continue
        setattr(cfg, key, value)

    return cfg
