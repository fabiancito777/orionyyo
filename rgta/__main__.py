"""Punto de entrada: python -m rgta <comando>."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import detect, normalize
from .capture import Grabber
from .config import Config, load
from .driver import Tab
from .ocr import Reader


# ----------------------------------------------------------------------
def _load(args) -> Config:
    cfg = load(args.config)
    if getattr(args, "region", None):
        cfg_region = tuple(int(v) for v in args.region.split(","))
        args._region = cfg_region
    return cfg


def cmd_doctor(args) -> int:
    cfg = _load(args)
    print(f"python {sys.version.split()[0]}")
    try:
        grabber = Grabber(cfg, getattr(args, "_region", None))
        print(f"captura: {grabber.describe()}")
        fps = grabber.fps_probe(0.7)
        print(f"captura: {fps:.0f} fps maximos en esa zona")
    except Exception as exc:  # noqa: BLE001
        print(f"captura: ERROR {exc}")
        return 1
    try:
        reader = Reader(cfg)
        print(f"ocr: {reader.backend.name}")
    except SystemExit as exc:
        print(f"ocr: ERROR {exc}")
    for name, port in (("stream", cfg.stream_port), ("xbox", cfg.xbox_port), ("ps", cfg.ps_port)):
        try:
            tab = Tab(port, "", timeout=1.5)
            print(f"cdp {name} ({port}): {tab.url()}")
            tab.close()
        except Exception as exc:  # noqa: BLE001
            print(f"cdp {name} ({port}): ERROR {str(exc)[:120]}")
    return 0


def cmd_grab(args) -> int:
    cfg = _load(args)
    grabber = Grabber(cfg, getattr(args, "_region", None))
    frame = None
    for _ in range(5):
        frame = grabber.grab()
        if frame is not None:
            break
        time.sleep(0.05)
    if frame is None:
        print("no pude capturar")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    boxes = detect.find_candidates(frame, cfg)
    cv2.imwrite(str(out.with_name(out.stem + "-cajas.png")), detect.draw(frame, boxes))
    print(f"guardado {out} ({frame.shape[1]}x{frame.shape[0]}) cajas={len(boxes)}")
    for box in boxes:
        print(f"  caja {box.w}x{box.h} en ({box.x},{box.y}) fill={box.fill:.2f}")
    return 0


def _tune_image(cfg: Config, frame: np.ndarray, reader: Reader, show: bool) -> int:
    started = time.perf_counter()
    boxes = detect.find_candidates(frame, cfg)
    detect_ms = (time.perf_counter() - started) * 1000
    print(f"deteccion: {len(boxes)} cajas en {detect_ms:.1f} ms")
    preview = detect.draw(frame, boxes)

    for box in boxes:
        crop = detect.crop(frame, box)
        t0 = time.perf_counter()
        reads = reader.read(crop, is_valid=lambda t: normalize.looks_like_code(t, cfg))
        ocr_ms = (time.perf_counter() - t0) * 1000
        label = f"{box.w}x{box.h}@{box.x},{box.y}"
        print(f"  {label}: {ocr_ms:.1f} ms")
        for read in reads:
            codes = normalize.find_codes(read.text, cfg)
            mark = " ".join(f"{c.store}={c.text}({c.score:.1f})" for c in codes) or "sin codigo"
            print(f"    [{read.backend}/{read.variant}] {read.text!r} -> {mark}")
        cv2.putText(preview, label, (box.x, max(12, box.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

    if show:
        cv2.imshow("rgta tune", preview)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    return 0 if boxes else 1


def cmd_tune(args) -> int:
    cfg = _load(args)
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print(f"no puedo leer {args.image}")
            return 1
    else:
        grabber = Grabber(cfg, getattr(args, "_region", None))
        frame = grabber.grab()
        if frame is None:
            print("no pude capturar")
            return 1
    reader = Reader(cfg)
    print(f"imagen {frame.shape[1]}x{frame.shape[0]} | ocr={reader.backend.name}")
    return _tune_image(cfg, frame, reader, args.show)


def cmd_probe(args) -> int:
    cfg = _load(args)
    for store, port, hint in (
        ("xbox", cfg.xbox_port, cfg.xbox_url_hint),
        ("ps", cfg.ps_port, cfg.ps_url_hint),
    ):
        print(f"===== {store} ({port}) =====")
        try:
            tab = Tab(port, hint, timeout=3.0)
            tab.eval("location.href")
            from .stores import JS_LIB

            tab.eval(JS_LIB)
            info = tab.eval("window.__rgta.probe()")
            print(json.dumps(info, ensure_ascii=False, indent=2)[:6000])
            tab.close()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {exc}")
    return 0


# ----------------------------------------------------------------------
def _synthetic_frame(width: int = 1056, height: int = 778, ps: bool = False) -> np.ndarray:
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, (height // 4, width // 4, 3), dtype=np.uint8)
    frame = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)
    frame = cv2.GaussianBlur(frame, (31, 31), 0)
    overlay = np.zeros_like(frame)
    cv2.rectangle(overlay, (0, int(height * 0.55)), (width, height), (18, 40, 30), -1)
    frame = cv2.addWeighted(frame, 0.55, overlay, 0.75, 0)

    panel = np.zeros_like(frame)
    x0, y0 = int(width * 0.42), int(height * 0.62)
    cv2.rectangle(panel, (x0, y0), (min(width - 8, x0 + 540), y0 + 96), (22, 18, 26), -1)
    frame = cv2.addWeighted(frame, 0.65, panel, 0.75, 0)

    cv2.rectangle(frame, (x0 + 10, y0 + 20), (x0 + 52, y0 + 56), (60, 60, 66), -1)
    cv2.putText(frame, "Rubius", (x0 + 70, y0 + 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(frame, "CODIGO:", (x0 + 70, y0 + 66),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
    if ps:
        # Los de PlayStation son mas cortos: el mismo panel los dibuja mas grandes.
        cv2.putText(frame, "F27H-7E2L-HP9Q", (x0 + 70, y0 + 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.05, (140, 90, 240), 2, cv2.LINE_AA)
    else:
        # Xbox: 25 caracteres en la misma caja -> letra mas pequena.
        cv2.putText(frame, "MVXDW-9DFTV-CKQVD-FH3V9-CTCZP", (x0 + 210, y0 + 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (140, 90, 240), 1, cv2.LINE_AA)

    cv2.putText(frame, "Espera. Un momento.", (int(width * 0.3), height - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2, cv2.LINE_AA)
    return frame


def cmd_selftest(args) -> int:
    cfg = _load(args)
    reader = Reader(cfg)
    print(f"ocr: {reader.backend.name}")
    ok = True
    for ps in (False, True):
        label = "PLAYSTATION (12)" if ps else "XBOX (25)"
        print(f"===== {label} =====")
        frame = _synthetic_frame(ps=ps)
        if args.save:
            path = Path(args.save)
            suffix = "-ps" if ps else "-xbox"
            cv2.imwrite(str(path.with_name(path.stem + suffix + path.suffix)), frame)
        if _tune_image(cfg, frame, reader, args.show) != 0:
            ok = False
    return 0 if ok else 1


# ----------------------------------------------------------------------
def cmd_run(args) -> int:
    from .pipeline import Pipeline

    cfg = _load(args)
    pipeline = Pipeline(cfg, submit=not args.no_submit, region=getattr(args, "_region", None))
    pipeline.start()
    try:
        pipeline.run(seconds=args.seconds)
    except KeyboardInterrupt:
        print("\ninterrumpido")
    finally:
        pipeline.stop()
        time.sleep(0.3)
    print(f"registro: {pipeline.journal.path}")
    return 0


# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rgta", description="Lector de codigos en directo + canje")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--region", default=None, help="x0,y0,x1,y1 absolutos")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="comprobar captura, OCR y pestanas")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("grab", help="guardar un frame de la zona vigilada")
    p.add_argument("--out", default="logs/frame.png")
    p.set_defaults(func=cmd_grab)

    p = sub.add_parser("tune", help="probar deteccion+OCR sobre una imagen o en vivo")
    p.add_argument("--image", default=None)
    p.add_argument("--show", action="store_true")
    p.set_defaults(func=cmd_tune)

    p = sub.add_parser("probe", help="volcar el DOM de las pestanas de las tiendas")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("selftest", help="pipeline completo sobre una imagen sintetica")
    p.add_argument("--save", default="logs/selftest.png")
    p.add_argument("--show", action="store_true")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("run", help="bucle real")
    p.add_argument("--seconds", type=float, default=None)
    p.add_argument("--no-submit", action="store_true", help="leer sin canjear")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
