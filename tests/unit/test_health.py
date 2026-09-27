"""联网检查 / 密钥有效性判定测试（全部 mock，不联网）。"""

from __future__ import annotations

import httpx

from mds.adapters import health
from mds.config import Settings


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    def __init__(self, response=None, *, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._response


def patch(monkeypatch, response=None, *, exc=None) -> FakeClient:
    client = FakeClient(response, exc=exc)
    monkeypatch.setattr(health, "_client", lambda _timeout: client)
    return client


# ── AcoustID：两种都是 HTTP 400，必须靠 message 区分 ──────
def test_acoustid_valid_key_returns_invalid_fingerprint(monkeypatch) -> None:
    """有效 Key + 假指纹 → 服务端说 invalid fingerprint —— 这正是"Key 有效"的证据。"""
    patch(monkeypatch, FakeResponse(400, {"error": {"code": 3, "message": "invalid fingerprint"}}))
    check = health.check_acoustid("good-key")
    assert check.ok is True
    assert "Key 有效" in check.detail


def test_acoustid_invalid_key_is_reported_as_key_problem(monkeypatch) -> None:
    patch(monkeypatch, FakeResponse(400, {"error": {"code": 4, "message": "invalid API key"}}))
    check = health.check_acoustid("bad-key")
    assert check.ok is False
    assert "Key 无效" in check.detail
    assert "acoustid.org" in check.hint  # 要告诉他去哪拿


def test_acoustid_missing_key(monkeypatch) -> None:
    patch(monkeypatch, FakeResponse(200, {}))
    check = health.check_acoustid("")
    assert check.ok is False
    assert "没有填" in check.detail


def test_acoustid_network_error(monkeypatch) -> None:
    patch(monkeypatch, exc=httpx.ConnectError("boom"))
    check = health.check_acoustid("good-key")
    assert check.ok is False
    assert "连不上" in check.detail


# ── DeepSeek：用不花钱的 /models ─────────────────────────
def test_deepseek_uses_free_models_endpoint(monkeypatch) -> None:
    client = patch(monkeypatch, FakeResponse(200, {"object": "list"}))
    check = health.check_deepseek("sk-good")
    assert check.ok is True
    url, kwargs = client.calls[0]
    assert url == health.DEEPSEEK_MODELS
    assert kwargs["headers"]["Authorization"] == "Bearer sk-good"


def test_deepseek_invalid_key(monkeypatch) -> None:
    patch(monkeypatch, FakeResponse(401, {"error": {"message": "Authentication Fails"}}))
    check = health.check_deepseek("sk-bad")
    assert check.ok is False
    assert "Key 无效" in check.detail


def test_deepseek_missing_key_is_not_a_failure(monkeypatch) -> None:
    """没填 DeepSeek Key 不算失败 —— 没有 AI 也能跑扫描与候选。"""
    patch(monkeypatch, FakeResponse(200, {}))
    check = health.check_deepseek("")
    assert check.ok is True
    assert "不影响" in check.detail


# ── MusicBrainz：看 User-Agent ───────────────────────────
def test_musicbrainz_sends_user_agent(monkeypatch) -> None:
    client = patch(monkeypatch, FakeResponse(200, {}))
    check = health.check_musicbrainz("mds/1.0 ( mailto:me@example.com )")
    assert check.ok is True
    _, kwargs = client.calls[0]
    assert kwargs["headers"]["User-Agent"] == "mds/1.0 ( mailto:me@example.com )"


def test_musicbrainz_without_user_agent(monkeypatch) -> None:
    patch(monkeypatch, FakeResponse(200, {}))
    check = health.check_musicbrainz("")
    assert check.ok is False
    assert "没填邮箱" in check.detail


def test_musicbrainz_rejected(monkeypatch) -> None:
    patch(monkeypatch, FakeResponse(503, {}))
    check = health.check_musicbrainz("mds/1.0 ( mailto:me@example.com )")
    assert check.ok is False


# ── 一把测三个 ───────────────────────────────────────────
def test_check_services_runs_all_three(monkeypatch) -> None:
    patch(monkeypatch, FakeResponse(200, {}))
    settings = Settings(
        acoustid_api_key="k",
        deepseek_api_key="k",
        musicbrainz_user_agent="mds/1.0 ( mailto:me@example.com )",
    )
    checks = health.check_services(settings)
    assert [c.name for c in checks] == ["AcoustID", "MusicBrainz", "DeepSeek"]


def test_error_message_tolerates_non_json() -> None:
    assert health._error_message(FakeResponse(500, None, text="<html>oops</html>")) == ""
