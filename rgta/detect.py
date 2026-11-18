"""Localiza la linea del codigo en la zona vigilada sin saber donde cae
ni a que tamaño aparece.

Dos detectores complementarios sobre el frame reducido:

1) Contraste: top-hat + black-hat para realzar texto claro sobre oscuro y
   oscuro sobre claro por igual (el overlay es semitransparente).
2) Color: el codigo va en cian (Xbox) o rosa (PlayStation) y la etiqueta
   "CODIGO:" va en blanco. Una mascara de saturacion aisla solo el codigo,
   con lo que el OCR recibe menos ruido.

En ambos casos se cierran los huecos entre caracteres en horizontal para
que cada linea sea un componente conexo, y se filtra por geometria amplia.
El contenido lo valida el OCR, no la geometria.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    fill: float = 0.0

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h


def iou(a: Box, b: Box) -> float:
    x0 = max(a.x, b.x)
    y0 = max(a.y, b.y)
    x1 = min(a.x2, b.x2)
    y1 = min(a.y2, b.y2)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def _merge(boxes: list[Box], threshold: float = 0.45) -> list[Box]:
    out: list[Box] = []
    for box in boxes:
        for i, kept in enumerate(out):
            if iou(box, kept) > threshold:
                if box.w * box.h > kept.w * kept.h:
                    out[i] = box
                break
        else:
            out.append(box)
    return out


def _collect(mask: np.ndarray, inv: float, width: int, cfg) -> list[Box]:
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes: list[Box] = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < 6 or h < 3:
            continue
        wo, ho = w * inv, h * inv
        if not (cfg.min_text_h <= ho <= cfg.max_text_h):
            continue
        aspect = wo / max(1.0, ho)
        if not (cfg.min_aspect <= aspect <= cfg.max_aspect):
            continue
        if wo < 70 or wo > width * 0.95:
            continue
        fill = area / float(max(1, w * h))
        # El cierre horizontal puede convertir la linea en una barra maciza
        # (fill ~= 1) cuando la fuente es compacta. Esta bien: un componente
        # en vez de 29. El OCR valida despues.
        if not (0.03 <= fill <= 1.01):
            continue
        boxes.append(Box(int(x * inv), int(y * inv), int(wo), int(ho), float(fill)))
    boxes.sort(key=lambda b: -(b.w * b.h))
    return boxes


def find_candidates(frame: np.ndarray, cfg) -> list[Box]:
    if frame is None or frame.size == 0:
        return []
    height, width = frame.shape[:2]
    scale = float(cfg.detect_scale) if cfg.detect_scale > 0 else 1.0

    if scale != 1.0:
        small = cv2.resize(
            frame,
            (max(16, int(width * scale)), max(16, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = frame

    narrow_k = max(3, int(round(10 * scale)) | 1)
    wide_k = max(7, int(round(34 * scale)) | 1)
    horizontal_narrow = cv2.getStructuringElement(cv2.MORPH_RECT, (narrow_k, 1))
    horizontal_wide = cv2.getStructuringElement(cv2.MORPH_RECT, (wide_k, 1))
    inv = 1.0 / scale

    # ---- detector 1: contraste (polaridad agnostic) ------------------
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    k = max(3, int(round(15 * scale)) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    top = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    black = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    resp = cv2.max(top, black)

    contrast_boxes: list[Box] = []
    if int(resp.max()) >= 12:
        if cfg.detect_threshold > 0:
            thr = int(cfg.detect_threshold)
        else:
            otsu, _ = cv2.threshold(resp, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            thr = max(18, int(otsu))
        _, mask = cv2.threshold(resp, thr, 255, cv2.THRESH_BINARY)
        mask = cv2.max(
            cv2.morphologyEx(mask, cv2.MORPH_CLOSE, horizontal_narrow),
            cv2.morphologyEx(mask, cv2.MORPH_CLOSE, horizontal_wide),
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
        contrast_boxes = _collect(mask, inv, width, cfg)

    # ---- detector 2: texto coloreado --------------------------------
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    saturated = cv2.inRange(hsv[:, :, 1], 70, 255)
    bright = cv2.inRange(hsv[:, :, 2], 100, 255)
    color_mask = cv2.bitwise_and(saturated, bright)
    color_mask = cv2.max(
        cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, horizontal_narrow),
        cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, horizontal_wide),
    )
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    color_boxes = _collect(color_mask, inv, width, cfg)

    # El detector de color va primero: si acierta, el recorte llega limpio
    # al OCR (solo el codigo, sin la palabra "CODIGO:").
    return _merge(color_boxes + contrast_boxes)[: int(cfg.max_boxes)]


def crop(frame: np.ndarray, box: Box, pad_x: float = 0.55, pad_y: float = 0.45) -> np.ndarray:
    height, width = frame.shape[:2]
    px = int(round(box.h * pad_x))
    py = int(round(box.h * pad_y))
    x0 = max(0, box.x - px)
    y0 = max(0, box.y - py)
    x1 = min(width, box.x2 + px)
    y1 = min(height, box.y2 + py)
    return frame[y0:y1, x0:x1]


def draw(frame: np.ndarray, boxes: list[Box], color=(0, 255, 0)) -> np.ndarray:
    out = frame.copy()
    for box in boxes:
        cv2.rectangle(out, (box.x, box.y), (box.x2, box.y2), color, 2)
    return out
