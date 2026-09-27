"""DeepSeek 客户端：候选消歧 + 标签清洗。

关键实现决定（POC 实测，见）：
- 模型 `deepseek-flash`；**关闭思考模式**（输出 token 36→11、耗时 0.9s→0.4s）；
- JSON 输出 + temperature 0（判别任务要可复现）；
- **成本累加器**：达预算上限立即停止云端调用；
- 输出必须过 Pydantic 校验；校验失败**重试一次**，仍失败则标 schema_error（R8）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from ..core.models import Candidate, CleanedTags, ItemView, Suggestion, Tags
from ..logging_setup import get_logger
from .ratelimit import RateLimiter

log = get_logger("llm")

API_URL = "https://api.deepseek.com/chat/completions"

# DeepSeek 官方价目（美元 / 百万 tokens），来源：
# https://api-docs.deepseek.com/quick_start/pricing  （deepseek-flash 档，2026-09 核对）
# 峰时：周一至周五 01:00-04:00 与 06:00-10:00 UTC；其余（含周末与中国法定假日）为谷时，半价。
USD_IN_MISS_PEAK, USD_IN_MISS_OFFPEAK = 0.30, 0.15
USD_IN_HIT_PEAK, USD_IN_HIT_OFFPEAK = 0.006, 0.003
USD_OUT_PEAK, USD_OUT_OFFPEAK = 1.20, 0.60
USD_TO_CNY = 7.1  # 估算汇率，实际以账单为准

PEAK_HOURS_UTC = ((1, 4), (6, 10))

SYSTEM_PROMPT = """你是音乐元数据整理专家，任务是从候选发行版中选出与用户本地文件真正对应的那一个。

判断原则（按可信度从高到低）：
1. 曲目时长吻合度：候选里这首歌的时长与文件时长越接近越可信。
   差 5 秒以内很强；差 15 秒以上基本可排除。
2. 专辑名与文件现有专辑标签的一致性（若提供了现有标签，这是次强证据）。
3. 文件名线索：文件名常含曲序、专辑名或单曲名，可作交叉验证。
4. 发行版类型只是弱参考：**不要仅仅因为候选是合集/原声带/再版就排除它**
   ——用户的文件很可能正是从某张合集里抓的。
5. 地区与年份：与文件现有信息吻合的优先。

若证据不足以确定，必须降低 confidence 并在 reason 里说明缺什么证据，不要硬猜。

只输出 JSON，不要任何额外文字。格式：
{"chosen_index": <int, 从 0 开始, 无法确定填 -1>,
 "confidence": <0~1>,
 "reason": "<不超过 80 字的中文理由>",
 "cleaned": {"title": "...", "artist": "...", "album": "...", "date": "...", "genre": "..."}}"""

# 给模型的证据级别（对应报告与测试里的三种轮次）
PromptMode = str  # "full" | "no_album" | "none"


def is_peak(now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if now.weekday() >= 5:  # 周末全谷时
        return False
    hour = now.hour
    return any(start <= hour < end for start, end in PEAK_HOURS_UTC)


def cost_usd(in_miss: int, in_hit: int, out: int, *, peak: bool) -> float:
    if peak:
        return (
            in_miss / 1e6 * USD_IN_MISS_PEAK
            + in_hit / 1e6 * USD_IN_HIT_PEAK
            + out / 1e6 * USD_OUT_PEAK
        )
    return (
        in_miss / 1e6 * USD_IN_MISS_OFFPEAK
        + in_hit / 1e6 * USD_IN_HIT_OFFPEAK
        + out / 1e6 * USD_OUT_OFFPEAK
    )


def build_user_prompt(
    name: str,
    tags: Tags,
    duration_sec: int,
    candidates: list[Candidate],
    mode: PromptMode = "full",
) -> str:
    """组装用户提示。

    mode="full"     给全部现有标签
    mode="no_album" 只给曲名/艺术家，隐藏专辑（最接近"缺专辑标签"的真实场景）
    mode="none"     完全不给标签（只有文件名与时长）
    """
    lines = ["# 待整理的本地文件", f"文件名：{name}", f"时长：{duration_sec} 秒"]
    if mode == "full":
        lines += [
            f"现有标签-曲名：{tags.value_of('title')}",
            f"现有标签-艺术家：{tags.value_of('artist')}",
            f"现有标签-专辑：{tags.value_of('album')}",
            f"现有标签-年份：{tags.value_of('date')}",
        ]
    elif mode == "no_album":
        lines += [
            f"现有标签-曲名：{tags.value_of('title')}",
            f"现有标签-艺术家：{tags.value_of('artist')}",
            "（该文件没有专辑标签，需要你推断它出自哪个发行版）",
        ]
    else:
        lines.append("（该文件没有可用标签，只有文件名与时长）")

    lines += ["", f"# 候选发行版（共 {len(candidates)} 个）"]
    for index, cand in enumerate(candidates):
        delta = cand.length_delta(duration_sec)
        delta_text = f"{delta}s" if delta is not None else "未知"
        rg_tail = f"/{cand.rg_secondary}" if cand.rg_secondary else ""
        lines.append(
            f"[{index}] 专辑《{cand.album}》| 日期 {cand.date or '未知'} | 国家 {cand.country or '未知'}"
            f" | 状态 {cand.status or '未知'} | 类型 {cand.rg_type or '未知'}{rg_tail}"
            f" | 格式 {cand.format or '未知'} | 曲目数 {cand.track_count}"
            f" | 本曲时长 {cand.track_length_sec if cand.track_length_sec is not None else '未知'}"
            f"（与文件差 {delta_text}） | 厂牌 {cand.label or '未知'}"
        )
    lines += ["", "请从上述候选中选出最可能对应的那一个，按规定 JSON 格式输出。"]
    return "\n".join(lines)


def parse_suggestion(content: str, n_candidates: int) -> tuple[Suggestion | None, str]:
    """校验模型输出（R8）。返回 (Suggestion, "") 或 (None, 错误说明)。"""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(obj, dict):
        return None, "not_object"

    index = obj.get("chosen_index")
    if not isinstance(index, int):
        return None, "chosen_index_not_int"
    if not (-1 <= index < n_candidates):
        return None, f"chosen_index_out_of_range({index})"

    confidence = obj.get("confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        confidence = None

    cleaned = obj.get("cleaned") if isinstance(obj.get("cleaned"), dict) else {}
    try:
        return (
            Suggestion(
                chosen_index=index,
                confidence=confidence,
                reason=str(obj.get("reason", "")),
                cleaned=CleanedTags(
                    title=str(cleaned.get("title", "") or ""),
                    artist=str(cleaned.get("artist", "") or ""),
                    album=str(cleaned.get("album", "") or ""),
                    date=str(cleaned.get("date", "") or ""),
                    genre=str(cleaned.get("genre", "") or ""),
                ),
            ),
            "",
        )
    except ValidationError:
        return None, "schema_validation_failed"


@dataclass
class LLMResult:
    status: str  # ok | schema_error | budget_stopped | error
    suggestion: Suggestion | None = None
    raw: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    error: str = ""


class CostTracker:
    """预算熔断。按"每 100 首"上限线性折算。"""

    def __init__(self, budget_per_100_tracks: float) -> None:
        self.budget_per_100 = budget_per_100_tracks
        self.spent_usd = 0.0
        self.calls = 0

    @property
    def spent_cny(self) -> float:
        return self.spent_usd * USD_TO_CNY

    def allowed(self, processed: int) -> bool:
        if self.budget_per_100 <= 0:
            return True
        allowed_usd = self.budget_per_100 / USD_TO_CNY * max(processed, 1) / 100.0
        return self.spent_usd < allowed_usd

    def add(self, usd: float) -> None:
        self.spent_usd += usd
        self.calls += 1


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        tracker: CostTracker,
        limiter: RateLimiter | None = None,
        client: httpx.Client | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.tracker = tracker
        self.limiter = limiter or RateLimiter(1000.0, "llm")
        self.timeout = timeout
        self._own_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> DeepSeekClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def chat(
        self, item: ItemView, candidates: list[Candidate], *, mode: PromptMode = "full",
        retry_on_schema_error: bool = True,
    ) -> LLMResult:
        user_prompt = build_user_prompt(item.name, item.tags, item.duration_sec, candidates, mode)
        result = self._request(user_prompt, n_candidates=len(candidates))

        if result.status == "schema_error" and retry_on_schema_error:
            log.debug("模型输出不合规（%s），重试一次", result.error)
            repaired = (
                user_prompt
                + f"\n\n注意：上次输出不合规（{result.error}）。"
                + f"chosen_index 必须是 -1 到 {len(candidates) - 1} 之间的整数，"
                + "confidence 必须是 0 到 1 的数字，且只输出 JSON。"
            )
            result = self._request(repaired, n_candidates=len(candidates))
        return result

    def _request(self, user_prompt: str, *, n_candidates: int) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 600,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        self.limiter.wait()
        started = datetime.now(UTC)
        try:
            resp = self._client.post(API_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            return LLMResult(status="error", error=f"网络错误: {type(exc).__name__}")

        if resp.status_code != 200:
            detail = resp.text[:160].replace(self.api_key, "***")
            return LLMResult(status="error", error=f"HTTP {resp.status_code}: {detail}")

        body = resp.json()
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cache_hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        cache_miss = int(usage.get("prompt_cache_miss_tokens", prompt_tokens) or 0)
        usd = cost_usd(cache_miss, cache_hit, completion_tokens, peak=is_peak(started))
        self.tracker.add(usd)

        content = str((body.get("choices") or [{}])[0].get("message", {}).get("content", ""))
        suggestion, error = parse_suggestion(content, n_candidates)

        base = dict(
            raw=content,
            prompt_tokens=prompt_tokens,
            cache_hit_tokens=cache_hit,
            completion_tokens=completion_tokens,
            cost_usd=usd,
            model=str(body.get("model", self.model)),
        )
        if suggestion is None:
            return LLMResult(status="schema_error", error=error, **base)
        return LLMResult(status="ok", suggestion=suggestion, **base)
