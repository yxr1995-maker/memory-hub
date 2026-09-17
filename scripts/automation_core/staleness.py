"""Shared staleness policy: thresholds, env parsing, effective-date rule."""
from __future__ import annotations
import os
import sys
from datetime import date, datetime
from pathlib import Path
STALE_DAYS_ENV_VAR = "MEMORY_HUB_STALE_DAYS"
STALE_FACTOR_ENV_VAR = "MEMORY_HUB_STALE_FACTOR"
DEFAULT_STALE_DAYS = 180
DEFAULT_STALE_FACTOR = 0.5
_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")
def _warn(message: str) -> None:
    print(f"staleness: {message}", file=sys.stderr)
def _parse_int(raw: str, var: str, default: int) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        _warn(f"{var}={raw!r} invalid, using default {default}")
        return default
def _parse_float(raw: str, var: str, default: float) -> float:
    try:
        return float(raw.strip())
    except ValueError:
        _warn(f"{var}={raw!r} invalid, using default {default}")
        return default
def stale_days() -> int:
    raw = os.environ.get(STALE_DAYS_ENV_VAR, "").strip()
    return _parse_int(raw, STALE_DAYS_ENV_VAR, DEFAULT_STALE_DAYS) if raw else DEFAULT_STALE_DAYS
def stale_factor() -> float:
    raw = os.environ.get(STALE_FACTOR_ENV_VAR, "").strip()
    return _parse_float(raw, STALE_FACTOR_ENV_VAR, DEFAULT_STALE_FACTOR) if raw else DEFAULT_STALE_FACTOR
STALE_DAYS = stale_days()
STALE_FACTOR = stale_factor()
def parse_date(value: object) -> date | None:
    text = str(value or "").strip().strip("\u0027\"").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
def effective_date(last_verified: object = "", valid_at: object = "", updated: object = "") -> date | None:
    for value in (last_verified, valid_at, updated):
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    return None
