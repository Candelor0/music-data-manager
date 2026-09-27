"""MusicBrainz 客户端（recording → 发行版候选。

**R6（POC 实测教训）**：必须带 `inc=media`。漏掉时 `releases[].media` 为 None，
候选里就没有时长和曲目数，而时长是消歧最强的一条证据
（实测：漏掉 → 0/25 候选有时长；加上 → 25/25 有时长）。

限流：官方要求 1 次/秒，所有请求必须经过传入的 RateLimiter。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..core.models import Candidate
from ..logging_setup import get_logger
from .ratelimit import RateLimiter

log = get_logger("musicbrainz")

API_BASE = "https://musicbrainz.org/ws/2"
# ⚠️ 这个 inc 组合不能删减（R6）
INC = "releases+release-groups+artist-credits+media"
_RETRYABLE = {429, 500, 502, 503, 504}


@dataclass
class MBCandidates:
    status: str  # ok | no_result | error
    track_title: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    error: str = ""


def _extract_tracks(medium: dict) -> list[dict]:
    """MusicBrainz 在 recording 查询里用单数键 `track`（值为数组），也可能遇到复数 `tracks`。"""
    raw = medium.get("track")
    if raw is None:
        raw = medium.get("tracks")
    if isinstance(raw, dict):
        return [raw]
    return [t for t in (raw or []) if isinstance(t, dict)]


def _build_candidates(releases: list[dict]) -> list[Candidate]:
    out: list[Candidate] = []
    for rel in releases:
        media = rel.get("media") or []
        group = rel.get("release-group") or {}

        track_len: int | None = None
        track_number = ""
        fmt = ""
        for medium in media:
            fmt = fmt or (medium.get("format") or "")
            for track in _extract_tracks(medium):
                if not track_number and track.get("number"):
                    track_number = str(track.get("number"))
                if track.get("length"):
                    track_len = int(track["length"])
                    break
            if track_len:
                break

        labels = [
            (li.get("label") or {}).get("name", "") for li in rel.get("label-info") or []
        ]
        out.append(
            Candidate(
                release_mbid=rel.get("id", ""),
                album=rel.get("title", ""),
                date=rel.get("date") or group.get("first-release-date") or "",
                country=rel.get("country") or "",
                status=rel.get("status") or "",
                format=fmt,
                track_count=sum(int(m.get("track-count") or 0) for m in media),
                track_number=track_number,
                # 存原始时长；与文件的差值必须在使用时实时算（候选会跨文件复用缓存）
                track_length_sec=round(track_len / 1000) if track_len else None,
                release_group=group.get("title", "") or "",
                rg_type=group.get("primary-type", "") or "",
                rg_secondary=", ".join(group.get("secondary-types") or []),
                label=labels[0] if labels else "",
            )
        )
    return out


def fetch_candidates(
    recording_mbid: str,
    user_agent: str,
    limiter: RateLimiter,
    file_duration_sec: int = 0,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> MBCandidates:
    if not recording_mbid:
        return MBCandidates(status="error", error="缺少 recording MBID")
    if not user_agent:
        return MBCandidates(status="error", error="缺少 MusicBrainz User-Agent")

    url = f"{API_BASE}/recording/{recording_mbid}"
    params = {"inc": INC, "fmt": "json"}
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    own_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        for attempt in range(1, max_retries + 1):
            limiter.wait()  # ← 唯一出口
            try:
                resp = http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= max_retries:
                    return MBCandidates(status="error", error=f"网络错误: {type(exc).__name__}")
                continue
            if resp.status_code in _RETRYABLE and attempt < max_retries:
                log.debug("MusicBrainz %s，重试 %d/%d", resp.status_code, attempt, max_retries)
                continue
            if resp.status_code != 200:
                return MBCandidates(status="error", error=f"HTTP {resp.status_code}")

            body = resp.json()
            releases = body.get("releases") or []
            candidates = _build_candidates(releases)
            if not candidates:
                return MBCandidates(status="no_result", track_title=body.get("title", ""))

            # 按时长吻合优先排序（给模型一个好顺序，但不暗示答案；不按类型加权 —— R5）
            candidates.sort(
                key=lambda c: (
                    c.length_delta(file_duration_sec)
                    if c.length_delta(file_duration_sec) is not None
                    else 9999,
                    c.date,
                )
            )
            return MBCandidates(
                status="ok", track_title=body.get("title", ""), candidates=candidates
            )
    finally:
        if own_client:
            http.close()
    return MBCandidates(status="error", error="重试后仍失败")
