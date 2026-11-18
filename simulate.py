"""Simulaciones del pipeline de lectura sin depender del directo.

Genera frames sinteticos con el overlay en posiciones y tamanos aleatorios
dentro de la zona vigilada, y mide deteccion, precision del codigo y latencia.

Uso:
    python simulate.py                 # 12 frames aleatorios
    python simulate.py --n 30
    python simulate.py --image frame.png   # probar sobre una captura real
"""

from __future__ import annotations

import argparse
import random
import string
import time

import cv2
import numpy as np

from rgta import detect, normalize
from rgta.config import load
from rgta.ocr import Reader

ALPHABET = string.ascii_uppercase + string.digits


def random_code(store: str) -> str:
    length = 25 if store == "xbox" else 12
    group = 5 if store == "xbox" else 4
    body = "".join(random.choice(ALPHABET) for _ in range(length))
    return "-".join(body[i : i + group] for i in range(0, length, group))


def make_frame(cfg, store: str, seed: int) -> tuple[np.ndarray, str]:
    rng = np.random.default_rng(seed)
    random.seed(seed)
    width = int(1920 * (cfg.region_x1 - cfg.region_x0))
    height = int(1080 * (cfg.region_y1 - cfg.region_y0))
    noise = rng.integers(0, 255, (max(8, height // 4), max(8, width // 4), 3), dtype=np.uint8)
    frame = cv2.GaussianBlur(cv2.resize(noise, (width, height)), (21, 21), 0)

    code = random_code(store)
    panel_w = 540 if store == "xbox" else 430
    panel_h = 90
    x0 = rng.integers(int(width * 0.05), max(int(width * 0.05) + 1, width - panel_w - 10))
    y0 = rng.integers(int(height * 0.12), max(int(height * 0.12) + 1, height - panel_h - 10))
    x0, y0 = int(x0), int(y0)

    overlay = np.zeros_like(frame)
    cv2.rectangle(overlay, (x0, y0), (x0 + panel_w, y0 + panel_h), (22, 18, 26), -1)
    frame = cv2.addWeighted(frame, 0.6, overlay, 0.8, 0)

    scale = 0.48 if store == "xbox" else 1.0
    cv2.putText(frame, "Rubius", (x0 + 16, y0 + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(frame, "CODIGO:", (x0 + 16, y0 + 58),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, code, (x0 + 170 if store == "xbox" else x0 + 16,
                              y0 + (58 if store == "xbox" else 88)),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (140, 90, 240), 1 if store == "xbox" else 2,
                cv2.LINE_AA)
    return frame, code.replace("-", "")


def run_frame(cfg, reader, frame: np.ndarray) -> tuple[list[str], float]:
    started = time.perf_counter()
    boxes = detect.find_candidates(frame, cfg)
    found: list[str] = []
    for box in boxes:
        crop = detect.crop(frame, box)
        reads = reader.read(
            crop,
            is_valid=lambda text: normalize.looks_like_code(text, cfg),
            max_tries=4,
            all_variants=True,
        )
        for read in reads:
            for code in normalize.find_codes(read.text, cfg):
                found.append(code.text)
    return found, (time.perf_counter() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--image", default=None)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    cfg = load()
    reader = Reader(cfg)
    print(f"ocr={reader.backend.name} | zona={int(1920*(cfg.region_x1-cfg.region_x0))}px de ancho")

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"No puedo leer {args.image}")
            return 1
        codes, ms = run_frame(cfg, reader, frame)
        print(f"{args.image}: {ms:.1f} ms -> {codes}")
        if args.show:
            cv2.imshow("simulacion", detect.draw(frame, detect.find_candidates(frame, cfg)))
            cv2.waitKey(0)
        return 0

    hits = exact = 0
    total_ms = 0.0
    for index in range(args.n):
        store = "xbox" if index % 2 == 0 else "ps"
        frame, expected = make_frame(cfg, store, index + 100)
        codes, ms = run_frame(cfg, reader, frame)
        total_ms += ms
        ok = any(c == expected for c in codes)
        found = bool(codes)
        hits += int(found)
        exact += int(ok)
        print(
            f"[{index+1:02}/{args.n}] {store:4} esperado={expected} "
            f"leido={codes} {ms:.1f}ms {'OK' if ok else 'FALLO'}"
        )
        if args.show:
            cv2.imshow("simulacion", detect.draw(frame, detect.find_candidates(frame, cfg)))
            cv2.waitKey(300)

    print(
        f"\nresultado: deteccion {hits}/{args.n} ({hits/args.n:.0%}) | "
        f"codigo exacto {exact}/{args.n} ({exact/args.n:.0%}) | "
        f"latencia media {total_ms/args.n:.1f}ms"
    )
    return 0 if exact == args.n else 1


if __name__ == "__main__":
    raise SystemExit(main())
