"""重试与熔断。适配器把可重试的错误（429 / 5xx / 网络）统一包成 RetryableError。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    """连续失败 N 次后熔断 cooldown 秒，期间直接失败，让页面走降级路径而不是排队等死。"""

    def __init__(self, failures: int, cooldown_s: float):
        self.threshold = failures
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None

    def check(self) -> None:
        if self.opened_at is None:
            return
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            self.opened_at = None  # 半开：放一个请求试试
            self.failures = self.threshold - 1
            return
        raise CircuitOpenError("引擎熔断中")

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            self.opened_at = None
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


async def with_retry(fn: Callable[[], Awaitable[T]], *, attempts: int = 5, base_s: float = 1.0,
                     cap_s: float = 60.0, breaker: CircuitBreaker | None = None) -> T:
    for attempt in range(1, attempts + 1):
        if breaker:
            breaker.check()
        try:
            result = await fn()
        except RetryableError as e:
            if breaker:
                breaker.record(False)
            if attempt == attempts:
                raise
            # 指数退避 + full jitter；服务端给了 retry-after 就听它的
            delay = e.retry_after if e.retry_after else random.uniform(0, min(cap_s, base_s * 2 ** (attempt - 1)))
            await asyncio.sleep(delay)
        else:
            if breaker:
                breaker.record(True)
            return result
    raise AssertionError("unreachable")
