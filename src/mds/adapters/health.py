"""联网连通性与**密钥有效性**检查。

给两处共用：命令行 `mds doctor` 与界面设置页的「测试连接」按钮。

为什么不能用"能不能连上"代替"Key 对不对"（实测数据，2026-09-22）：

    AcoustID  有效 Key + 假指纹  → HTTP 400 `{"error": {"code": 3, "message": "invalid fingerprint"}}`
    AcoustID  无效 Key          → HTTP 400 `{"error": {"code": 4, "message": "invalid API key"}}`
    DeepSeek  GET /models 有效   → HTTP 200（**免费**，不产生 token 费用）
    DeepSeek  GET /models 无效   → HTTP 401

两者都是 HTTP 400/401，光看状态码分不出"Key 填错了"还是"服务抽风"。
所以必须读响应体里的 `message`，才能给用户一句准确的话。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings

ACOUSTID_LOOKUP = "https://api.acoustid.org/v2/lookup"
MUSICBRAINZ_PING = (
    "https://musicbrainz.org/ws/2/artist/5b11f4ce-a62d-471e-81fc-a69a8278c7da?fmt=json"
)
DEEPSEEK_MODELS = "https://api.deepseek.com/models"

#: 服务名 → 去哪里拿 Key（界面上的提示语）
KEY_HINTS = {
    "AcoustID": "注册（要「应用」key，不是账号 key）：https://acoustid.org/new-application",
    "MusicBrainz": "只需填一个能联系到你的邮箱，不用注册",
    "DeepSeek": "注册：https://platform.deepseek.com",
}


@dataclass
class ServiceCheck:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""

    def line(self) -> str:
        return f"[{'✅' if self.ok else '❌'}] {self.name}：{self.detail}"


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def check_acoustid(api_key: str, *, timeout: float = 15.0) -> ServiceCheck:
    """查一次 AcoustID（用假指纹）。

    Key 有效时服务端会说 `invalid fingerprint`；Key 不对时会说 `invalid API key`。
    两种都是 HTTP 400，所以必须读 message 才能给出准确提示。
    """
    if not api_key:
        return ServiceCheck("AcoustID", False, "没有填 Key", KEY_HINTS["AcoustID"])
    params = {"client": api_key, "duration": "1", "fingerprint": "AQAAAA"}
    try:
        with _client(timeout) as client:
            resp = client.get(ACOUSTID_LOOKUP, params=params)
    except httpx.HTTPError as exc:
        return ServiceCheck("AcoustID", False, f"连不上（{type(exc).__name__}）", "检查网络后重试")

    message = _error_message(resp)
    if "api key" in message.lower():
        return ServiceCheck("AcoustID", False, "Key 无效（服务端说 invalid API key）", KEY_HINTS["AcoustID"])
    if resp.status_code in (200, 400):
        # 400 + 非"key 无效" = Key 本身是对的（我们发的是假指纹，本来就该被拒）
        return ServiceCheck("AcoustID", True, f"通（HTTP {resp.status_code}，Key 有效）")
    return ServiceCheck("AcoustID", False, f"HTTP {resp.status_code}", KEY_HINTS["AcoustID"])


def check_musicbrainz(user_agent: str, *, timeout: float = 15.0) -> ServiceCheck:
    """MusicBrainz 主要看 User-Agent 是否规范（它靠这个识别调用方）。"""
    if not user_agent:
        return ServiceCheck("MusicBrainz", False, "没填邮箱", KEY_HINTS["MusicBrainz"])
    try:
        with _client(timeout) as client:
            resp = client.get(MUSICBRAINZ_PING, headers={"User-Agent": user_agent})
    except httpx.HTTPError as exc:
        return ServiceCheck("MusicBrainz", False, f"连不上（{type(exc).__name__}）", "检查网络后重试")

    if resp.status_code == 200:
        return ServiceCheck("MusicBrainz", True, "通（HTTP 200）")
    if resp.status_code in (403, 503):
        return ServiceCheck("MusicBrainz", False, f"HTTP {resp.status_code}", KEY_HINTS["MusicBrainz"])
    return ServiceCheck("MusicBrainz", False, f"HTTP {resp.status_code}", "稍后重试")


def check_deepseek(api_key: str, *, timeout: float = 15.0) -> ServiceCheck:
    """用 `GET /models` 验 Key —— 这个接口**不产生 token 费用**。"""
    if not api_key:
        return ServiceCheck("DeepSeek", True, "没填（不影响扫描与候选，只影响 AI 消歧）")
    try:
        with _client(timeout) as client:
            resp = client.get(DEEPSEEK_MODELS, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        return ServiceCheck("DeepSeek", False, f"连不上（{type(exc).__name__}）", "检查网络后重试")

    if resp.status_code == 200:
        return ServiceCheck("DeepSeek", True, "通（HTTP 200，Key 有效）")
    if resp.status_code == 401:
        return ServiceCheck("DeepSeek", False, "Key 无效（HTTP 401）", KEY_HINTS["DeepSeek"])
    return ServiceCheck("DeepSeek", False, f"HTTP {resp.status_code}", "稍后重试")


def _error_message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        if isinstance(error, str):
            return error
        return str(payload.get("message") or "")
    return ""


def check_services(settings: Settings, *, timeout: float = 15.0) -> list[ServiceCheck]:
    """三个服务一起测。每个只请求一次，不做压力测试。"""
    return [
        check_acoustid(settings.acoustid_api_key, timeout=timeout),
        check_musicbrainz(settings.musicbrainz_user_agent, timeout=timeout),
        check_deepseek(settings.deepseek_api_key, timeout=timeout),
    ]
