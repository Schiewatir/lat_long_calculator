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
