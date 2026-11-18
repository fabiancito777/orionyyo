"""Limpieza, reparacion y expansion de lo que devuelve el OCR.

El OCR sobre texto de 15 pixeles se equivoca sobre todo en caracteres
ambiguos (0/O, 1/I, 5/S, 8/B, 2/Z, 6/G). Aqui se generan las variantes
plausibles y se ordenan para que la primera que se envia sea la mas probable.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# caracter -> alternativa tipica de confusion
CONFUSIONS: dict[str, tuple[str, ...]] = {
    "O": ("0", "Q"),
    "Q": ("O", "0"),
    "0": ("O", "Q"),
    "I": ("1", "L"),
    "1": ("I", "L"),
    "L": ("1", "I"),
    "S": ("5",),
    "5": ("S", "J"),
    "J": ("5",),
    "B": ("8",),
    "8": ("B",),
    "Z": ("2",),
    "2": ("Z",),
    "G": ("6", "C"),
    "C": ("G",),
    "6": ("G",),
    "D": ("0", "O"),
    "A": ("4",),
    "4": ("A",),
    "T": ("7",),
    "7": ("T",),
    "U": ("V",),
    "V": ("U",),
}

PREFIXES = ("CODIGOS", "CODIGO", "COUPONS", "COUPON", "CUPONES", "CUPON", "CODE", "REDEEM")


@dataclass
class Code:
    text: str
    store: str
    score: float


def fold(text: str) -> str:
    """Mayusculas, sin acentos, sin caracteres raros."""
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    return stripped.upper()


def _scan(raw: str) -> tuple[str, list[bool]]:
    """Devuelve solo alfanumericos ASCII y, por posicion, si habia un
    separador (guion, punto, espacio) detras en el texto original."""
    text: list[str] = []
    sep_after: list[bool] = []
    previous_index: int | None = None
    # SOLO guiones cuentan como separador de grupo. Espacios, barras, puntos
    # y guiones bajos aparecen en texto normal y generaban falsos positivos
    # leyendo codigos de frases cualquiera.
    separators = "-–—−"
    for index, char in enumerate(raw):
        if not (char.isascii() and char.isalnum()):
            continue
        if previous_index is not None and sep_after:
            gap = raw[previous_index + 1 : index]
            if any(c in gap for c in separators):
                sep_after[-1] = True
        text.append(char)
        sep_after.append(False)
        previous_index = index
    return "".join(text), sep_after


def _drop_prefixes(text: str, sep: list[bool], keep: int) -> tuple[str, list[bool]]:
    for prefix in PREFIXES:
        if text.startswith(prefix) and len(text) - len(prefix) >= keep:
            return text[len(prefix) :], sep[len(prefix) :]
    return text, sep


def _best_window(
    text: str, sep: list[bool], length: int, group: int, hint: str
) -> tuple[str, float] | None:
    if len(text) < length:
        return None
    best: tuple[float, str] | None = None
    max_start = min(4, len(text) - length)
    for start in range(0, max_start + 1):
        tail = len(text) - (start + length)
        if tail > 8:
            continue
        window = text[start : start + length]
        boundaries = [start + group * g - 1 for g in range(1, length // group)]
        hits = sum(1 for k in boundaries if 0 <= k < len(sep) and sep[k])
        # Un codigo real SIEMPRE viene con sus guiones (5-5-5-5-5 o 4-4-4).
        # Exigirlos descarta frases y codigos de fuente que el OCR inventa.
        if hits < length // group - 1:
            continue
        digits = sum(1 for c in window if c.isdigit())
        letters = length - digits
        if digits < 3 or letters < 3:
            continue
        hint_ratio = sum(1 for c in window if c in hint) / float(length)
        score = hits * 2.5 + hint_ratio * 3.0
        if start == 0:
            score += 1.5
        score -= tail * 0.15
        if length == 12 and len(text) > 14 and hits < 2:
            continue
        if best is None or score > best[0]:
            best = (score, window)
    if best is None:
        return None
    return best[1], best[0]


def find_codes(raw: str, cfg) -> list[Code]:
    """Extrae los codigos plausibles de un texto de OCR."""
    if not raw:
        return []
    text, sep = _scan(fold(raw))
    if len(text) < cfg.ps_len:
        return []
    text, sep = _drop_prefixes(text, sep, cfg.ps_len)

    found: list[Code] = []
    for length, group, store in (
        (cfg.xbox_len, 5, "xbox"),
        (cfg.ps_len, 4, "ps"),
    ):
        hit = _best_window(text, sep, length, group, cfg.charset_hint)
        if hit is None:
            continue
        code, score = hit
        found.append(Code(code, store, score))

    found.sort(key=lambda c: -c.score)
    return found


def variants(code: str, charset_hint: str, limit: int = 6) -> list[str]:
    """El codigo tal cual y luego variantes con una confusion corregida.
    Se prefiere siempre el caracter que existe en el alfabeto conocido."""
    base = list(code)
    swaps: list[tuple[int, str]] = []
    for index, char in enumerate(base):
        char_known = char in charset_hint
        for other in CONFUSIONS.get(char, ()):
            if other == char:
                continue
            if char_known and other not in charset_hint:
                continue
            swaps.append((index, other))

    out = [code]
    for index, other in swaps:
        candidate = base.copy()
        candidate[index] = other
        out.append("".join(candidate))
        if len(out) >= limit:
            break
    return out


def hamming(a: str, b: str) -> int:
    if len(a) != len(b):
        return 999
    return sum(1 for x, y in zip(a, b) if x != y)


def consensus(codes: list[str], charset_hint: str) -> str:
    """Elige, posicion a posicion, el caracter mas voting entre varias lecturas
    del mismo codigo. Cuando hay empate prefiere el alfabeto conocido."""
    codes = [c for c in codes if c]
    if not codes:
        return ""
    if len(set(codes)) == 1:
        return codes[0]
    length = len(codes[0])
    out: list[str] = []
    for index in range(length):
        votes: dict[str, int] = {}
        for code in codes:
            if len(code) == length:
                votes[code[index]] = votes.get(code[index], 0) + 1
        if not votes:
            out.append(codes[0][index])
            continue
        best = max(votes.values())
        tied = [char for char, count in votes.items() if count == best]
        if len(tied) == 1:
            out.append(tied[0])
        else:
            known = [char for char in tied if char in charset_hint]
            out.append(known[0] if known else tied[0])
    return "".join(out)


def looks_like_code(raw: str, cfg) -> bool:
    return bool(find_codes(raw, cfg))
