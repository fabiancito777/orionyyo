"""Bucle principal: mirar la pantalla, leer, y encolar el canje.

El canje corre en hilos aparte (uno por tienda) para que la red y las esperas
de la pagina no paren la vigilancia: si sale otro codigo mientras se esta
canjeando uno, se sigue leyendo igual.
"""

from __future__ import annotations

import csv
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2

from . import detect, normalize
from .capture import Grabber
from .config import Config
from .ocr import Reader
from .stores import Result, make_flow


def format_code(code: str, store: str) -> str:
    size = 5 if store == "xbox" else 4
    return "-".join(code[i : i + size] for i in range(0, len(code), size))


def _beep(kind: str) -> None:
    try:
        import winsound

        if kind == "success":
            winsound.Beep(1400, 180)
            winsound.Beep(1900, 260)
        elif kind == "invalid":
            winsound.Beep(400, 140)
    except Exception:  # noqa: BLE001
        pass


class Journal:
    def __init__(self, log_dir: str):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = self.dir / f"eventos-{stamp}.csv"
        self._lock = threading.Lock()
        self._file = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(
            ["hora", "evento", "tienda", "codigo", "estado", "detalle",
             "ms_listo", "ms_escribir", "ms_siguiente", "ms_total"]
        )
        self._file.flush()

    def add(self, evento: str, tienda: str = "", codigo: str = "", estado: str = "",
            detalle: str = "", timings: dict | None = None) -> None:
        timings = timings or {}
        row = [
            datetime.now().strftime("%H:%M:%S.%f")[:-3], evento, tienda, codigo, estado, detalle,
            f"{timings.get('ready', 0):.1f}", f"{timings.get('fill', 0):.1f}",
            f"{timings.get('next', 0):.1f}", f"{timings.get('settle', 0):.1f}",
        ]
        with self._lock:
            self._writer.writerow(row)
            self._file.flush()


@dataclass
class Job:
    store: str
    code: str
    variants: list[str]
    detected_at: float


class Submitter(threading.Thread):
    def __init__(self, cfg: Config, store: str, enabled: bool, journal: Journal):
        super().__init__(daemon=True, name=f"submit-{store}")
        self.cfg = cfg
        self.store = store
        self.enabled = enabled
        self.journal = journal
        self.queue: queue.Queue[Job | None] = queue.Queue()
        self.flow = None
        self._last_ready = 0.0

    # ------------------------------------------------------------------
    def _connect(self):
        self.flow = make_flow(self.cfg, self.store)
        self.journal.add("conectado", self.store, detalle=self.flow.tab.url())
        return self.flow

    def _reset(self) -> None:
        # En Xbox el formulario es un iframe que desaparece al cerrar el
        # modal. Reconstruir el flow desde cero es mas fiable que navegar el
        # iframe (que ya no existe).
        try:
            if self.flow is not None:
                self.flow.close_panel()
        except Exception:  # noqa: BLE001
            pass
        self.flow = None
        for attempt in range(2):
            try:
                self._connect()
                if self.flow.ensure_ready(2.0):
                    return
            except Exception as exc:  # noqa: BLE001
                self.journal.add("reset-error", self.store, detalle=repr(exc)[:200])
            self.flow = None
            time.sleep(0.4)
        self.journal.add("reset-fail", self.store, detalle="no quedo listo para el siguiente codigo")

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            self._connect()
            self._reset()
        except Exception as exc:  # noqa: BLE001
            print(f"[{self.store}] no pude preparar la pestana: {exc}")
            self.flow = None

        while True:
            job = self.queue.get()
            if job is None:
                return
            if not self.enabled:
                print(f"[{self.store}] (sin enviar) {format_code(job.code, self.store)}")
                self.journal.add("detectado", self.store, format_code(job.code, self.store), "dry")
                continue
            try:
                self._attempt(job)
            except Exception as exc:  # noqa: BLE001
                self.journal.add("error", self.store, job.code, detalle=repr(exc)[:300])
                print(f"[{self.store}] error: {exc}")
                self.flow = None

    # ------------------------------------------------------------------
    def _attempt(self, job: Job) -> None:
        if self.flow is None:
            self._connect()

        ordered = [job.code] + [v for v in job.variants if v != job.code][:1]
        for index, code in enumerate(ordered):
            result: Result = self.flow.submit(code)  # type: ignore[union-attr]
            shown = format_code(code, self.store)
            retry = " (variante)" if index else ""
            extra = (f" | body={result.body[:220]!r}" if result.status != "success" and result.body else "")
            print(
                f"[{self.store}] {shown}{retry} -> {result.status} "
                f"total={result.timings.get('settle', 0):.0f}ms {result.detail}{extra}"
            )
            self.journal.add(
                "canje", self.store, shown, result.status,
                (result.detail + extra)[:400], result.timings,
            )
            if result.status == "success":
                _beep("success")
                break
            if result.status in ("invalid", "unknown"):
                continue  # vale la pena probar la siguiente variante
            break  # ya canjeado, rate limit, region, error: no insistir
        else:
            _beep("invalid")

        # La pagina se queda en la pantalla de resultado: hay que devolverla
        # a un estado listo para el siguiente codigo. El bucle de captura
        # sigue vivo porque esto corre en el hilo de esta tienda.
        self._last_ready = time.time()
        self._reset()

    def stop(self) -> None:
        self.queue.put(None)


class Pipeline:
    def __init__(self, cfg: Config, submit: bool = True, region=None):
        self.cfg = cfg
        self.submit = submit
        self.grabber = Grabber(cfg, region)
        self.reader = Reader(cfg)
        self.journal = Journal(cfg.log_dir)
        self.submitters = {
            store: Submitter(cfg, store, submit, self.journal) for store in ("xbox", "ps")
        }
        self.enqueued: set[str] = set()
        self.recent_codes: list[tuple[str, str, float]] = []
        self.recent: list[tuple[detect.Box, float, bool]] = []
        self.stop_event = threading.Event()
        self.last_frame = None
        self.stats = {"frames": 0, "ocr": 0, "boxes": 0, "hits": 0}

    # ------------------------------------------------------------------
    def _skip(self, box: detect.Box) -> bool:
        now = time.time()
        retry = self.cfg.retry_after_ms / 1000.0
        kept: list[tuple[detect.Box, float, bool]] = []
        skipped = False
        for old, stamp, ok in self.recent:
            # una caja de la que ya sacamos codigo se relee cada 1.5 s por
            # si la lectura anterior tenia un caracter mal; una que no dio
            # nada se reintenta antes.
            hold = 1.5 if ok else retry
            if now - stamp < hold:
                kept.append((old, stamp, ok))
                if detect.iou(box, old) > 0.4 and ok:
                    skipped = True
        self.recent = kept
        return skipped

    def _mark(self, box: detect.Box, ok: bool) -> None:
        self.recent.append((box, time.time(), ok))

    # ------------------------------------------------------------------
    def start(self) -> None:
        for submitter in self.submitters.values():
            submitter.start()

    def stop(self) -> None:
        self.stop_event.set()
        for submitter in self.submitters.values():
            submitter.stop()

    # ------------------------------------------------------------------
    def run(self, seconds: float | None = None) -> None:
        try:
            import keyboard

            keyboard.add_hotkey("f8", self.stop_event.set, suppress=False)
            keyboard.add_hotkey("f9", self._save_frame, suppress=False)
        except Exception as exc:  # noqa: BLE001
            print(f"    (hotkeys no disponibles: {exc}; usa Ctrl+C para parar)")

        period = 1.0 / max(1.0, self.cfg.fps)
        deadline = None if seconds is None else time.time() + seconds
        last_report = time.time()

        print(f"captura: {self.grabber.describe()}")
        print(f"ocr: {self.reader.backend.name} | canje: {'ACTIVO' if self.submit else 'solo lectura'}")
        print("F8 parar | F9 guardar frame | vigilando...")

        while not self.stop_event.is_set():
            if deadline is not None and time.time() > deadline:
                break

            started = time.perf_counter()
            frame = self.grabber.grab()
            if frame is None:
                time.sleep(0.005)
                continue
            self.last_frame = frame
            self.stats["frames"] += 1

            boxes = detect.find_candidates(frame, self.cfg)
            self.stats["boxes"] += len(boxes)

            for box in boxes:
                if self._skip(box):
                    continue
                crop = detect.crop(frame, box)
                is_valid = lambda text: normalize.looks_like_code(text, self.cfg)  # noqa: E731
                reads = self.reader.read(crop, is_valid=is_valid, max_tries=2)
                if not reads:
                    self._mark(box, False)
                    continue
                self.stats["ocr"] += 1

                def candidate_list(items) -> list:
                    found = []
                    for item in items:
                        codes = normalize.find_codes(item.text, self.cfg)
                        if codes:
                            found.append(codes[0])
                    return found

                found = candidate_list(reads)
                if found:
                    # Vale la pena pagar 2 lecturas mas: con 3-4 lecturas del
                    # mismo recorte se vota caracter a caracter y se corrigen
                    # O/Q, J/5, 0/O antes de enviar nada.
                    extra = self.reader.read(
                        crop, is_valid=is_valid, max_tries=4, all_variants=True
                    )
                    if extra:
                        reads = extra
                        found = candidate_list(reads)

                if not found:
                    self._mark(box, False)
                    continue

                agreed = normalize.consensus([c.text for c in found], self.cfg.charset_hint)
                best = next((c for c in found if c.text == agreed), found[0])
                if self.cfg.debug:
                    print(f"    lecturas={[c.text for c in found]} -> consenso={agreed}")

                if best.text in self.enqueued:
                    self._mark(box, True)
                    continue
                # Varias cajas del MISMO overlay pueden dar codigos que
                # difieren en 1-2 caracteres. No los envies como si fueran
                # codigos distintos: se pierde tiempo en intentos fallidos.
                now = time.time()
                self.recent_codes = [
                    item for item in self.recent_codes if now - item[2] < 3.0
                ]
                if any(
                    store == best.store and normalize.hamming(code, best.text) <= 2
                    for store, code, _ in self.recent_codes
                ):
                    self._mark(box, True)
                    continue

                self.enqueued.add(best.text)
                self.recent_codes.append((best.store, best.text, now))
                self.stats["hits"] += 1
                latency = (time.perf_counter() - started) * 1000
                print(
                    f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "
                    f"{best.store.upper():4} {format_code(best.text, best.store)} "
                    f"({latency:.0f}ms desde el frame) votos={[c.text for c in found]}"
                )
                self.journal.add("detectado", best.store, format_code(best.text, best.store),
                                 detalle=f"consenso de {len(found)} lecturas")
                self.submitters[best.store].queue.put(
                    Job(best.store, best.text,
                        normalize.variants(best.text, self.cfg.charset_hint, self.cfg.max_variants),
                        time.time())
                )
                self._mark(box, True)

            if self.cfg.debug and self.last_frame is not None:
                preview = detect.draw(self.last_frame, boxes)
                cv2.imshow("rgta", preview)
                if cv2.waitKey(1) & 0xFF == 27:
                    self.stop_event.set()

            now = time.time()
            if now - last_report > 5.0:
                elapsed_report = now - last_report
                last_report = now
                print(
                    f"    ... {self.stats['frames'] / max(1e-6, elapsed_report):.1f} fps, "
                    f"{self.stats['boxes']} cajas, {self.stats['hits']} codigos"
                )
                self.stats["frames"] = 0
                self.stats["boxes"] = 0

            elapsed = time.perf_counter() - started
            if elapsed < period:
                time.sleep(period - elapsed)

    # ------------------------------------------------------------------
    def _save_frame(self) -> None:
        if self.last_frame is None:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path(self.cfg.log_dir) / f"frame-{stamp}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), self.last_frame)
        boxes = detect.find_candidates(self.last_frame, self.cfg)
        cv2.imwrite(str(path.with_name(f"frame-{stamp}-cajas.png")), detect.draw(self.last_frame, boxes))
        print(f"    frame guardado: {path}")
