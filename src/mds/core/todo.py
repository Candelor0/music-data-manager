"""待办 4 组 —— 把"几百首歌的决策"压成"几行要你动手指的事"。纯函数。

几百首歌一次性列出来会非常长，没人有看下去的欲望。

所以默认界面**不显示文件列表**，只显示 4 行：

    🟢 可以放心写入        547 首   ← 往空字段填值 + 安全清洗，默认全勾，一键批量写
    🟡 需要你选出自哪张专辑  31 首    ← 集中在 4 张专辑，选 4 次就够
    🟠 AI 有不同判断        18 首    ← 它想换发行版，默认保留你的
    ⚪ 无需处理             4 首     ← 没查到 / 无需改动 / 被安全规则拦下

一句话原则：**让你看"需要你动手指的"，不让你看"已经替你办好的"。**

本模块只依赖 core 的类型，不碰数据库、不碰 Qt → 可在无图形环境的机器上直接测。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import Decision, WritePlan
from .selectrules import is_checked

#: 四组的键（顺序固定，界面按这个顺序渲染）
GROUP_SAFE = "safe"
GROUP_CHOOSE = "choose"
GROUP_CONFLICT = "conflict"
GROUP_NONE = "none"

GROUP_ORDER: tuple[str, ...] = (GROUP_SAFE, GROUP_CHOOSE, GROUP_CONFLICT, GROUP_NONE)

GROUP_LABELS: dict[str, str] = {
    GROUP_SAFE: "🟢 可以写入",
    GROUP_CHOOSE: "🟡 需要确认专辑",
    GROUP_CONFLICT: "🟠 标签不一致",
    GROUP_NONE: "⚪ 无需处理",
}

#: 各组的一句话说明。**这是给用户看的文案**（界面与 `mds todo` 共用），
#: 措辞标准：不口语、不堆术语（既定）。
GROUP_HINTS: dict[str, str] = {
    GROUP_SAFE: "仅填写空字段与安全清洗，不覆盖已有标签",
    GROUP_CHOOSE: "多个候选时长一致，无法从音频分辨，需人工确认",
    GROUP_CONFLICT: "匹配结果与现有专辑名不同，默认保留现有标签",
    GROUP_NONE: "未查到匹配 / 无需改动 / 未通过安全规则",
}

#: 展示用：最多列几个专辑目录名
FOLDER_PREVIEW_LIMIT = 8


@dataclass(frozen=True)
class TodoEntry:
    """一个条目的决策与计划。文件夹用于「按专辑裁决」与"集中在几张专辑"。"""

    item_id: int
    folder: str
    decision: Decision | None
    plan: WritePlan | None


@dataclass
class TodoGroup:
    key: str
    label: str
    hint: str
    item_ids: list[int] = field(default_factory=list)
    n_changes: int = 0
    n_checked: int = 0
    by_folder: dict[str, list[int]] = field(default_factory=dict)

    @property
    def n_items(self) -> int:
        return len(self.item_ids)

    @property
    def n_folders(self) -> int:
        return len(self.by_folder)

    def folder_names(self, limit: int = FOLDER_PREVIEW_LIMIT) -> list[str]:
        """按"待办条数多的目录优先"返回目录名，最多 limit 个。"""
        ranked = sorted(self.by_folder.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return [folder for folder, _ in ranked[:limit]]

    def folder_label(self, folder: str) -> str:
        """给界面显示的短名（只取最后一级目录名）。"""
        return folder.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1] or folder

    def summary(self) -> str:
        """一行说明，例如「集中在 4 张专辑 → 选 4 次就够」。"""
        if not self.n_items:
            return ""
        if self.key == GROUP_CHOOSE and self.n_folders:
            return f"集中在 {self.n_folders} 张专辑 → 选 {self.n_folders} 次就够"
        if self.key == GROUP_SAFE:
            return f"共 {self.n_changes} 处改动，默认已全部勾选"
        if self.key == GROUP_CONFLICT and self.n_checked:
            return (
                f"其中 {self.n_checked} 首有可以放心写入的改动"
                "（往空字段填值 / 安全清洗，不动专辑名）"
            )
        return ""


def classify(decision: Decision | None, plan: WritePlan | None) -> str:
    """一个条目属于哪一组。"""
    if decision is None:
        return GROUP_NONE
    action = decision.action
    if action == "ask_user":
        return GROUP_CHOOSE
    if action == "keep_existing":
        return GROUP_CONFLICT
    if action in ("fill_missing", "cleanup"):
        # 有安全项可写才归为"放心写入"；否则没什么可做的
        return GROUP_SAFE if is_checked(decision, plan) else GROUP_NONE
    return GROUP_NONE


def build_todo(entries: list[TodoEntry]) -> list[TodoGroup]:
    """把一堆条目归成 4 组。**总是返回 4 组**（空组也返回），界面按需隐藏。"""
    groups: dict[str, TodoGroup] = {
        key: TodoGroup(key=key, label=GROUP_LABELS[key], hint=GROUP_HINTS[key])
        for key in GROUP_ORDER
    }
    for entry in entries:
        key = classify(entry.decision, entry.plan)
        group = groups[key]
        group.item_ids.append(entry.item_id)
        if entry.plan is not None and entry.plan.allowed:
            group.n_changes += len(entry.plan.changes)
        if is_checked(entry.decision, entry.plan):
            group.n_checked += 1
        group.by_folder.setdefault(entry.folder, []).append(entry.item_id)
    return [groups[key] for key in GROUP_ORDER]


def verdict_counts(entries: list[TodoEntry]) -> dict[str, int]:
    """按处置类型计数（给汇总行用）。"""
    counter: Counter[str] = Counter()
    for entry in entries:
        counter[entry.decision.action if entry.decision else "未分析"] += 1
    return dict(counter.most_common())


def checked_total(groups: list[TodoGroup]) -> int:
    """所有组里"默认勾选"的条目总数 —— 底部「确认写入」写的就是这些。"""
    return sum(g.n_checked for g in groups)


def headline(groups: list[TodoGroup]) -> str:
    """首屏顶部那一句话。"""
    by_key = {g.key: g for g in groups}
    total = sum(g.n_items for g in groups)
    need_you = by_key[GROUP_CHOOSE].n_items + by_key[GROUP_CONFLICT].n_items
    safe = by_key[GROUP_SAFE].n_items
    if not total:
        return "还没有分析结果，先点「开始」"
    if not need_you:
        return f"共 {total} 首，其中 {safe} 首可以直接写入，没有需要你决定的事"
    return f"共 {total} 首，其中 {safe} 首可以直接写入，{need_you} 首需要你决定"
