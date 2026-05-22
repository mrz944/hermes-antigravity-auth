from __future__ import annotations

import time


def now_ms() -> float:
    """Return current time in epoch milliseconds."""
    return time.time() * 1000
