"""Motores de OCR. Windows.Media.Ocr es el primario (rapido, nativo, sin
modelos que descargar). RapidOCR queda como alternativa si esta instalado.

Se prueban varias preparaciones del recorte porque el overlay es
semitransparente: no sabemos si el texto sera claro sobre oscuro, oscuro
sobre claro, o si hara falta binarizar. Se para en cuanto una lectura es
valida, asi que en el caso bueno solo se paga un OCR.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Read:
    text: str
    score: float
    variant: str
    backend: str


def _upscale(image: np.ndarray, target_h: int, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return image
    factor = max(1.0, target_h / float(height))
    out_w = max(8, int(round(width * factor)))
    out_h = max(8, int(round(height * factor)))
    longest = max(out_w, out_h)
    if longest > max_side:
        shrink = max_side / float(longest)
        out_w = max(8, int(out_w * shrink))
        out_h = max(8, int(out_h * shrink))
    if (out_w, out_h) == (width, height):
        return image
    return cv2.resize(image, (out_w, out_h), interpolation=cv2.INTER_CUBIC)


class WinRtBackend:
    name = "winrt"

    def __init__(self) -> None:
        from winsdk.windows.globalization import Language
        from winsdk.windows.media.ocr import OcrEngine

        self._engine = OcrEngine.try_create_from_user_profile_languages()
        if self._engine is None:
            for tag in ("es-ES", "es-CO", "en-US", "en-GB"):
                try:
                    self._engine = OcrEngine.try_create_from_language(Language(tag))
                except Exception:
                    self._engine = None
                if self._engine is not None:
                    break
        if self._engine is None:
            raise RuntimeError(
                "Windows OCR no tiene ningun paquete de idioma instalado. "
                "Configuracion > Hora e idioma > Idioma > Agregar idioma (con OCR)."
            )
        self._loop = asyncio.new_event_loop()
        from winsdk.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winsdk.windows.security.cryptography import CryptographicBuffer

        self._SoftwareBitmap = SoftwareBitmap
        self._format = BitmapPixelFormat
        self._alpha = BitmapAlphaMode
        self._buffer = CryptographicBuffer

    def read(self, image: np.ndarray) -> str:
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        height, width = gray.shape[:2]
        bgra = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGRA)
        raw = bytearray(bgra.tobytes())
        buffer = self._buffer.create_from_byte_array(raw)
        bitmap = self._SoftwareBitmap.create_copy_from_buffer(
            buffer, self._format.BGRA8, width, height, self._alpha.PREMULTIPLIED
        )
        result = self._loop.run_until_complete(self._engine.recognize_async(bitmap))
        return (result.text or "").strip()


class RapidBackend:
    name = "rapid"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        self._engine = RapidOCR()

    def read(self, image: np.ndarray) -> tuple[str, float]:
        try:
            result = self._engine(image, use_det=False, use_cls=False, use_rec=True)
        except TypeError:
            result = self._engine(image)
        payload = result[0] if isinstance(result, tuple) else result
        if not payload:
            return "", 0.0
        first = payload[0] if isinstance(payload, (list, tuple)) else payload
        if isinstance(first, (list, tuple)):
            text = str(first[0])
            score = float(first[1]) if len(first) > 1 else 0.0
            return text.strip(), score
        return str(first).strip(), 0.0


class Reader:
    def __init__(self, cfg):
        self.cfg = cfg
        self.backend = None
        requested = (cfg.ocr_backend or "auto").lower()
        errors: list[str] = []

        if requested in ("auto", "winrt"):
            try:
                self.backend = WinRtBackend()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"winrt: {exc}")
        if self.backend is None and requested in ("auto", "rapid"):
            try:
                self.backend = RapidBackend()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"rapid: {exc}")
        if self.backend is None:
            raise SystemExit("Sin motor de OCR. " + " | ".join(errors))

    # ------------------------------------------------------------------
    def _variants(self, crop: np.ndarray):
        prepared = _upscale(crop, int(self.cfg.ocr_target_h), int(self.cfg.ocr_max_side))
        gray = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY) if prepared.ndim == 3 else prepared
        yield "gray", gray
        yield "inv", cv2.bitwise_not(gray)
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        yield "otsu", otsu
        yield "otsu_inv", cv2.bitwise_not(otsu)

    def read(self, crop: np.ndarray, is_valid=None, max_tries: int = 2,
             all_variants: bool = False, skip=frozenset()) -> list[Read]:
        """max_tries cuenta preparaciones desde la primera, incluidas las
        de skip (que ya se leyeron antes y no se vuelven a pasar al OCR)."""
        reads: list[Read] = []
        for index, (name, image) in enumerate(self._variants(crop)):
            if index >= max_tries:
                break
            if name in skip:
                continue
            try:
                if isinstance(self.backend, WinRtBackend):
                    text, score = self.backend.read(image), 1.0
                else:
                    text, score = self.backend.read(image)  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                continue
            if not text:
                continue
            if score < self.cfg.min_score and self.backend.name == "rapid":
                continue
            reads.append(Read(text, score, name, self.backend.name))
            if not all_variants and is_valid is not None and is_valid(text):
                break
        return reads
