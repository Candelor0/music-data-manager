"""默认勾选规则 —— 决定「哪些可以放心批量写」。纯函数。

规则一句话：只默认勾"安全"的（往空字段填值 + 安全清洗）；
凡是会改动已有标签的，一律默认不勾，必须逐条确认。

一条待写改动被**默认勾选**，当且仅当下面 4 条全成立：

    1. `plan.allowed is True`（已过 writeplan 的安全校验）
    2. `decision.action ∈ {fill_missing, cleanup, keep_existing}`
    3. 每一项改动的 `kind ∈ {fill, cleanup, consensus}`
    4. 不存在"换一个发行版的专辑名"（`field == album` 且 `kind ∉ {cleanup, consensus}`）

为什么 `keep_existing` 也在其中
--------------------------------
它的意思是"AI 想换发行版，但给你保留现有的" —— 那条换名的改动**已经被丢掉了**
（`decide._drop_album_replacements`），计划里剩下的只有安全的填空与清洗。
所以按既定规则（"只填空字段或安全清洗的是安全的"），这部分**该默认勾**。

它仍然留在 🟠 组里显示，只是提醒"AI 有不同判断，你可能想去看一眼"，
不阻止那几条安全的填空被一起写掉。实测真实样例库里有 2 条属于这种情况
（14 条则根本没有可写改动）。

**为什么"有冲突"不作为拦条**（初稿里它是第 3 条，已去掉）：

    「已有值与同目录共识不一致」说的是**另一个字段**——那个字段有值，
    根本不会被写（共识只补空）。所以冲突**不影响要写什么**，
    拿它拦默认勾选既多余又有害：被拦下的条目会掉出 🟢 组，
    而它又不符合 🟡/🟠 的定义，最后**从待办里消失**（实测踩到）。

    现在冲突照旧在清单里用 ⚠ 显著提示，只是不阻止安全的填空与清洗。

**明确排除 `normalize`（写法统一）**：它会把 `Song  Name` 改成 `Song Name` ——
语义没变，但它改的是**你亲手写下的值**，所以必须你亲自点头。
实测它只在"用户显式选择采纳 AI"那条路径上出现，本来也不该自动勾。

安全性的机械表述（tests/unit/test_selectrules.py 里逐条断言）：

    默认勾选的每一项改动，要么 `before == ""`（纯填空），
    要么 `kind == "cleanup"`（去杂质，由 normalize.strip_title_junk /
    strip_album_qualifier / strip_disc_suffix 这些**保守**规则产生）。

即：**绝不会把一个值改成语义不同的另一个值。**

⚠️ 这里更正过一句早先的过度承诺
------------------------------------------------
开发记录初稿里写的是「默认勾选的改动，其 `before` 必须为空」，
却又在同一个表格里把「安全清洗（如去掉『(实体专辑版)』）」列为默认勾选项 ——
两句话自相矛盾，因为清洗本来就改动非空的已有值。以**本模块 + 测试**为准。
已确定按「填空 + 安全清洗」执行，故保留 `cleanup`。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Decision, TagChange, WritePlan

#: 允许出现在默认勾选项里的改动类型
SAFE_KINDS: frozenset[str] = frozenset({"fill", "cleanup", "consensus"})

#: 允许默认勾选的处置类型
SAFE_ACTIONS: frozenset[str] = frozenset({"fill_missing", "cleanup", "keep_existing"})

#: 专辑字段上允许的类型（排除"换发行版"）
ALBUM_SAFE_KINDS: frozenset[str] = frozenset({"cleanup", "consensus"})

#: 给界面看的不勾选理由
REASON_NOT_ALLOWED = "安全校验未通过"
REASON_ACTION = "需要你确认后才写入"
REASON_KIND = "包含会改动你已有标签的改动"
REASON_ALBUM = "会把专辑名换成另一个发行版"
REASON_OK = "只填空字段或做安全清洗"


@dataclass(frozen=True)
class SelectVerdict:
    checked: bool
    reason: str


def _unsafe_change(changes: list[TagChange]) -> TagChange | None:
    """找出第一项不该默认勾的改动。"""
    for change in changes:
        if change.kind not in SAFE_KINDS:
            return change
        if change.field == "album" and change.kind not in ALBUM_SAFE_KINDS:
            return change
    return None


def check(decision: Decision | None, plan: WritePlan | None) -> SelectVerdict:
    """判断这一条能否被默认勾选，并给出理由。"""
    if decision is None or plan is None:
        return SelectVerdict(False, REASON_ACTION)
    if not plan.allowed:
        return SelectVerdict(False, REASON_NOT_ALLOWED)
    if decision.action not in SAFE_ACTIONS:
        return SelectVerdict(False, REASON_ACTION)

    # 以**计划里的改动**为准：计划是唯一决定"要写什么"的地方
    unsafe = _unsafe_change(list(plan.changes))
    if unsafe is not None:
        if unsafe.field == "album" and unsafe.kind not in ALBUM_SAFE_KINDS:
            return SelectVerdict(False, REASON_ALBUM)
        return SelectVerdict(False, REASON_KIND)
    if not plan.changes:
        return SelectVerdict(False, REASON_NOT_ALLOWED)
    if decision.conflicts:
        # 照旧可以默认勾 —— 但把冲突说出来，让界面能提示
        return SelectVerdict(
            True, f"{REASON_OK}（另有 {len(decision.conflicts)} 处与同目录共识不一致，仅提示）"
        )
    return SelectVerdict(True, REASON_OK)


def is_checked(decision: Decision | None, plan: WritePlan | None) -> bool:
    return check(decision, plan).checked


def default_checked_ids(
    entries: list[tuple[int, Decision | None, WritePlan | None]],
) -> list[int]:
    """一批条目里，默认应该勾上的那些 item id。"""
    return [item_id for item_id, decision, plan in entries if is_checked(decision, plan)]


def assert_only_safe(changes: list[TagChange]) -> None:
    """安全断言：默认勾选项的改动只能是"填空"或"去杂质"。

    给测试与运行时自检用。故意做成会抛异常的形态，方便在关键路径上兜底。
    """
    for change in changes:
        pure_fill = not (change.before or "").strip()
        safe_cleanup = change.kind == "cleanup"
        if not (pure_fill or safe_cleanup):
            raise AssertionError(
                f"默认勾选的改动可能覆盖已有值：{change.field} "
                f"[{change.kind}] {change.before!r} → {change.after!r}"
            )
