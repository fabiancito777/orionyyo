"""Escritura del portapapeles de Windows sin dependencias externas.

Se usa ctypes directo en vez de pyperclip o subprocess para que copiar el
codigo cueste microseconds y no anada latencia al bucle de deteccion.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]


def set_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not _user32.OpenClipboard(0):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _user32.EmptyClipboard()
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            _kernel32.GlobalUnlock(handle)
        # A partir de aqui el handle es propiedad del sistema.
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _user32.CloseClipboard()
