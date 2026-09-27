"""写入计划 —— 本阶段的核心规则层。纯函数，**不碰任何文件**。

它回答一个唯一的问题：**这一条，允许自动写哪些字段？**

三条不可违反的规则：
    R2  **文件已有专辑标签 → 默认保留**。AI 的不同判断默认不采纳，
        仅当用户显式选择"采纳 AI"时才允许改 album，**且只能改 album**。
    R3  **需用户选择候选的条目，在用户选之前一律不写**。
    R4  不使用模型自报的 confidence 做任何判断。

另外两条硬约束（防止"AI 好心办坏事"）：
    - **绝不把非空值清空**（目标值为空但原值非空 → 该字段直接丢弃）
    - 只处理白名单字段（title / artist / album / date / genre）
"""

from __future__ import annotations

from .models import (
    Decision,
    ItemView,
    TagChange,
    Tags,
    WritePlan,
)
from .normalize import normalize_text

#: 允许写入的字段白名单。
#: discnumber 只由"多碟专辑拆碟号"这条规则产生（见 cleaning.propose_changes），
#: 其它路径不会凭空写它。
ALLOWED_FIELDS: tuple[str, ...] = ("title", "artist", "album", "date", "genre", "discnumber")

#: 用户选择
CHOICE_KEPT_EXISTING = "kept_existing"  # 保留现有标签（默认）
CHOICE_ADOPTED_AI = "adopted_ai"        # 采纳 AI 判断（仅影响 album）
CHOICE_SKIPPED = "skipped"              # 跳过这条

_NOT_ALLOWED_HINT = "（如需采纳 AI 的专辑名，请显式选择）"


def _sanitize(changes: list[TagChange], current: Tags) -> list[TagChange]:
    """只留下「安全且有实际变化」的改动。"""
    out: list[TagChange] = []
    for change in changes:
        if change.field not in ALLOWED_FIELDS:
            continue
        before = (change.before or "").strip()
        after = (change.after or "").strip()
        if not after:
            # 绝不因为 AI 没给值就把用户数据清空
            continue
        if before == after:
            continue
        if normalize_text(before) == normalize_text(after):
            continue
        if current.value_of(change.field) != before:
            # 计划与当前标签对不上（期间数据变过）→ 放弃这条，避免盲写
            continue
        out.append(change.model_copy(update={"before": before, "after": after}))
    return out


def _target_tags(current: Tags, changes: list[TagChange]) -> Tags:
    target = current.model_copy()
    for change in changes:
        setattr(target, change.field, change.after)
    return target


def _plan(
    item: ItemView,
    changes: list[TagChange],
    *,
    source: str,
    reason_when_empty: str,
    reason_when_ok: str = "",
) -> WritePlan:
    changes = _sanitize(changes, item.tags)
    if not changes:
        return WritePlan(allowed=False, reason=reason_when_empty, target=item.tags)
    return WritePlan(
        allowed=True,
        reason=reason_when_ok,
        changes=changes,
        target=_target_tags(item.tags, changes),
        touches_album=any(c.field == "album" for c in changes),
        source=source,
    )


def build_write_plan(
    item: ItemView,
    decision: Decision,
    user_choice: str | None = None,
) -> WritePlan:
    """把决策变成写入计划。

    这是**唯一**允许决定"要不要写、写哪些字段"的地方。
    """
    action = decision.action

    if user_choice == CHOICE_SKIPPED:
        return WritePlan(allowed=False, reason="用户已跳过", target=item.tags)

    if action == "no_op":
        return WritePlan(allowed=False, reason="无需改动", target=item.tags)

    if action == "no_evidence":
        return WritePlan(allowed=False, reason="没有候选可依据", target=item.tags)

    # ── R3：需用户先选候选 ───────────────────────────────
    if action == "ask_user":
        if user_choice is None:
            return WritePlan(
                allowed=False,
                reason="需先确认这首出自哪张专辑（未选择，故不写入）",
                target=item.tags,
            )
        chosen = decision.chosen
        if chosen is None:
            return WritePlan(allowed=False, reason="候选缺失", target=item.tags)
        # 用户已选：用所选候选的专辑名，其余空字段照常补
        album_change = TagChange(
            field="album",
            before=item.tags.value_of("album"),
            after=chosen.album if not item.tags.value_of("album") else item.tags.value_of("album"),
            kind="fill",
        )
        others = [c for c in decision.changes if c.field != "album"]
        return _plan(
            item,
            [album_change, *others],
            source="ask_user_choice",
            reason_when_empty="所选候选与原标签一致，无需改动",
        )

    # ── R2：AI 与现有专辑标签冲突 → 默认保留 ───────────────
    if action == "keep_existing":
        if user_choice != CHOICE_ADOPTED_AI:
            # 默认：不换发行版；但"去版本说明"的安全清洗与其它空字段仍可写
            safe = [
                c
                for c in decision.changes
                if c.field != "album" or c.kind in ("cleanup", "consensus")
            ]
            return _plan(
                item,
                safe,
                source="fill_missing",
                reason_when_empty="现有专辑标签已保留" + _NOT_ALLOWED_HINT,
                reason_when_ok="保留现有专辑；仅补全空白字段与安全的专辑名清洗",
            )
        alt = decision.alternative
        if alt is None or not alt.album:
            return WritePlan(allowed=False, reason="AI 未给出可采纳的专辑名", target=item.tags)
        adopted = TagChange(
            field="album",
            before=item.tags.value_of("album"),
            after=alt.album,
            kind="normalize",
        )
        others = [c for c in decision.changes if c.field != "album"]
        return _plan(
            item,
            [adopted, *others],
            source="adopted_ai",
            reason_when_empty="采纳后与原标签一致，无需改动",
        )

    # ── 补全 / 清洗 ───────────────────────────────────────
    return _plan(
        item,
        decision.changes,
        source=action,
        reason_when_empty="安全校验后没有可写入的改动",
    )
