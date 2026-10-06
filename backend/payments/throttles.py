"""Redis-backed fixed-window rate limiting for payment creation.

Drops excessive retries (thundering herd) before they reach the DB.
Falls back to allowing requests when Redis is unavailable.
"""

import time

from django.conf import settings


class PaymentCreateThrottle:
    rate = getattr(settings, "PAYMENTS_CREATE_RATE", "20/min")

    def __init__(self):
        num, _, per = self.rate.partition("/")
        self.limit = int(num)
        self.window = {"sec": 1, "min": 60, "hour": 3600, "day": 86400}.get(per, 60)

    def allow_request(self, request, view) -> bool:
        url = getattr(settings, "REDIS_URL", "")
        if not url:
            return True
        try:
            import redis

            r = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
            ident = request.META.get("REMOTE_ADDR", "anon")
            key = f"throttle:payments:create:{ident}:{int(time.time() // self.window)}"
            count = r.incr(key)
            if count == 1:
                r.expire(key, self.window)
            return count <= self.limit
        except Exception:  # noqa: BLE001 - fail open, never block payments on Redis outage
            return True
