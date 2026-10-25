"""Shared, credential-safe handling for Amap Web Service responses."""

from __future__ import annotations


class AmapResponseError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code if isinstance(code, str) and code.isascii() and code.isdigit() and 4 <= len(code) <= 6 else None


def require_amap_success(payload: dict, *, operation: str) -> None:
    if str(payload.get("status")) == "1":
        return
    info = _safe_token(payload.get("info"), fallback="UNKNOWN_ERROR")
    infocode = _safe_token(payload.get("infocode"), fallback="unknown")
    raise AmapResponseError(f"Amap {operation} failed: {info} ({infocode})", code=infocode)


def _safe_token(value: object, *, fallback: str) -> str:
    text = str(value or fallback)
    return "".join(character for character in text if character.isalnum() or character in "_-.")[:80]
