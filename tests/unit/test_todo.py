"""待办 4 组测试（600 行压成 4 行的那套规则）。"""

from __future__ import annotations

from mds.core.models import Decision, TagChange, WritePlan
from mds.core.todo import (
    GROUP_CHOOSE,
    GROUP_CONFLICT,
    GROUP_NONE,
    GROUP_ORDER,
    GROUP_SAFE,
    TodoEntry,
    build_todo,
    classify,
    headline,
    verdict_counts,
)


def dec(action: str = "fill_missing", **kw) -> Decision:
    return Decision(action=action, **kw)  # type: ignore[arg-type]


FILL = TagChange(field="date", before="", after="2022", kind="fill")
ALBUM_CLEANUP = TagChange(field="album", before="专辑（实体版）", after="专辑", kind="cleanup")
NORMALIZE = TagChange(field="title", before="A  B", after="A B", kind="normalize")


def plan(changes=(), *, allowed: bool = True) -> WritePlan:
    return WritePlan(allowed=allowed, changes=list(changes), source="fill_missing")


def entry(item_id: int, action: str = "fill_missing", *, folder: str = "/Music/al", **kw) -> TodoEntry:
    d = kw.pop("decision", None)
    p = kw.pop("plan", None)
    if d is None:
        d = dec(action, **kw)
    if p is None:
        p = plan([FILL])
    return TodoEntry(item_id=item_id, folder=folder, decision=d, plan=p)


# ── 归类 ──────────────────────────────────────────────────
def test_classify_by_action() -> None:
    assert classify(dec("fill_missing"), plan([FILL])) == GROUP_SAFE
    assert classify(dec("cleanup"), plan([ALBUM_CLEANUP])) == GROUP_SAFE
    assert classify(dec("ask_user"), plan()) == GROUP_CHOOSE
    assert classify(dec("keep_existing"), plan()) == GROUP_CONFLICT
    assert classify(dec("no_op"), plan()) == GROUP_NONE
    assert classify(dec("no_evidence"), plan()) == GROUP_NONE
    assert classify(None, None) == GROUP_NONE


def test_classify_safe_action_with_disallowed_plan_is_none() -> None:
    """说是"建议补全"，但计划被安全校验拦下 → 没什么可做的，归入无需处理。"""
    assert classify(dec("fill_missing"), plan([FILL], allowed=False)) == GROUP_NONE
    assert classify(dec("fill_missing"), plan([])) == GROUP_NONE


def test_classify_unsafe_change_goes_to_none() -> None:
    """带 normalize 的项不算"放心写入"（不能默认勾），也不该混进要你决定的组。"""
    assert classify(dec("cleanup"), plan([NORMALIZE])) == GROUP_NONE


# ── 分组 ──────────────────────────────────────────────────
def test_always_returns_four_groups_in_order() -> None:
    groups = build_todo([])
    assert [g.key for g in groups] == list(GROUP_ORDER)
    assert all(g.n_items == 0 for g in groups)


def test_groups_split_correctly() -> None:
    entries = [
        entry(1, "fill_missing"),
        entry(2, "fill_missing"),
        entry(3, "ask_user"),
        entry(4, "keep_existing"),
        entry(5, "no_evidence"),
    ]
    by_key = {g.key: g for g in build_todo(entries)}
    assert by_key[GROUP_SAFE].item_ids == [1, 2]
    assert by_key[GROUP_CHOOSE].item_ids == [3]
    assert by_key[GROUP_CONFLICT].item_ids == [4]
    assert by_key[GROUP_NONE].item_ids == [5]


def test_safe_group_counts_changes_and_checked() -> None:
    entries = [
        entry(1, "fill_missing", plan=plan([FILL, ALBUM_CLEANUP])),
        entry(2, "fill_missing", plan=plan([FILL])),
    ]
    safe = next(g for g in build_todo(entries) if g.key == GROUP_SAFE)
    assert safe.n_items == 2
    assert safe.n_changes == 3
    assert safe.n_checked == 2


def test_folder_aggregation_and_preview() -> None:
    entries = [
        entry(1, "ask_user", folder="/Music/A/专辑一"),
        entry(2, "ask_user", folder="/Music/A/专辑一"),
        entry(3, "ask_user", folder="/Music/A/专辑二"),
        entry(4, "ask_user", folder="/Music/B/专辑三"),
    ]
    choose = next(g for g in build_todo(entries) if g.key == GROUP_CHOOSE)
    assert choose.n_items == 4
    assert choose.n_folders == 3
    # 待办条数多的目录排前面
    assert choose.folder_names()[0] == "/Music/A/专辑一"
    assert choose.folder_label("/Music/A/专辑一") == "专辑一"
    assert choose.folder_label("D:\\Music\\专辑一") == "专辑一"


def test_conflict_group_counts_its_safe_part() -> None:
    """🟠 组里那些"安全的填空/清洗"要能被默认勾上并计数 —— 不然它们会被白白跳过。"""
    entries = [
        entry(1, "keep_existing", plan=plan([FILL])),
        entry(2, "keep_existing", plan=plan([])),
    ]
    conflict = next(g for g in build_todo(entries) if g.key == GROUP_CONFLICT)
    assert conflict.n_items == 2
    assert conflict.n_checked == 1
    assert "1 首有可以放心写入的改动" in conflict.summary()


def test_checked_total_spans_groups() -> None:
    from mds.core.todo import checked_total

    entries = [
        entry(1, "fill_missing", plan=plan([FILL])),
        entry(2, "keep_existing", plan=plan([FILL])),
        entry(3, "ask_user", plan=plan([FILL])),
    ]
    assert checked_total(build_todo(entries)) == 2


def test_choose_summary_says_how_many_clicks() -> None:
    """「集中在 4 张专辑 → 选 4 次就够」—— 这正是设计目标"看不下去"解法。"""
    entries = [entry(i, "ask_user", folder=f"/Music/专辑{i}") for i in range(1, 5)]
    choose = next(g for g in build_todo(entries) if g.key == GROUP_CHOOSE)
    assert "4 张专辑" in choose.summary()
    assert "选 4 次" in choose.summary()


def test_safe_summary_mentions_change_count() -> None:
    safe = next(g for g in build_todo([entry(1, plan=plan([FILL, FILL]))]) if g.key == GROUP_SAFE)
    assert "2 处改动" in safe.summary()


def test_verdict_counts() -> None:
    entries = [entry(1, "fill_missing"), entry(2, "fill_missing"), entry(3, "no_op"), TodoEntry(4, "/x", None, None)]
    assert verdict_counts(entries) == {"fill_missing": 2, "no_op": 1, "未分析": 1}


# ── 顶部那句话 ────────────────────────────────────────────
def test_headline_mentions_what_needs_you() -> None:
    entries = [entry(i) for i in range(1, 11)] + [entry(11, "ask_user"), entry(12, "keep_existing")]
    text = headline(build_todo(entries))
    assert "共 12 首" in text
    assert "10 首可以直接写入" in text
    assert "2 首需要你决定" in text


def test_headline_when_nothing_needs_you() -> None:
    text = headline(build_todo([entry(i) for i in range(1, 4)]))
    assert "没有需要你决定的事" in text


def test_headline_when_empty() -> None:
    assert "先点" in headline(build_todo([]))


def test_large_library_still_only_four_rows() -> None:
    """600 首 → 首屏仍然只有 4 组（这是 S30 的核心）。"""
    entries = [entry(i, folder=f"/Music/专辑{i % 40}") for i in range(600)]
    groups = build_todo(entries)
    assert len(groups) == 4
    assert sum(g.n_items for g in groups) == 600
    assert next(g for g in groups if g.key == GROUP_SAFE).n_items == 600
