"""Cliente CDP minimo (Chrome DevTools Protocol) sobre websocket.

Se conecta a un Chrome ya abierto con --remote-debugging-port. Permite
ejecutar JS y pulsar cosas en una pestana que ni siquiera esta visible,
que es justo lo que necesitamos: el stream ocupa la pantalla y las tiendas
trabajan por detras.
"""

from __future__ import annotations

import itertools
import json
import time
import urllib.request

import websocket


class CDPError(RuntimeError):
    pass


class Tab:
    def __init__(self, port: int, url_hint: str = "", timeout: float = 5.0,
                 kinds: tuple[str, ...] = ("page",)):
        self.port = int(port)
        self.url_hint = url_hint
        self.kinds = kinds
        self._ws: websocket.WebSocket | None = None
        self._ids = itertools.count(1)
        self._connect(timeout)

    # ------------------------------------------------------------------
    def _targets(self) -> list[dict]:
        url = f"http://127.0.0.1:{self.port}/json/list"
        with urllib.request.urlopen(url, timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def _connect(self, timeout: float) -> None:
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                pages = [
                    t
                    for t in self._targets()
                    if t.get("type") in self.kinds and t.get("webSocketDebuggerUrl")
                ]
                if self.url_hint:
                    matching = [t for t in pages if self.url_hint in (t.get("url") or "")]
                    if matching:
                        pages = matching
                if pages:
                    self._ws = websocket.create_connection(
                        pages[0]["webSocketDebuggerUrl"], timeout=5.0, suppress_origin=True
                    )
                    return
            except Exception as exc:  # noqa: BLE001
                last = exc
            time.sleep(0.15)
        raise CDPError(f"No consigo conectarme al puerto {self.port} ({self.url_hint}): {last}")

    def ensure(self) -> None:
        if self._ws is None:
            self._connect(3.0)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    # ------------------------------------------------------------------
    def call(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        self.ensure()
        call_id = next(self._ids)
        payload = json.dumps({"id": call_id, "method": method, "params": params or {}})
        assert self._ws is not None
        self._ws.send(payload)
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise CDPError(f"timeout esperando {method}")
            self._ws.settimeout(remaining)
            try:
                message = json.loads(self._ws.recv())
            except websocket.WebSocketTimeoutException as exc:
                raise CDPError(f"timeout esperando {method}") from exc
            except Exception as exc:  # noqa: BLE001
                self.close()
                raise CDPError(f"conexion CDP rota en {method}: {exc}") from exc
            if message.get("id") != call_id:
                continue  # evento, se ignora
            if "error" in message:
                raise CDPError(f"{method}: {message['error']}")
            return message.get("result", {})

    def eval(self, expression: str, await_promise: bool = False, timeout: float = 30.0):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
                "timeout": int(timeout * 1000),
            },
            timeout=timeout + 2.0,
        )
        if "exceptionDetails" in result:
            raise CDPError(str(result["exceptionDetails"])[:500])
        return result.get("result", {}).get("value")

    def navigate(self, url: str, timeout: float = 10.0) -> None:
        self.call("Page.navigate", {"url": url}, timeout=timeout)

    def press_escape(self) -> None:
        for kind in ("keyDown", "keyUp"):
            self.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": "Escape",
                    "code": "Escape",
                    "windowsVirtualKeyCode": 27,
                    "nativeVirtualKeyCode": 27,
                },
            )

    def url(self) -> str:
        value = self.eval("location.href")
        return str(value or "")

    # ------------------------------------------------------------------
    def frame_context(self, url_hint: str) -> int | None:
        """Devuelve un executionContextId dentro de un iframe cross-origin.

        Brave no publica estos iframes como targets CDP, pero si aparecen en
        Page.getFrameTree. Con un mundo aislado se puede ejecutar JS dentro
        del frame y llegar al formulario.
        """
        tree = self.call("Page.getFrameTree", {}).get("frameTree", {})
        frames: list[dict] = []

        def walk(node: dict) -> None:
            frames.append(node.get("frame", {}))
            for child in node.get("childFrames", []):
                walk(child)

        walk(tree)
        matches = [f for f in frames if url_hint in (f.get("url") or "")]
        if not matches:
            return None
        world = self.call(
            "Page.createIsolatedWorld",
            {"frameId": matches[0]["id"], "worldName": "rgta", "grantUniveralAccess": True},
        )
        return int(world["executionContextId"])

    def eval_ctx(self, expression: str, context_id: int, await_promise: bool = False,
                 timeout: float = 30.0):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
                "contextId": context_id,
                "timeout": int(timeout * 1000),
            },
            timeout=timeout + 2.0,
        )
        if "exceptionDetails" in result:
            raise CDPError(str(result["exceptionDetails"])[:500])
        return result.get("result", {}).get("value")
