"""一键流程：扫描 → 分组 → 分析 → 生成计划。

界面上的「开始」按钮走这里。原本要用户依次点「选目录 → 扫描 → 分组 → 分析」，
步骤太多；所以四个按钮合成一个。其中「分组」是**完全本地、免费、零点几秒**的事，
本来就不该暴露给用户 —— 它在这里是 `analyze` 之前的内部准备步骤。

续跑语义（沿用早期版本的约定）：每一步都即时写库，
再点一次「开始」只处理没做完的，不会重复花钱。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Settings
from ..core.todo import TodoEntry, TodoGroup, build_todo
from ..logging_setup import get_logger
from ..storage.db import Database
from .analyze import AnalyzeOptions, analyze
from .common import decision_of
from .group import build_groups
from .plan import build_plans
from .scan import scan

log = get_logger("run_all")

ProgressFn = Callable[[str], None]


@dataclass
class RunAllOptions:
    library: str = ""
    limit: int = 0
    use_llm: bool = True
    assume_no_album_tag: bool = False
    mode: str = "full"


@dataclass
class RunAllResult:
    run_id: str = ""
    library: str = ""
    scanned: int = 0            # 本次新入库
    inserted: int = 0
    total: int = 0              # 这个 run 一共多少首
    pending_before: int = 0     # 开始前还有多少首没分析（费用预估依据）
    estimated_cost: float = 0.0  # 预计花费（元），**是估算不是保证**
    processed: int = 0
    skipped_done: int = 0
    errors: int = 0
    cost_usd: float = 0.0
    elapsed_sec: float = 0.0
    stopped: bool = False
    todo: list[TodoGroup] = field(default_factory=list)
    headline: str = ""


def estimate_cost(n_tracks: int, per_100: float) -> float:
    """预估云端花费（元）：按"¥per_100 / 100 首"算，**向上取整到分**。

    界面文案必须写明这是估算：实际花费以跑完的账单为准。
    """
    if n_tracks <= 0 or per_100 <= 0:
        return 0.0
    return math.ceil(n_tracks * per_100 / 100 * 100) / 100


def pending_count(db: Database, run_id: str, *, use_llm: bool = True) -> int:
    """还有多少首这次会被处理（费用预估的依据）。

    口径：还没有决策的条目；若启用云端，之前被跳过的（无 Key/超预算）也算上。
    """
    n = 0
    for row in db.iter_items(run_id):
        row = dict(row)
        if not row.get("decision_json"):
            n += 1
        elif use_llm and row.get("llm_status") == "skipped":
            n += 1
    return n


def todo_entries(db: Database, run_id: str) -> list[TodoEntry]:
    """把数据库里的行整理成待办条目（供界面与命令行共用）。"""
    from pathlib import Path

    out: list[TodoEntry] = []
    for raw in db.iter_items(run_id):
        row = dict(raw)
        decision = decision_of(row)
        plan = None
        data = row.get("plan_json")
        if data:
            from .common import load_json

            payload = load_json(data)
            if payload:
                from ..core.models import WritePlan

                try:
                    plan = WritePlan(**payload)
                except Exception:  # noqa: BLE001 - 旧格式当作没有计划
                    plan = None
        out.append(
            TodoEntry(
                item_id=int(row["id"]),
                folder=str(Path(str(row.get("path", ""))).parent),
                decision=decision,
                plan=plan,
            )
        )
    return out


def build_todo_from_db(db: Database, run_id: str) -> list[TodoGroup]:
    return build_todo(todo_entries(db, run_id))


def prepare(
    db: Database,
    settings: Settings,
    *,
    options: RunAllOptions | None = None,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RunAllResult:
    """第一段：扫描 + 分组 + 费用预估。**完全不花钱、不联网、不改文件。**

    约定「费用预估弹窗可关」（默认关）。弹窗必须在**界面主线程**弹，
    所以把它拆出来：这一段跑完，界面拿到准确的待处理首数，
    再决定要不要弹窗、以及要不要进第二段。
    """
    opts = options or RunAllOptions()
    say = progress or (lambda _msg: None)
    result = RunAllResult(library=opts.library)

    scan_result = scan(
        db,
        opts.library,
        params={
            "limit": opts.limit,
            "acoustid_rate": settings.acoustid_rate,
            "musicbrainz_rate": settings.musicbrainz_rate,
            "model": settings.model,
            "source": "run_all",
        },
        limit=opts.limit,
        resume=True,
    )
    result.run_id = scan_result.run_id
    result.library = scan_result.music_root
    result.scanned = scan_result.found
    result.inserted = scan_result.inserted
    result.total = len(list(db.iter_items(result.run_id)))
    say(f"扫描完成：发现 {scan_result.found} 个文件（新入库 {scan_result.inserted}）")

    group_stats = build_groups(
        db, result.run_id, recompute_decisions=False, refresh_plans=False
    )
    say(
        f"按专辑目录分组：{group_stats.n_groups} 个目录"
        f"（多文件 {group_stats.n_multi} 个、单文件 {group_stats.n_single} 个）"
    )

    result.pending_before = pending_count(db, result.run_id, use_llm=opts.use_llm)
    result.estimated_cost = estimate_cost(result.pending_before, settings.estimated_cost_per_100)
    say(
        f"本次需要处理 {result.pending_before} 首，"
        f"预估云端花费 ¥{result.estimated_cost:.2f}"
        f"（上限 ¥{settings.budget_per_100_tracks:.2f}/100 首，超了会自动停）"
    )
    if should_stop is not None and should_stop():
        result.stopped = True
        say("已取消。进度已保存，下次会接着跑。")
    return _finish(db, result, settings)


def process(
    db: Database,
    settings: Settings,
    run_id: str,
    *,
    options: RunAllOptions | None = None,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RunAllResult:
    """第二段：分析（联网 + 模型）+ 生成计划（本地）。"""
    opts = options or RunAllOptions()
    say = progress or (lambda _msg: None)
    run = db.get_run(run_id)
    result = RunAllResult(
        run_id=run_id,
        library=str(run["music_root"]) if run else opts.library,
        total=len(list(db.iter_items(run_id))),
    )

    started = time.monotonic()
    stats = analyze(
        db,
        settings,
        run_id,
        options=AnalyzeOptions(
            mode=opts.mode,
            use_llm=opts.use_llm,
            assume_no_album_tag=opts.assume_no_album_tag,
        ),
        progress=say,
        should_stop=should_stop,
    )
    result.processed = stats.processed
    result.skipped_done = stats.skipped_done
    result.errors = stats.errors
    result.cost_usd = stats.cost_usd
    result.stopped = bool(should_stop is not None and should_stop())
    result.elapsed_sec = round(time.monotonic() - started, 1)

    # refresh=True：用最新决策重建；**人工选择仍会保留**（build_plans 会读 items.user_choice）
    build_plans(db, run_id, refresh=True)
    say("已生成写入计划（只读，不改文件）")
    return _finish(db, result, settings)


def run_all(
    db: Database,
    settings: Settings,
    *,
    options: RunAllOptions | None = None,
    progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RunAllResult:
    """扫描 → 分组 → 分析 → 计划（命令行用；界面分两段调用 prepare/process）。"""
    opts = options or RunAllOptions()
    say = progress or (lambda _msg: None)

    prepared = prepare(db, settings, options=opts, progress=say, should_stop=should_stop)
    if prepared.stopped:
        return prepared

    result = process(
        db, settings, prepared.run_id, options=opts, progress=say, should_stop=should_stop
    )
    # 保留第一段的扫描统计，便于上层显示"本次新入库 N 首"
    result.scanned = prepared.scanned
    result.inserted = prepared.inserted
    result.pending_before = prepared.pending_before
    result.estimated_cost = prepared.estimated_cost
    return result


def render_todo(db: Database, run_id: str, *, limit: int = 0) -> list[str]:
    """把待办 4 组渲染成给人看的文本（命令行 `mds todo` 用）。

    **默认只输出这 4 行** —— 不让用户去看几百行文件列表（早期的诉求）。
    `limit > 0` 时才展开每组的目录明细。
    """
    groups = build_todo_from_db(db, run_id)
    from ..core.todo import GROUP_CHOOSE, GROUP_SAFE, headline

    lines: list[str] = []
    lines.append(headline(groups))
    lines.append("")
    for group in groups:
        if not group.n_items:
            continue
        suffix = f"　{group.summary()}" if group.summary() else ""
        lines.append(f"{group.label:<24} {group.n_items:>5} 首{suffix}")
        lines.append(f"{'':<24} {group.hint}")
        if limit and group.key != GROUP_SAFE:
            for folder in group.folder_names(limit):
                n = len(group.by_folder.get(folder, []))
                lines.append(f"{'':<26}· {group.folder_label(folder)}（{n} 首）")
    empty = [g for g in groups if not g.n_items]
    if empty:
        lines.append("")
        lines.append("（" + "、".join(g.label for g in empty) + " 这类暂时没有）")
    if not any(g.n_items for g in groups):
        lines.append("")
        lines.append("还没有分析结果。先跑：mds run <音乐库目录>")
    else:
        safe = next(g for g in groups if g.key == GROUP_SAFE)
        choose = next(g for g in groups if g.key == GROUP_CHOOSE)
        lines.append("")
        if safe.n_items:
            lines.append(f"下一步：mds apply {run_id} --yes       # 批量写入（默认先预览）")
        if choose.n_items:
            lines.append(f"下一步：mds choose {run_id}             # 看需要你选的专辑")
        lines.append(f"        mds todo {run_id} --limit 5     # 看每组的目录明细")
    return lines


def _finish(db: Database, result: RunAllResult, settings: Settings) -> RunAllResult:
    from ..core.todo import headline

    result.todo = build_todo_from_db(db, result.run_id)
    result.headline = headline(result.todo)
    log.debug("一键流程结果：%s", result)
    return result
