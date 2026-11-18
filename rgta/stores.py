"""Canje en Xbox y PlayStation via CDP.

No usa selectores fragiles: localiza el campo y los botones por su texto y
por su placeholder, y escribe con el setter nativo de HTMLInputElement para
que los frameworks (React) se enteren. El ultimo paso se pulsa desde dentro
de la pagina con un bucle de 8 ms, asi no se pierde ni un round-trip.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .driver import CDPError, Tab

JS_LIB = r"""
(() => {
  if (window.__rgta && window.__rgta.version === 3) return 'ready';
  const vis = el => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    if (parseFloat(s.opacity || '1') < 0.05) return false;
    return true;
  };
  const txt = el => ((el.innerText || el.value ||
      el.getAttribute('aria-label') || el.getAttribute('placeholder') || '') + '')
      .replace(/\s+/g, ' ').trim();
  const clickables = () => Array.from(document.querySelectorAll(
      'button,a,[role="button"],[role="link"],input[type="button"],input[type="submit"]')).filter(vis);
  const findInput = (sel, reSrc) => {
    if (sel) { const el = document.querySelector(sel); if (el && vis(el)) return el; }
    const re = new RegExp(reSrc, 'i');
    for (const el of Array.from(document.querySelectorAll('input,textarea'))) {
      if (!vis(el)) continue;
      const blob = [el.placeholder, el.getAttribute('aria-label'), el.name, el.id, el.type]
          .filter(Boolean).join(' ');
      if (re.test(blob)) return el;
    }
    return null;
  };
  const findClick = (sel, reSrc, needEnabled) => {
    if (sel) {
      const el = document.querySelector(sel);
      if (el && vis(el) && !(needEnabled && (el.disabled || el.getAttribute('aria-disabled') === 'true'))) return el;
    }
    const re = new RegExp(reSrc, 'i');
    for (const el of clickables()) {
      if (needEnabled && (el.disabled || el.getAttribute('aria-disabled') === 'true')) continue;
      if (re.test(txt(el))) return el;
    }
    return null;
  };
  const setValue = (el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    el.focus();
    if (desc && desc.set) desc.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return el.value;
  };
  window.__rgta = {
    version: 3, vis, txt, findInput, findClick,
    hasInput(sel, reSrc) { return !!findInput(sel, reSrc); },
    fill(sel, reSrc, value) {
      const el = findInput(sel, reSrc);
      if (!el) return { ok: false, reason: 'sin-campo' };
      const got = setValue(el, value);
      return { ok: true, value: got, placeholder: el.placeholder || '' };
    },
    click(sel, reSrc, needEnabled) {
      const el = findClick(sel, reSrc, needEnabled !== false);
      if (!el) return { ok: false };
      const label = txt(el);
      const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
      el.click();
      return { ok: true, label: label, disabled: disabled };
    },
    waitResult(resultReSrc, ms) {
      return new Promise(resolve => {
        const re = new RegExp(resultReSrc, 'i');
        const t0 = Date.now();
        const id = setInterval(() => {
          try {
            const body = document.body.innerText || '';
            const m = body.match(re);
            // Se llama DESPUES de pulsar Continuar: el resultado puede
            // estar ya en el DOM, asi que no se puede ignorar como previo.
            if (m) {
              clearInterval(id);
              resolve({ state: 'result', match: m[0], body: body.slice(0, 2000) });
              return;
            }
            if (Date.now() - t0 > ms) {
              clearInterval(id);
              resolve({ state: 'timeout', body: body.slice(0, 2000) });
            }
          } catch (e) {
            clearInterval(id);
            resolve({ state: 'error', error: String(e) });
          }
        }, 8);
      });
    },
    settle(sel, reSrc, resultReSrc, ms) {
      return new Promise(resolve => {
        const before = document.body.innerText || '';
        const resultRe = resultReSrc ? new RegExp(resultReSrc, 'i') : null;
        const t0 = Date.now();
        let clicked = false;
        const id = setInterval(() => {
          try {
            const body = document.body.innerText || '';
            if (clicked && resultRe) {
              const m = body.match(resultRe);
              if (m && !before.includes(m[0])) {
                clearInterval(id);
                resolve({ state: 'result', match: m[0], body: body.slice(0, 2000) });
                return;
              }
            }
            if (!clicked) {
              const el = findClick(sel, reSrc, true);
              if (el) { clicked = true; el.click(); }
            }
            if (Date.now() - t0 > ms) {
              clearInterval(id);
              resolve({ state: clicked ? 'timeout' : 'no-button', body: body.slice(0, 2000) });
            }
          } catch (e) {
            clearInterval(id);
            resolve({ state: 'error', error: String(e) });
          }
        }, 8);
      });
    },
    body(n) { return (document.body.innerText || '').slice(0, n || 2500); },
    probe() {
      const inputs = Array.from(document.querySelectorAll('input,textarea')).map(el => ({
        tag: el.tagName, type: el.type, ph: el.placeholder || '',
        aria: el.getAttribute('aria-label') || '', name: el.name || '',
        id: el.id || '', visible: vis(el)
      }));
      const buttons = clickables().slice(0, 60).map(el => ({
        tag: el.tagName, text: txt(el).slice(0, 70),
        disabled: !!el.disabled, aria: el.getAttribute('aria-label') || ''
      }));
      return { url: location.href, inputs: inputs, buttons: buttons };
    }
  };
  return 'installed';
})()
"""


SUCCESS_RE = (
    r"(enhorabuena|¡?listo|se ha canjeado|se ha agregado|se agreg[oó]|se ha a[nñ]adido|"
    r"ya (lo )?tienes|ya est[aá] en tu|canjeado correctamente|canjea realizado|"
    r"tu c[oó]digo se ha canjeado|c[oó]digo canjeado|redeemed|successfully|"
    r"already in your|gracias por canjear|thanks for redeeming|recompensa)"
)
ALREADY_RE = (
    r"(ya (se )?ha (sido )?canjeado|ya fue canjeado|ya (ha sido )?utilizado|"
    r"already (been )?redeemed|previously redeemed|ya (se )?us[oó]|ya usado|code has been used)"
)
INVALID_RE = (
    r"(no (es|parece) v[aá]lido|no v[aá]lid|c[oó]digo incorrecto|incorrect|"
    r"no podemos encontrar|comprueba el c[oó]digo|revisa el c[oó]digo|"
    r"introduce un c[oó]digo|vuelve a intentarlo|check the code|invalid)"
)
REGION_RE = r"(regi[oó]n|region|pa[ií]s|country)"
RATE_RE = r"(demasiad|int[eé]ntalo (de nuevo )?m[aá]s tarde|try again later|m[aá]s tarde|too many)"


@dataclass
class Result:
    status: str  # success | already | invalid | region | rate | unknown | error | not-ready
    detail: str = ""
    body: str = ""
    timings: dict = field(default_factory=dict)


def classify(text: str) -> str:
    import re

    if not text:
        return "unknown"
    for pattern, status in (
        (ALREADY_RE, "already"),
        (INVALID_RE, "invalid"),
        (REGION_RE, "region"),
        (RATE_RE, "rate"),
        (SUCCESS_RE, "success"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            return status
    return "unknown"


class StoreFlow:
    name = "base"
    input_re = r""
    next_re = r""
    confirm_re = r""
    opener_re = r""
    url_hint = ""
    port_attr = ""
    sel_input = ""
    sel_next = ""
    sel_confirm = ""
    tab_kinds: tuple[str, ...] = ("page",)
    frame_hint = ""
    has_confirm_step = True
    _ctx_id: int | None = None
    _ctx_ts: float = 0.0

    def __init__(self, cfg):
        self.cfg = cfg
        self.tab = self._make_tab()
        self.tab.eval(JS_LIB)

    def _make_tab(self) -> Tab:
        return Tab(
            int(getattr(self.cfg, self.port_attr)),
            self.url_hint,
            timeout=6.0,
            kinds=self.tab_kinds,
        )

    # ------------------------------------------------------------------
    def _js(self, function: str, *args) -> str:
        payload = ", ".join(json.dumps(a) for a in args)
        return f"window.__rgta.{function}({payload})"

    def _context(self) -> int | None:
        if not self.frame_hint:
            return None
        now = time.time()
        if self._ctx_id is not None and now - self._ctx_ts < 2.0:
            return self._ctx_id
        context_id = self.tab.frame_context(self.frame_hint)
        if context_id is not None:
            self.tab.eval_ctx(JS_LIB, context_id)
            self._ctx_id = context_id
            self._ctx_ts = now
        return context_id

    def _eval(self, function: str, *args, await_promise: bool = False, timeout: float = 30.0):
        js = self._js(function, *args)
        context_id = self._context()
        try:
            if context_id is not None:
                return self.tab.eval_ctx(js, context_id, await_promise=await_promise, timeout=timeout)
            return self.tab.eval(js, await_promise=await_promise, timeout=timeout)
        except CDPError:
            # El iframe puede recargarse y dejar el contexto invalido.
            self._ctx_id = None
            context_id = self._context()
            if context_id is not None:
                return self.tab.eval_ctx(js, context_id, await_promise=await_promise, timeout=timeout)
            raise

    # ------------------------------------------------------------------
    def has_input(self) -> bool:
        return bool(self._eval("hasInput", self.sel_input, self.input_re))

    def ensure_ready(self, timeout: float = 4.0, allow_opener: bool = True) -> bool:
        deadline = time.time() + timeout
        tried_opener = False
        while time.time() < deadline:
            if self.has_input():
                return True
            if allow_opener and not tried_opener:
                tried_opener = True
                try:
                    self._eval("click", "", self.opener_re, False)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.18)
        return False

    def fill(self, code: str) -> dict:
        return self._eval("fill", self.sel_input, self.input_re, code) or {}

    def press_next(self, timeout: float = 1.5) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self._eval("click", self.sel_next, self.next_re, True) or {}
            if last.get("ok") and not last.get("disabled"):
                return last
            time.sleep(0.05)
        return last

    def settle(self, timeout: float | None = None) -> dict:
        ms = int((timeout if timeout is not None else self.cfg.submit_timeout) * 1000)
        result = self._eval(
            "settle", self.sel_confirm, self.confirm_re, SUCCESS_RE, ms,
            await_promise=True, timeout=ms / 1000.0 + 5.0,
        )
        return result or {}

    def wait_result(self, timeout: float | None = None) -> dict:
        """Busca la respuesta del canje en TODOS los frames de checkout.

        Al pulsar Continuar, PlayStation cambia el iframe de /voucher a
        /unified/cart u otra URL, asi que quedarse en el contexto antiguo
        hacia que la lectura expirara aunque la tienda ya hubiera respondido.
        """
        limit = timeout if timeout is not None else self.cfg.submit_timeout
        deadline = time.perf_counter() + limit
        pattern = "|".join((SUCCESS_RE, ALREADY_RE, INVALID_RE, REGION_RE, RATE_RE))
        last_body = ""
        last_url = ""
        while time.perf_counter() < deadline:
            try:
                frames = self.tab.frames()
            except Exception:  # noqa: BLE001
                break
            for frame in frames:
                url = frame.get("url", "")
                if "checkout.playstation.com" not in url:
                    continue  # el store principal tiene texto que da falsos positivos
                try:
                    context_id = self.tab.frame_context_id(frame["id"])
                    body = self.tab.eval_ctx(
                        "(document.body.innerText||'').slice(0,2000)",
                        context_id, timeout=2.0,
                    ) or ""
                except Exception:  # noqa: BLE001
                    continue
                if body:
                    last_body = str(body)
                match = re.search(pattern, last_body, re.IGNORECASE)
                if match:
                    return {"state": "result", "match": match.group(0), "body": last_body,
                            "url": url}
                if "/voucher" not in url:
                    last_url = url
                    return {"state": "navigated", "body": last_body, "url": url}
            time.sleep(0.05)
        return {"state": "timeout", "body": last_body, "url": last_url}

    def body(self) -> str:
        return str(self._eval("body", 2500) or "")

    def probe(self) -> dict:
        return self._eval("probe") or {}

    def close_panel(self) -> None:
        try:
            self.tab.press_escape()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.12)
        try:
            self._eval("click", "", r"^(cerrar|close|×|✕)$", False)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def submit(self, code: str) -> Result:
        timings: dict[str, float] = {}
        start = time.perf_counter()

        if not self.ensure_ready():
            return Result("not-ready", "no encuentro el campo del codigo", timings=timings)
        timings["ready"] = (time.perf_counter() - start) * 1000

        filled = self.fill(code)
        timings["fill"] = (time.perf_counter() - start) * 1000
        if not filled.get("ok"):
            return Result("not-ready", "no pude escribir en el campo", timings=timings)

        clicked = self.press_next()
        timings["next"] = (time.perf_counter() - start) * 1000
        if not clicked.get("ok"):
            body = self.body()
            return Result(classify(body), "no encontre el boton siguiente", body, timings)

        # Xbox tiene un paso extra de confirmacion. PlayStation devuelve el
        # resultado en el mismo paso: pulsar "Continuar" otra vez lo romperia.
        settled = self.settle() if self.has_confirm_step else self.wait_result()
        timings["settle"] = (time.perf_counter() - start) * 1000
        body = str(settled.get("body") or "")
        state = settled.get("state")
        if state == "result":
            return Result(classify(str(settled.get("match") or "")), "ok", body, timings)
        if state == "no-button":
            return Result("invalid", "no aparecio el boton de confirmar", body, timings)
        return Result(classify(body), f"estado={state}", body, timings)


class XboxFlow(StoreFlow):
    name = "xbox"
    port_attr = "xbox_port"
    input_re = r"25|c[oó]digo|code|tarjeta"
    next_re = r"^(siguiente|next)$"
    confirm_re = r"^(canjear|redeem|confirmar|confirm)$"
    opener_re = r"(canjear c[oó]digo|canjear|redeem)"
    tab_kinds = ("page", "iframe")

    def __init__(self, cfg):
        self.url_hint = cfg.xbox_url_hint
        self.sel_input = cfg.xbox_input
        self.sel_next = cfg.xbox_next
        self.sel_confirm = cfg.xbox_confirm
        super().__init__(cfg)

    def _make_tab(self) -> Tab:
        # El formulario de Xbox vive en un iframe de microsoft.com dentro de
        # xbox.com. Si aun no existe, se pulsa "CANJEAR AHORA" en la pagina
        # principal y se espera a que aparezca el target del iframe.
        cfg = self.cfg
        try:
            return Tab(cfg.xbox_port, "redeemnow", timeout=1.0, kinds=self.tab_kinds)
        except Exception:
            main = Tab(cfg.xbox_port, cfg.xbox_url_hint, timeout=4.0)
            main.eval(JS_LIB)
            main.eval("window.__rgta.click('', 'canjear', false)")
            time.sleep(0.7)
            main.close()
            return Tab(cfg.xbox_port, "redeemnow", timeout=6.0, kinds=self.tab_kinds)


class PsFlow(StoreFlow):
    name = "ps"
    port_attr = "ps_port"
    input_re = r"12|c[oó]digo de cup[oó]n|cup[oó]n|coupon|voucher|c[oó]digo"
    next_re = r"^(continuar|continue)$"
    confirm_re = r"^(canjear|redeem|confirmar|confirm|continuar|continue)$"
    opener_re = r"(canjear c[oó]digo|redeem code|canjear|redeem)"
    frame_hint = "checkout.playstation.com/voucher"
    has_confirm_step = False

    def __init__(self, cfg):
        self.url_hint = cfg.ps_url_hint
        self.sel_input = cfg.ps_input
        self.sel_next = cfg.ps_next
        self.sel_confirm = cfg.ps_confirm
        super().__init__(cfg)

    def _real_click_in_frame(self, pattern: str) -> bool:
        """Clic real sobre un boton dentro del iframe, sumando el offset del
        iframe en la pagina principal (CDP usa coordenadas del viewport)."""
        context_id = self._context()
        if context_id is None:
            return False
        js = (
            "(()=>{const re=new RegExp(%s,'i');"
            "const b=[...document.querySelectorAll('button')]"
            ".find(x=>re.test((x.innerText||'').trim()));"
            "if(!b)return null;const r=b.getBoundingClientRect();"
            "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % json.dumps(pattern)
        )
        pos = self.tab.eval_ctx(js, context_id)
        offset = self.tab.eval(
            "(()=>{const f=[...document.querySelectorAll('iframe')]"
            ".find(x=>(x.src||'').includes('voucher'));"
            "if(!f)return null;const r=f.getBoundingClientRect();"
            "return {x:r.x,y:r.y};})()"
        )
        if not pos or not offset:
            return False
        self.tab.click_at(offset["x"] + pos["x"], offset["y"] + pos["y"])
        return True

    def submit(self, code: str) -> Result:
        # Si el panel quedo con un mensaje de la respuesta anterior
        # ("ya se uso"), wait_result lo veria de nuevo y clasificaria mal el
        # codigo siguiente. Se cierra y se reabre para partir del form limpio.
        try:
            self.close_panel()
            time.sleep(0.25)
        except Exception:  # noqa: BLE001
            pass
        return super().submit(code)

    def press_next(self, timeout: float = 1.5) -> dict:
        # Un element.click() desde un mundo aislado no siempre dispara el
        # manejador de React. Enter real primero; si no cambia nada, clic
        # real con coordenadas absolutas.
        before = self.body()
        try:
            self.tab.press_enter()
            time.sleep(0.4)
            if self.body() != before:
                return {"ok": True, "label": "Enter"}
            if self._real_click_in_frame(self.next_re):
                time.sleep(0.3)
                return {"ok": True, "label": "click-real"}
        except Exception:  # noqa: BLE001
            pass
        return super().press_next(timeout)

    def _real_click_text(self, pattern: str) -> bool:
        js = (
            "(()=>{const re=new RegExp(%s,'i');"
            "const b=[...document.querySelectorAll('button,a,[role=button]')]"
            ".find(x=>re.test((x.innerText||'').trim()));"
            "if(!b)return null;const r=b.getBoundingClientRect();"
            "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % json.dumps(pattern)
        )
        pos = self.tab.eval(js)
        if not pos:
            return False
        self.tab.click_at(pos["x"], pos["y"])
        return True

    def ensure_ready(self, timeout: float = 4.0, allow_opener: bool = True) -> bool:
        if self.has_input():
            return True
        for attempt in range(2):
            if allow_opener:
                try:
                    self.tab.eval(JS_LIB)
                    if self.tab.click_element("button.profile-icon"):
                        time.sleep(0.6)
                        self._real_click_text(r"^canjear c[oó]digo$")
                except Exception:  # noqa: BLE001
                    pass
            deadline = time.time() + min(timeout, 2.5)
            while time.time() < deadline:
                if self.has_input():
                    return True
                time.sleep(0.2)
            # El iframe puede quedarse en /unified/cart tras un canje: solo
            # se recupera recargando la tienda y volviendo a abrir el menu.
            try:
                self.tab.navigate(self.cfg.ps_url)
                time.sleep(3.0)
            except Exception:  # noqa: BLE001
                pass
        return self.has_input()


def make_flow(cfg, store: str) -> StoreFlow:
    return XboxFlow(cfg) if store == "xbox" else PsFlow(cfg)
