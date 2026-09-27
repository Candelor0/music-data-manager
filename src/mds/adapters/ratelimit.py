"""限流器。

AcoustID 限流每秒最多 2 次（官方上限 3）；MusicBrainz 官方 1 次/秒。
本模块是**唯一出口**：所有网络请求都必须先 `limiter.wait()`。
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """最小间隔限流器：rate_per_sec=2 ⇒ 任意两次请求间隔 ≥ 0.5 秒。

    单线程使用即可；加锁是为了将来并行时也不会突破限制。
    """

    def __init__(self, rate_per_sec: float, name: str = "") -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec 必须大于 0")
        self.rate = float(rate_per_sec)
        self.name = name or "limiter"
        self.min_interval = 1.0 / self.rate
        self._last = 0.0
        self._lock = threading.Lock()
        self.total_wait = 0.0
        self.calls = 0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                sleep_for = self.min_interval - delta
                time.sleep(sleep_for)
                self.total_wait += sleep_for
            self._last = time.monotonic()
            self.calls += 1

    def stats(self) -> dict[str, float]:
        return {"calls": self.calls, "total_wait": round(self.total_wait, 2), "rate": self.rate}
