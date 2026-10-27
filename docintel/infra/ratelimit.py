"""每个引擎一个限流器：并发信号量 + RPM / TPM 两个令牌桶。"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator


class TokenBucket:
    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> None:
        if self.capacity <= 0:  # 0 = 不限
            return
        amount = min(amount, self.capacity)
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                await asyncio.sleep((amount - self.tokens) / self.rate)


class RateLimiter:
    def __init__(self, max_concurrency: int, rpm: int = 0, tpm: int = 0):
        self._sem = asyncio.Semaphore(max(max_concurrency, 1))
        self._rpm = TokenBucket(rpm)
        self._tpm = TokenBucket(tpm)

    @asynccontextmanager
    async def slot(self, est_tokens: int = 0) -> AsyncIterator[None]:
        async with self._sem:
            await self._rpm.acquire(1)
            await self._tpm.acquire(est_tokens)
            yield
