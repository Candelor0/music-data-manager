"""AcoustID 客户端（指纹 → recording MBID）。

限流：限流约定：**每秒最多 2 次**，所有请求必须经过传入的 RateLimiter。
密钥只以参数形式出现，**绝不写进日志或异常信息**。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..logging_setup import get_logger
from .ratelimit import RateLimiter

log = get_logger("acoustid")

API_URL = "https://api.acoustid.org/v2/lookup"
_RETRYABLE = {429, 500, 502, 503, 504}


@dataclass
class AcoustIDResult:
    status: str  # ok | no_result | error
    score: float = 0.0
    recording_mbid: str = ""
    title: str = ""
    artists: list[str] = field(default_factory=list)
    n_candidates: int = 0
    error: str = ""


def _sanitize(message: str, api_key: str) -> str:
    """异常信息里若混进了 key，抹掉（双保险，日志层还有一道）。"""
    return message.replace(api_key, "***") if api_key else message


def lookup(
    api_key: str,
    fingerprint: str,
    duration_sec: int,
    limiter: RateLimiter,
    *,
    client: httpx.Client | None = None,
    timeout: float = 25.0,
    max_retries: int = 3,
) -> AcoustIDResult:
    if not api_key:
        return AcoustIDResult(status="error", error="缺少 AcoustID API Key")

    payload = {
        "client": api_key,
        "duration": str(duration_sec),
        "fingerprint": fingerprint,
        "meta": "recordings releasegroups compress",
        "format": "json",
    }
    own_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        for attempt in range(1, max_retries + 1):
            limiter.wait()  # ← 唯一出口，不可绕过
            try:
                resp = http.post(API_URL, data=payload)
            except httpx.HTTPError as exc:
                if attempt >= max_retries:
                    return AcoustIDResult(status="error", error=f"网络错误: {type(exc).__name__}")
                continue
            if resp.status_code in _RETRYABLE and attempt < max_retries:
                log.debug("AcoustID %s，重试 %d/%d", resp.status_code, attempt, max_retries)
                continue
            if resp.status_code != 200:
                message = ""
                try:
                    message = str(resp.json().get("error", {}).get("message", ""))
                except Exception:  # noqa: BLE001
                    message = f"HTTP {resp.status_code}"
                return AcoustIDResult(
                    status="error", error=_sanitize(f"{resp.status_code}: {message}", api_key)
                )

            body = resp.json()
            if body.get("status") != "ok":
                err = body.get("error") or {}
                return AcoustIDResult(
                    status="error",
                    error=_sanitize(f"{err.get('code')}: {err.get('message')}", api_key),
                )
            return _parse(body)
    finally:
        if own_client:
            http.close()
    return AcoustIDResult(status="error", error="重试后仍失败")


def _parse(body: dict) -> AcoustIDResult:
    results = body.get("results") or []
    if not results:
        return AcoustIDResult(status="no_result")
    best = results[0]
    recordings = best.get("recordings") or []
    if not recordings:
        return AcoustIDResult(status="no_result", score=float(best.get("score", 0.0)))
    rec = recordings[0]
    return AcoustIDResult(
        status="ok",
        score=round(float(best.get("score", 0.0)), 3),
        recording_mbid=rec.get("id", ""),
        title=rec.get("title", ""),
        artists=[a.get("name", "") for a in rec.get("artists") or []],
        n_candidates=len(recordings),
    )
