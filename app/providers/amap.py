"""Shared, credential-safe handling for Amap Web Service responses."""

from __future__ import annotations


class AmapResponseError(RuntimeError):
    pass


def require_amap_success(payload: dict, *, operation: str) -> None:
    if str(payload.get("status")) == "1":
        return
    info = _safe_token(payload.get("info"), fallback="UNKNOWN_ERROR")
    infocode = _safe_token(payload.get("infocode"), fallback="unknown")
    raise AmapResponseError(f"Amap {operation} failed: {info} ({infocode})")


def _safe_token(value: object, *, fallback: str) -> str:
    text = str(value or fallback)
    return "".join(character for character in text if character.isalnum() or character in "_-.")[:80]
