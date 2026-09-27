"""分析编排：指纹 → AcoustID → MusicBrainz → DeepSeek → 决策。

**本版本只读**：全程不修改任何用户文件。

续跑语义（S6）：每个 item 的每一步都即时写库；重跑只处理未完成的步骤，
已完成的（含 error）不重复处理，除非显式 `retry_errors=True`。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..adapters import acoustid, fpcalc, musicbrainz
from ..adapters.llm import (
    CostTracker,
    DeepSeekClient,
    LLMResult,
    build_user_prompt,
    parse_suggestion,
)
from ..adapters.ratelimit import RateLimiter
from ..config import Settings
from ..core.decide import decide, verdict_of
from ..core.models import Candidate, Suggestion
from ..logging_setup import get_logger
from ..storage.db import Database, prompt_hash
from .common import item_view as _item_view
from .common import load_json as _load
from .group import groups_from_rows

log = get_logger("analyze")

ProgressFn = Callable[[str], None]


@dataclass
class AnalyzeOptions:
    mode: str = "full"  # full | no_album | none
    use_llm: bool = True
    retry_errors: bool = False
    refresh_decisions: bool = False
    # 仅用于回归验证：忽略文件里已有的专辑标签，模拟「标签缺失的音乐库」。
    # 只影响传给决策层的视图，不修改数据库里读到的真实标签，也不改任何文件。
    assume_no_album_tag: bool = False
    #: 多碟专辑：把 album 里的 "Disc N" 拆到 discnumber（可通过 --no-strip-disc 关闭）
    strip_disc: bool = True
    max_candidates: int = 10
    low_evidence_candidates: int = 10
    budget_per_100_tracks: float = 0.5
    fingerprint_length: int = 120


@dataclass
class AnalyzeStats:
    total: int = 0
    processed: int = 0
    fp_ok: int = 0
    ac_ok: int = 0
    ac_no_result: int = 0
    cand_ok: int = 0
    llm_ok: int = 0
    llm_schema_error: int = 0
    llm_skipped: int = 0
    errors: int = 0
    skipped_done: int = 0
    llm_cache_hits: int = 0
    mb_cache_hits: int = 0
    fp_cache_hits: int = 0
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms_total: int = 0
    elapsed_sec: float = 0.0
    verdicts: dict[str, int] = field(default_factory=dict)


def _needs(status: str, *, retry_errors: bool) -> bool:
    if status == "pending":
        return True
    return retry_errors and status in ("error", "schema_error")


def analyze(
    db: Database,
    settings: Settings,
    run_id: str,
    *,
    options: AnalyzeOptions | None = None,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AnalyzeStats:
    opts = options or AnalyzeOptions()
    say = progress or (lambda _msg: None)

    run = db.get_run(run_id)
    if run is None:
        raise ValueError(f"找不到 run：{run_id}")

    ac_limiter = RateLimiter(settings.acoustid_rate, "acoustid")
    mb_limiter = RateLimiter(settings.musicbrainz_rate, "musicbrainz")
    tracker = CostTracker(opts.budget_per_100_tracks)

    stats = AnalyzeStats()
    # 统一转成 dict：sqlite3.Row 不支持 .get()，混用会在某些分支上炸（实测踩过）
    rows = [dict(r) for r in db.iter_items(run_id)]
    stats.total = len(rows)
    started = time.monotonic()

    # 先按专辑目录成组，决策时就能用「同目录共识」补空字段。
    # 这步完全本地：不联网、不改文件、不花钱。
    groups_by_path = groups_from_rows(rows)
    if groups_by_path:
        say(f"已按专辑目录分组：{len(groups_by_path)} 个目录")

    llm_client: DeepSeekClient | None = None
    if opts.use_llm and settings.has_deepseek:
        llm_client = DeepSeekClient(
            settings.deepseek_api_key, model=settings.model, tracker=tracker
        )
    elif opts.use_llm:
        say("⚠️ 未配置 DeepSeek Key：跳过 AI 消歧，只做到候选（结果会标为未验证）")

    budget_exhausted = False
    try:
        for index, row in enumerate(rows, 1):
            if should_stop is not None and should_stop():
                say("已请求停止，保存进度后退出。")
                break

            view = _item_view(row, assume_no_album_tag=opts.assume_no_album_tag)
            label = f"[{index}/{stats.total}] {row['name'][:52]}"

            # 续跑：已出决策且各阶段无待办的条目直接跳过（不重算、不重花钱）
            # 注意：llm_status='skipped'（无 Key 或超预算）在启用云端时应该再给一次机会，
            # 否则 --no-llm 跑过的库永远无法补上 AI 消歧。
            llm_pending = _needs(row["llm_status"], retry_errors=opts.retry_errors) or (
                opts.use_llm and row["llm_status"] == "skipped"
            )
            pending = (
                _needs(row["fp_status"], retry_errors=opts.retry_errors)
                or _needs(row["ac_status"], retry_errors=opts.retry_errors)
                or _needs(row["cand_status"], retry_errors=opts.retry_errors)
                or llm_pending
            )
            if row["decision_json"] and not pending and not opts.refresh_decisions:
                stats.skipped_done += 1
                continue

            # ── 1. 指纹 ─────────────────────────────────────
            if _needs(row["fp_status"], retry_errors=opts.retry_errors):
                cached = db.fingerprint_get(row["path"], int(row["size"] or 0), float(row["mtime"] or 0.0))
                if cached:
                    stats.fp_cache_hits += 1
                    fp = fpcalc.Fingerprint(
                        status="ok",
                        fingerprint=cached["fingerprint"],
                        duration_sec=int(cached["duration_sec"] or 0),
                    )
                else:
                    fp = fpcalc.compute(row["path"], length=opts.fingerprint_length)

                if fp.status != "ok":
                    db.update_item(row["id"], fp_status="error", fp_error=fp.error, error=fp.error)
                    stats.errors += 1
                    say(f"  {label}  ✗ 指纹失败：{fp.error}")
                    continue
                db.fingerprint_put(row["path"], int(row["size"] or 0), float(row["mtime"] or 0.0),
                                   fp.fingerprint, fp.duration_sec)
                db.update_item(
                    row["id"], fp_status="ok", fp_error=None, duration_sec=fp.duration_sec
                )
                row = {
                    **row,
                    "fp_status": "ok",
                    "duration_sec": fp.duration_sec,
                }
                view = _item_view(row, assume_no_album_tag=opts.assume_no_album_tag)
                stats.fp_ok += 1

            fingerprint = (db.fingerprint_get(row["path"], int(row["size"] or 0),
                                              float(row["mtime"] or 0.0)) or {}).get("fingerprint", "")
            if not fingerprint:
                say(f"  {label}  ✗ 找不到指纹（跳过）")
                continue

            # ── 2. AcoustID ────────────────────────────────
            if _needs(row["ac_status"], retry_errors=opts.retry_errors):
                if not settings.has_acoustid:
                    db.update_item(row["id"], ac_status="error", ac_error="未配置 AcoustID Key")
                    stats.errors += 1
                    say(f"  {label}  ✗ 未配置 AcoustID Key")
                    continue
                result = acoustid.lookup(
                    settings.acoustid_api_key, fingerprint, int(row["duration_sec"] or 0), ac_limiter
                )
                if result.status == "ok":
                    db.update_item(
                        row["id"], ac_status="ok", ac_score=result.score,
                        ac_title=result.title, recording_mbid=result.recording_mbid, ac_error=None,
                    )
                    row = {**row, "ac_status": "ok", "recording_mbid": result.recording_mbid}
                    stats.ac_ok += 1
                elif result.status == "no_result":
                    db.update_item(row["id"], ac_status="no_result", ac_error=None)
                    stats.ac_no_result += 1
                    say(f"  {label}  ⌀ AcoustID 无结果")
                    continue
                else:
                    db.update_item(row["id"], ac_status="error", ac_error=result.error,
                                   error=result.error)
                    stats.errors += 1
                    say(f"  {label}  ✗ AcoustID 失败：{result.error}")
                    continue

            recording_mbid = row.get("recording_mbid") or ""

            # ── 3. MusicBrainz 候选 ────────────────────────
            if _needs(row["cand_status"], retry_errors=opts.retry_errors):
                if not settings.has_user_agent:
                    db.update_item(row["id"], cand_status="error",
                                   cand_error="未配置 MusicBrainz User-Agent")
                    stats.errors += 1
                    say(f"  {label}  ✗ 未配置 MusicBrainz User-Agent")
                    continue
                cached_mb = db.mb_get(recording_mbid) if recording_mbid else None
                if cached_mb:
                    stats.mb_cache_hits += 1
                    payload = cached_mb
                    status = "ok" if payload.get("candidates") else "no_result"
                else:
                    mb = musicbrainz.fetch_candidates(
                        recording_mbid, settings.musicbrainz_user_agent, mb_limiter,
                        int(row["duration_sec"] or 0),
                    )
                    status = mb.status
                    payload = {
                        "track_title": mb.track_title,
                        "candidates": [c.model_dump() for c in mb.candidates],
                        "error": mb.error,
                    }
                    if status == "ok":
                        db.mb_put(recording_mbid, payload)

                if status != "ok":
                    db.update_item(
                        row["id"], cand_status=("no_result" if status == "no_result" else "error"),
                        n_candidates=0,
                        cand_error=None if status == "no_result" else payload.get("error"),
                    )
                    say(f"  {label}  ⌀ 没有候选")
                    if status == "no_result":
                        _finalize_without_candidates(db, row, stats, opts, groups_by_path)
                    continue
                db.update_item(row["id"], cand_status="ok",
                               n_candidates=len(payload["candidates"]), cand_error=None)
                stats.cand_ok += 1

            candidates_raw = (db.mb_get(recording_mbid) or {}).get("candidates", [])
            candidates = [Candidate(**c) for c in candidates_raw][: opts.max_candidates]
            if not candidates:
                _finalize_without_candidates(db, row, stats, opts, groups_by_path)
                say(f"  {label}  ⌀ 没有候选")
                continue

            # ── 4. DeepSeek 消歧 ───────────────────────────
            suggestion = None
            llm_ok = row["llm_status"] == "ok"
            if not opts.use_llm:
                if row["llm_status"] != "skipped":
                    db.update_item(row["id"], llm_status="skipped")
                stats.llm_skipped += 1
            elif llm_pending:
                if budget_exhausted or (llm_client and not tracker.allowed(index - 1)):
                    budget_exhausted = True
                    db.update_item(row["id"], llm_status="skipped")
                    stats.llm_skipped += 1
                    say(f"  {label}  ⏸ 已达预算上限，跳过云端调用")
                elif llm_client is None:
                    db.update_item(row["id"], llm_status="skipped")
                    stats.llm_skipped += 1
                else:
                    user_prompt = build_user_prompt(
                        view.name, view.tags, view.duration_sec, candidates, opts.mode
                    )
                    key = prompt_hash(f"{settings.model}|{opts.mode}|{user_prompt}")
                    cached_llm = db.llm_get(key)
                    if cached_llm:
                        stats.llm_cache_hits += 1
                        parsed, err = parse_suggestion(
                            cached_llm["payload"].get("content", ""), len(candidates)
                        )
                        result = LLMResult(
                            status="ok" if parsed else "schema_error",
                            suggestion=parsed,
                            error=err,
                        )
                    else:
                        result = llm_client.chat(view, candidates, mode=opts.mode)
                        db.llm_put(key, {"content": result.raw}, settings.model)

                    if result.status == "ok":
                        suggestion = result.suggestion
                        db.update_item(
                            row["id"], llm_status="ok",
                            suggestion_json=suggestion.model_dump() if suggestion else None,
                            confidence=suggestion.confidence if suggestion else None,
                            chosen_index=suggestion.chosen_index if suggestion else None,
                            llm_error=None,
                        )
                        if not cached_llm:
                            stats.llm_ok += 1
                    elif result.status == "schema_error":
                        db.update_item(row["id"], llm_status="schema_error",
                                       llm_error=result.error)
                        stats.llm_schema_error += 1
                    else:
                        db.update_item(row["id"], llm_status="error", llm_error=result.error)
                        stats.errors += 1

                    if not cached_llm:
                        stats.cost_usd += result.cost_usd
                        stats.prompt_tokens += result.prompt_tokens
                        stats.completion_tokens += result.completion_tokens
                        stats.latency_ms_total += result.latency_ms
                        db.record_llm_call(
                            run_id=run_id, item_id=row["id"], model=result.model, mode=opts.mode,
                            prompt_tokens=result.prompt_tokens,
                            cache_hit_tokens=result.cache_hit_tokens,
                            completion_tokens=result.completion_tokens,
                            latency_ms=result.latency_ms, cost_usd=result.cost_usd,
                            status=result.status,
                        )
            elif llm_ok and row["suggestion_json"]:
                suggestion = Suggestion(**_load(row["suggestion_json"]))

            # ── 5. 决策 ───────────────────────────────────
            decision = decide(
                view, candidates, suggestion,
                low_evidence_candidates=opts.low_evidence_candidates,
                strip_disc=opts.strip_disc,
                group=_group_of(view.path, groups_by_path),
            )
            verdict = verdict_of(decision)
            db.update_item(
                row["id"],
                decision_json=decision.model_dump(mode="json"),
                verdict=verdict,
            )
            stats.verdicts[verdict] = stats.verdicts.get(verdict, 0) + 1
            stats.processed += 1
            mark = {"无需改动": "✅", "建议补全缺失字段": "➕", "建议清洗/规范化": "🧹",
                    "保留现有专辑（AI 有不同判断）": "🛡", "需你选择出自哪张专辑": "❔",
                    "无法处理（没有候选）": "⌀"}.get(verdict, "?")
            say(f"  {label}  {mark} {verdict}  候选 {len(candidates)}")
    finally:
        if llm_client is not None:
            llm_client.close()

    stats.elapsed_sec = round(time.monotonic() - started, 1)
    db.finish_run(run_id, "done", {
        "processed": stats.processed,
        "cost_usd": round(stats.cost_usd, 6),
        "verdicts": stats.verdicts,
        "acoustid_limiter": ac_limiter.stats(),
        "musicbrainz_limiter": mb_limiter.stats(),
    })
    return stats


def _group_of(path: str, groups_by_path: dict) -> object:
    from pathlib import Path as _Path

    return groups_by_path.get(str(_Path(path).parent))


def _finalize_without_candidates(
    db: Database, row, stats: AnalyzeStats, opts: AnalyzeOptions, groups_by_path: dict | None = None
) -> None:
    view = _item_view(row, assume_no_album_tag=opts.assume_no_album_tag)
    decision = decide(
        view, [], None, strip_disc=opts.strip_disc,
        group=_group_of(view.path, groups_by_path or {}),
    )
    verdict = verdict_of(decision)
    db.update_item(row["id"], decision_json=decision.model_dump(mode="json"), verdict=verdict)
    stats.verdicts[verdict] = stats.verdicts.get(verdict, 0) + 1
    stats.processed += 1
