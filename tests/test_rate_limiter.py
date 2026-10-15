"""
Unit tests for sliding window RateLimiter.
"""

import time

from rate_limiter import RateLimiter


def test_rate_limiter_allows_calls_within_limit():
    """Calls within limit should execute immediately."""
    limiter = RateLimiter(max_calls=3, period_seconds=1.0)
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.2


def test_rate_limiter_throttles_exceeded_calls():
    """Fourth call on 3/0.3s limiter should throttle."""
    limiter = RateLimiter(max_calls=2, period_seconds=0.2)
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15
