"""适配层测试：全部用假客户端，不联网、不花钱。

覆盖关键错误路径：超时、429、坏 JSON、坏密钥、缺字段、越界下标。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mds.adapters import acoustid, llm, musicbrainz
from mds.adapters.llm import CostTracker, is_peak, parse_suggestion
from mds.adapters.ratelimit import RateLimiter
from mds.core.models import Candidate, Tags


class FakeResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data or {}
        self.text = text or "..."  # 不把内容泄露到断言里

    def json(self) -> dict:
        return self._data


class FakeClient:
    def __init__(self, responses: list[FakeResponse] | None = None,
                 exc: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._exc = exc
        self.requests: list[dict] = []

    def _next(self):
        if self._exc is not None:
            raise self._exc
        return self._responses.pop(0)

    def post(self, url, **kwargs):
        self.requests.append({"method": "POST", "url": url, "kwargs": kwargs})
        return self._next()

    def get(self, url, **kwargs):
        self.requests.append({"method": "GET", "url": url, "kwargs": kwargs})
        return self._next()

    def close(self) -> None:
        pass


FAST = RateLimiter(10000.0, "test")


# ── 限流器 ────────────────────────────────────────────────
def test_rate_limiter_enforces_min_interval() -> None:
    import time

    limiter = RateLimiter(50.0, "test")  # 20ms 间隔
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.059, "4 次调用至少应有 3 个间隔"
    assert limiter.calls == 4


def test_rate_limiter_rejects_bad_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


# ── AcoustID ─────────────────────────────────────────────
def _acoustid_body() -> dict:
    return {
        "status": "ok",
        "results": [
            {"id": "x", "score": 0.98,
             "recordings": [{"id": "rec-1", "title": "T", "artists": [{"name": "A"}]}]}
        ],
    }


def test_acoustid_ok() -> None:
    client = FakeClient([FakeResponse(200, _acoustid_body())])
    result = acoustid.lookup("key", "fp", 200, FAST, client=client)
    assert result.status == "ok"
    assert result.recording_mbid == "rec-1"
    assert result.artists == ["A"]
    # 必须带 format=json，否则服务端可能返回 XML
    assert client.requests[0]["kwargs"]["data"]["format"] == "json"


def test_acoustid_no_result() -> None:
    client = FakeClient([FakeResponse(200, {"status": "ok", "results": []})])
    assert acoustid.lookup("key", "fp", 200, FAST, client=client).status == "no_result"


def test_acoustid_invalid_key_does_not_leak_key() -> None:
    body = {"status": "error", "error": {"code": 4, "message": "invalid API key"}}
    client = FakeClient([FakeResponse(400, body)])
    result = acoustid.lookup("SUPERSECRET", "fp", 200, FAST, client=client)
    assert result.status == "error"
    assert "invalid API key" in result.error
    assert "SUPERSECRET" not in result.error


def test_acoustid_retries_on_429() -> None:
    client = FakeClient([FakeResponse(429, {}), FakeResponse(200, _acoustid_body())])
    result = acoustid.lookup("key", "fp", 200, FAST, client=client, max_retries=3)
    assert result.status == "ok"
    assert len(client.requests) == 2


def test_acoustid_missing_key_short_circuits() -> None:
    client = FakeClient([])
    result = acoustid.lookup("", "fp", 200, FAST, client=client)
    assert result.status == "error"
    assert not client.requests


def test_acoustid_network_error_is_wrapped() -> None:
    import httpx

    client = FakeClient(exc=httpx.ConnectError("boom"))
    result = acoustid.lookup("key", "fp", 200, FAST, client=client, max_retries=2)
    assert result.status == "error"
    assert "网络错误" in result.error


# ── MusicBrainz ──────────────────────────────────────────
def _mb_body(*, media: bool = True) -> dict:
    medium = {
        "format": "CD",
        "track-count": 5,
        "track": [{"number": "3", "title": "T", "length": 275000}],
    }
    if not media:
        medium = {"format": "CD", "track-count": 5}
    rel = {
        "id": "rel-1",
        "title": "Album X",
        "date": "2020-12-16",
        "country": "JP",
        "status": "Official",
        "media": [medium],
        "release-group": {"title": "Album X", "primary-type": "EP"},
        "label-info": [{"label": {"name": "Label"}}],
    }
    return {"id": "rec-1", "title": "T", "releases": [rel]}


def test_musicbrainz_requests_media_inc() -> None:
    """R6：inc 里少了 media 就永远拿不到时长（POC 踩过的坑）。"""
    client = FakeClient([FakeResponse(200, _mb_body())])
    musicbrainz.fetch_candidates("rec-1", "mds/0.1 ( mailto:a@b.c )", FAST, 275, client=client)
    inc = client.requests[0]["kwargs"]["params"]["inc"]
    assert "media" in inc
    # User-Agent 必须带上去（MusicBrainz 强制要求）
    assert "User-Agent" in client.requests[0]["kwargs"]["headers"]


def test_musicbrainz_extracts_length_and_track_count() -> None:
    client = FakeClient([FakeResponse(200, _mb_body())])
    result = musicbrainz.fetch_candidates("rec-1", "ua", FAST, 275, client=client)
    assert result.status == "ok"
    cand = result.candidates[0]
    assert cand.track_length_sec == 275
    assert cand.track_number == "3"
    assert cand.track_count == 5
    assert cand.label == "Label"


def test_musicbrainz_without_media_has_no_length() -> None:
    """反证：没有 media 时候选里就没有时长 —— 这正是当初掉 4 个点的原因。"""
    client = FakeClient([FakeResponse(200, _mb_body(media=False))])
    result = musicbrainz.fetch_candidates("rec-1", "ua", FAST, 275, client=client)
    assert result.candidates[0].track_length_sec is None


def test_musicbrainz_requires_user_agent() -> None:
    client = FakeClient([])
    result = musicbrainz.fetch_candidates("rec-1", "", FAST, 275, client=client)
    assert result.status == "error"
    assert not client.requests


def test_musicbrainz_http_error() -> None:
    client = FakeClient([FakeResponse(500, {}), FakeResponse(500, {}), FakeResponse(500, {})])
    result = musicbrainz.fetch_candidates("rec-1", "ua", FAST, 275, client=client, max_retries=3)
    assert result.status == "error"


# ── LLM：提示词与校验 ─────────────────────────────────────
def test_prompt_full_contains_all_tags() -> None:
    prompt = llm.build_user_prompt("a.flac", Tags(title="T", artist="A", album="AL", date="1999"),
                                  200, [Candidate(album="X", track_length_sec=200)], "full")
    assert "T" in prompt and "A" in prompt and "AL" in prompt and "1999" in prompt


def test_prompt_no_album_hides_album() -> None:
    prompt = llm.build_user_prompt("a.flac", Tags(title="T", artist="A", album="SECRET"),
                                  200, [Candidate(album="X", track_length_sec=200)], "no_album")
    assert "SECRET" not in prompt
    assert "没有专辑标签" in prompt


def test_prompt_none_hides_everything() -> None:
    prompt = llm.build_user_prompt("a.flac", Tags(title="T", artist="A"), 200,
                                   [Candidate(album="X")], "none")
    assert "现有标签" not in prompt


def test_prompt_shows_length_delta_per_file() -> None:
    """时长差必须针对当前文件实时算（候选是跨文件复用的）。"""
    cands = [Candidate(album="X", track_length_sec=205)]
    p1 = llm.build_user_prompt("a.flac", Tags(), 205, cands, "none")
    p2 = llm.build_user_prompt("a.flac", Tags(), 999, cands, "none")
    assert "差 0s" in p1
    assert "差 0s" not in p2


@pytest.mark.parametrize("content", ["not json", "[]", '{"chosen_index":"x"}', '{}'])
def test_parse_suggestion_rejects_bad_payloads(content: str) -> None:
    suggestion, error = parse_suggestion(content, 3)
    assert suggestion is None and error


def test_parse_suggestion_rejects_out_of_range_index() -> None:
    suggestion, error = parse_suggestion('{"chosen_index": 9}', 3)
    assert suggestion is None
    assert "out_of_range" in error


def test_parse_suggestion_accepts_minus_one() -> None:
    suggestion, error = parse_suggestion('{"chosen_index": -1, "confidence": 0.2}', 3)
    assert error == ""
    assert suggestion is not None and suggestion.chosen_index == -1


def test_parse_suggestion_clamps_and_truncates() -> None:
    suggestion, _ = parse_suggestion(
        '{"chosen_index":0,"confidence":5,"reason":"' + "x" * 5000 + '",'
        '"cleaned":{"title":"' + "y" * 5000 + '"}}',
        2,
    )
    assert suggestion is not None
    assert suggestion.confidence == 1.0
    assert len(suggestion.reason) <= 300
    assert len(suggestion.cleaned.title) <= 300


def test_parse_suggestion_tolerates_bad_confidence() -> None:
    suggestion, _ = parse_suggestion('{"chosen_index":0,"confidence":"high"}', 2)
    assert suggestion is not None and suggestion.confidence is None


# ── 成本 ────────────────────────────────────────────────
def test_peak_detection() -> None:
    # 2026-09-22 是周二
    assert is_peak(datetime(2026, 9, 22, 2, 0, tzinfo=UTC)) is True
    assert is_peak(datetime(2026, 9, 22, 5, 0, tzinfo=UTC)) is False
    # 周日全谷时
    assert is_peak(datetime(2026, 9, 27, 2, 0, tzinfo=UTC)) is False


def test_offpeak_is_half_of_peak() -> None:
    args = (1_000_000, 0, 1_000_000)
    assert llm.cost_usd(*args, peak=False) * 2 == pytest.approx(llm.cost_usd(*args, peak=True))


def test_cost_tracker_blocks_over_budget() -> None:
    tracker = CostTracker(0.5)  # 每 100 首 ¥0.5
    assert tracker.allowed(100)
    tracker.add(1.0)  # 一次就超
    assert not tracker.allowed(100)
    assert tracker.spent_cny > 0


def test_cost_tracker_zero_budget_means_unlimited() -> None:
    tracker = CostTracker(0)
    tracker.add(999.0)
    assert tracker.allowed(1)
