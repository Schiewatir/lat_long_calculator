"""Redis-backed rate limiter shared by all worker processes."""

import asyncio
import time

from redis.asyncio import Redis


class RateLimiter:
    def __init__(self, redis: Redis, key: str, per_second: float):
        self.redis = redis
        self.key = key
        self.window_ms = max(1000, int(1000 / per_second)) if per_second > 0 else 1000
        self.limit = max(1, int(per_second * self.window_ms / 1000))

    @classmethod
    def per_window(cls, redis: Redis, key: str, limit: int, window_s: float) -> "RateLimiter":
        """`limit` acquisitions per fixed window of `window_s` seconds (e.g. 30 per minute)."""
        lim = cls(redis, key, 1.0)
        lim.window_ms = max(1, int(window_s * 1000))
        lim.limit = max(1, limit)
        return lim

    async def try_acquire(self) -> float | None:
        """Non-blocking: None if a slot was taken, otherwise seconds until the next window opens."""
        now_ms = int(time.time() * 1000)
        window = now_ms // self.window_ms
        key = f"rl:{self.key}:{window}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.pexpire(key, self.window_ms * 2)
        if count <= self.limit:
            return None
        return ((window + 1) * self.window_ms - now_ms) / 1000

    async def acquire(self) -> None:
        while True:
            now_ms = int(time.time() * 1000)
            window = now_ms // self.window_ms
            key = f"rl:{self.key}:{window}"
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.pexpire(key, self.window_ms * 2)
            if count <= self.limit:
                return
            await asyncio.sleep((window + 1) * self.window_ms / 1000 - now_ms / 1000 + 0.005)


class NoopLimiter:
    async def acquire(self) -> None:
        return None
