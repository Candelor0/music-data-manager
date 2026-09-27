"""一键流程与按专辑裁决的测试。

网络与模型调用全部替换掉；分组、计划这些本地步骤用**真的**跑，
这样测的是「编排对不对」，而不是"我 mock 了什么"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mds.config import Settings
from mds.core.models import Candidate, Decision, TagChange
from mds.pipeline import run_all as run_all_mod
from mds.pipeline.choose import album_choices, apply_album_choice
from mds.pipeline.run_all import (
    RunAllOptions,
    build_todo_from_db,
    estimate_cost,
    pending_count,
    prepare,
    process,
    run_all,
    todo_entries,
)
from mds.storage.db import Database


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "t.db")
    database.migrate()
    yield database
    database.close()


def settings(**over) -> Settings:
    base = dict(
        acoustid_api_key="k",
        deepseek_api_key="",
        musicbrainz_user_agent="mds/0.1 ( mailto:a@b.c )",
        acoustid_rate=10000.0,
        musicbrainz_rate=10000.0,
        budget_per_100_tracks=0.5,
    )
    base.update(over)
    return Settings(**base)


def item(path: str, **tags) -> dict:
    base = {"title": Path(path).stem, "artist": "A", "album": "AL"}
    base.update(tags)
    return {"path": path, "tags": base}


# ── 费用预估（纯函数）────────────────────────────────────
@pytest.mark.parametrize(
    ("n", "per_100", "expected"),
    [
        (0, 0.1, 0.0),
        (1, 0.1, 0.01),      # 向上取整到分，不写 0
        (57, 0.1, 0.06),     # 与文档里的数字一致
        (100, 0.1, 0.10),
        (600, 0.1, 0.60),
        (57, 0.0, 0.0),
        (-5, 0.1, 0.0),
    ],
)
def test_estimate_cost(n: int, per_100: float, expected: float) -> None:
    assert estimate_cost(n, per_100) == pytest.approx(expected)


# ── 待办 / 待处理计数 ────────────────────────────────────
def test_pending_count_counts_undecided(db: Database) -> None:
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, ["/Music/a/1.flac", "/Music/a/2.flac"])
    rows = [dict(r) for r in db.iter_items(run_id)]
    assert pending_count(db, run_id) == 2
    db.update_item(rows[0]["id"], decision_json={"action": "no_op"})
    assert pending_count(db, run_id) == 1


def test_pending_count_retries_skipped_llm(db: Database) -> None:
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, ["/Music/a/1.flac"])
    row = dict(next(iter(db.iter_items(run_id))))
    db.update_item(row["id"], decision_json={"action": "no_op"}, llm_status="skipped")
    assert pending_count(db, run_id, use_llm=True) == 1
    assert pending_count(db, run_id, use_llm=False) == 0


def test_todo_entries_and_build_from_db(db: Database) -> None:
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, ["/Music/al/1.flac", "/Music/al/2.flac"])
    rows = {r["path"]: dict(r) for r in db.iter_items(run_id)}
    for path, row in rows.items():
        db.update_item(
            row["id"],
            tags_json=item(path)["tags"],
            decision_json=Decision(
                action="fill_missing",
                changes=[TagChange(field="date", before="", after="2022", kind="consensus")],
            ).model_dump(mode="json"),
            plan_json={
                "allowed": True,
                "changes": [
                    {"field": "date", "before": "", "after": "2022", "kind": "consensus"}
                ],
                "source": "fill_missing",
            },
        )

    entries = todo_entries(db, run_id)
    assert len(entries) == 2
    assert all(e.folder == "/Music/al" for e in entries)

    groups = build_todo_from_db(db, run_id)
    safe = next(g for g in groups if g.key == "safe")
    assert safe.n_items == 2
    assert safe.n_checked == 2
    assert safe.n_folders == 1


# ── 一键流程编排 ─────────────────────────────────────────
@pytest.fixture()
def fake_pipeline(monkeypatch, db):
    """把扫描与分析换成假的（它们要联网），其余步骤真跑。"""
    state = {"scan": 0, "analyze": 0, "order": []}
    tracks = [
        item("/Music/专辑甲/01.flac", album="专辑甲"),
        item("/Music/专辑甲/02.flac", album="专辑甲"),
    ]

    def fake_scan(database, library, *, params, limit=0, resume=True):
        state["scan"] += 1
        state["order"].append("scan")
        run_id = database.create_run(library, params)
        database.insert_items(run_id, [t["path"] for t in tracks])
        rows = {r["path"]: dict(r) for r in database.iter_items(run_id)}
        for track in tracks:
            database.update_item(
                rows[track["path"]]["id"], tags_json=track["tags"], duration_sec=200, fp_status="ok"
            )
        from mds.pipeline.scan import ScanResult

        return ScanResult(run_id=run_id, music_root=library, found=len(tracks), inserted=len(tracks))

    def fake_analyze(database, cfg, run_id, *, options=None, progress=None, should_stop=None):
        state["analyze"] += 1
        state["order"].append("analyze")
        say = progress or (lambda _m: None)
        for index, row in enumerate(database.iter_items(run_id), 1):
            say(f"  [{index}/2] {row['name']}  ➕ 建议补全缺失字段  候选 3")
        for raw in database.iter_items(run_id):
            database.update_item(
                int(raw["id"]),
                decision_json=Decision(
                    action="fill_missing",
                    changes=[TagChange(field="date", before="", after="2022", kind="consensus")],
                ).model_dump(mode="json"),
            )

        class Stats:
            processed = 2
            skipped_done = 0
            errors = 0
            cost_usd = 0.004
            elapsed_sec = 1.0

        return Stats()

    monkeypatch.setattr(run_all_mod, "scan", fake_scan)
    monkeypatch.setattr(run_all_mod, "analyze", fake_analyze)
    return state


def test_run_all_does_scan_group_analyze_plan(db, fake_pipeline) -> None:
    result = run_all(db, settings(), options=RunAllOptions(library="/Music"))
    assert fake_pipeline["order"] == ["scan", "analyze"]
    assert result.total == 2
    assert result.pending_before == 2
    assert result.estimated_cost == pytest.approx(0.01)
    assert result.cost_usd == pytest.approx(0.004)

    # 分组真的落库了
    groups = db.list_groups(result.run_id)
    assert len(groups) == 1
    assert groups[0]["n_files"] == 2

    # 计划真的生成了，待办也出来了
    safe = next(g for g in result.todo if g.key == "safe")
    assert safe.n_items == 2
    assert "2 首可以直接写入" in result.headline


def test_run_all_refuses_to_spend_when_cancelled_first(db, fake_pipeline) -> None:
    """一开始就取消：不许调分析（不花钱），但已经扫到的东西要留着。"""
    result = run_all(
        db, settings(), options=RunAllOptions(library="/Music"), should_stop=lambda: True
    )
    assert fake_pipeline["analyze"] == 0
    assert result.stopped is True
    assert result.total == 2  # 扫描结果保留，下次接着跑


def test_run_all_second_pass_skips_done_work(db, fake_pipeline) -> None:
    """再点一次「开始」：不重复处理已完成的（S27 增量语义）。"""
    first = run_all(db, settings(), options=RunAllOptions(library="/Music"))
    assert first.pending_before == 2
    # 已完成 → 待处理数归零，于是预估花费为 0
    assert pending_count(db, first.run_id) == 0
    assert estimate_cost(pending_count(db, first.run_id), 0.1) == 0.0


# ── 按专辑批量裁决 ───────────────────────────────────────
def _seed_album(db: Database, *, n: int = 3) -> tuple[str, list[int]]:
    run_id = db.create_run("/Music", {})
    paths = [f"/Music/专辑甲/{i:02d}.flac" for i in range(1, n + 1)]
    db.insert_items(run_id, paths)
    rows = [dict(r) for r in db.iter_items(run_id)]
    for row in rows:
        db.update_item(
            row["id"],
            tags_json=item(row["path"], album="")["tags"],
            recording_mbid="rec-1",
            decision_json=Decision(
                action="ask_user", reason="需你确认"
            ).model_dump(mode="json"),
        )
    # 三个候选：A 覆盖全部 3 首，B 只覆盖前 2 首，C 只覆盖最后 1 首
    db.mb_put(
        "rec-1",
        {
            "candidates": [
                Candidate(release_mbid="rel-A", album="专辑甲", date="2020", country="JP").model_dump(),
                Candidate(release_mbid="rel-B", album="专辑甲（初回）", date="2020", country="JP").model_dump(),
                Candidate(release_mbid="rel-C", album="专辑甲（限定）", date="2021", country="JP").model_dump(),
            ],
            "error": None,
        },
    )
    return run_id, [int(r["id"]) for r in rows]


def test_album_choices_groups_by_folder(db: Database) -> None:
    run_id, _ = _seed_album(db)
    choices = album_choices(db, run_id)
    assert len(choices) == 1
    assert choices[0].title == "专辑甲"
    assert choices[0].n_items == 3
    # 覆盖全目录的候选排最前
    assert choices[0].covering_candidates()[0].release_mbid == "rel-A"


def test_album_choice_applies_to_every_item_once(db: Database) -> None:
    """S28：同目录 N 首，操作一次即全部确定。"""
    run_id, ids = _seed_album(db, n=3)
    stats = apply_album_choice(db, run_id, "/Music/专辑甲", "rel-A")
    assert stats.n_total == 3
    assert stats.n_chosen == 3
    assert stats.n_unmatched == 0
    assert stats.plans_rebuilt == 3
    for item_id in ids:
        row = next(dict(r) for r in db.iter_items(run_id) if int(r["id"]) == item_id)
        assert row["user_choice"] == "picked:0"
    # 裁决后这个目录不再需要用户决定
    assert album_choices(db, run_id) == []


def test_album_choice_leaves_unmatched_items_pending(db: Database) -> None:
    """候选不在某条目的列表里 → 该条保留待决，不硬套。"""
    run_id, _ = _seed_album(db, n=2)
    # 把第 2 条的顺序颠倒（A 变成索引 1），再选一个第 2 条没有的候选
    row2 = sorted((dict(r) for r in db.iter_items(run_id)), key=lambda r: r["path"])[1]
    db.mb_put(
        "rec-1",
        {
            "candidates": [
                Candidate(release_mbid="rel-B", album="B").model_dump(),
                Candidate(release_mbid="rel-A", album="A").model_dump(),
            ],
            "error": None,
        },
    )
    stats = apply_album_choice(db, run_id, "/Music/专辑甲", "rel-A")
    assert stats.n_chosen == 2  # 两条都能落（都在列表里，只是序号不同）
    assert stats.n_unmatched == 0
    assert row2["path"].endswith("02.flac")

    # 裁决落库后，这个目录不再有待决条目
    assert album_choices(db, run_id) == []
    stats2 = apply_album_choice(db, run_id, "/Music/专辑甲", "rel-不存在")
    assert stats2.n_total == 0


def test_album_choice_on_unknown_folder_is_noop(db: Database) -> None:
    run_id, _ = _seed_album(db)
    stats = apply_album_choice(db, run_id, "/Music/不存在", "rel-A")
    assert stats.n_total == 0


def test_prepare_is_free_and_process_is_where_money_goes(db, fake_pipeline) -> None:
    """两段式：界面靠它实现「先扫描（免费）→ 报预估 → 再决定要不要花钱」。"""
    prepared = prepare(db, settings(), options=RunAllOptions(library="/Music"))
    assert fake_pipeline["analyze"] == 0, "第一段绝不能调分析（那是要花钱的）"
    assert prepared.stopped is False
    assert prepared.total == 2
    assert prepared.pending_before == 2
    assert prepared.estimated_cost == pytest.approx(0.01)
    # 第一段跑完还没分析 → 待办里全是「无需处理（未分析）」
    by_key = {g.key: g for g in prepared.todo}
    assert by_key["safe"].n_items == 0
    assert by_key["none"].n_items == 2

    result = process(db, settings(), prepared.run_id)
    assert fake_pipeline["analyze"] == 1
    assert result.processed == 2
    assert result.run_id == prepared.run_id
    # 第二段跑完，条目才进入「可以放心写入」
    after = {g.key: g for g in result.todo}
    assert after["safe"].n_items == 2
    assert after["none"].n_items == 0


def test_prepare_can_be_cancelled_before_spending(db, fake_pipeline) -> None:
    prepared = prepare(
        db, settings(), options=RunAllOptions(library="/Music"), should_stop=lambda: True
    )
    assert prepared.stopped is True
    assert fake_pipeline["analyze"] == 0
