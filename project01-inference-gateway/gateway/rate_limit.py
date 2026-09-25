"""Per-tenant request token bucket."""

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_minute, burst, clock=time.monotonic):
        self.rate = rate_per_minute / 60.0
        self.burst = burst
        self.clock = clock
        self._buckets = {}
        self._lock = threading.Lock()

    def allow(self, tenant):
        """Return (allowed, seconds_until_next_token)."""
        with self._lock:
            now = self.clock()
            tokens, previous = self._buckets.get(tenant, (float(self.burst), now))
            tokens = min(self.burst, tokens + max(0.0, now - previous) * self.rate)
            if tokens >= 1.0:
                self._buckets[tenant] = (tokens - 1.0, now)
                return True, 0.0
            self._buckets[tenant] = (tokens, now)
            return False, (1.0 - tokens) / self.rate
