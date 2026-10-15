"""
Rate Limiter for WhatsApp Agent Platform.

Strictly enforces per-minute quotas specified in the official WhatsApp manual:
- 12 requests/minute for POST /messages and POST /statuses
- 15 requests/minute for GET /updates
Uses a sliding token-bucket algorithm with threading lock for thread safety.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Sliding window rate limiter."""

    def __init__(self, max_calls: int, period_seconds: float = 60.0):
        self.max_calls = max_calls
        self.period = period_seconds
        self.timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block if necessary until a slot in the current window is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                # Remove timestamps older than the period window
                cutoff = now - self.period
                self.timestamps = [t for t in self.timestamps if t > cutoff]

                if len(self.timestamps) < self.max_calls:
                    self.timestamps.append(now)
                    return

                # Calculate sleep duration until oldest timestamp expires
                sleep_time = (self.timestamps[0] + self.period) - now

            if sleep_time > 0:
                time.sleep(min(sleep_time + 0.05, 5.0))
