"""One place the Vault reads the time from.

Tests replace `now_ms` so they can control time. Everything stored is UTC.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ms() -> int:
    """Current time as integer milliseconds since the Unix epoch (UTC)."""
    return time.time_ns() // 1_000_000


def iso(ms: int) -> str:
    """Epoch milliseconds -> '2026-10-08T14:52:03.120Z'."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def now_iso() -> str:
    return iso(now_ms())


def parse_iso(text: str) -> int:
    """'2026-10-08T14:52:03.120Z' -> epoch milliseconds."""
    dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
