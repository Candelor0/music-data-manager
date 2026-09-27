"""视图模型与渲染测试 —— 不需要图形环境。"""

from __future__ import annotations

import pytest

from mds.ui import render
from mds.ui.viewmodel import (
    UNGROUPED_TITLE,
    build_groups,
    detail,
    fmt_delta,
    fmt_length,
    parse_progress_line,
)


# ── 纯小工具 ──────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  [12/57] 05. 間抜けなニムロド.flac  ➕ 建议补全缺失字段  候选 3", (12, 57, "05. 間抜けなニムロド.flac")),
        ("[1/1] a.flac", (1, 1, "a.flac")),
        ("已按专辑目录分组：10 个目录", None),
        ("", None),
        ("[试试] 这不是进度", None),
    ],
)
def test_parse_progress_line(text: str, expected) -> None:
    assert parse_progress_line(text) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "—"), (0, "—"), (59, "0:59"), (60, "1:00"), (241, "4:01"), (3725, "62:05")],
)
def test_fmt_length(seconds, expected: str) -> None:
    assert fmt_length(seconds) == expected


def test_fmt_delta() -> None:
    assert fmt_delta(None) == "—"
    assert fmt_delta(3) == "3s"


# ── 分组树 ────────────────────────────────────────────────
def test_build_groups_matches_seeded_library(seeded_db) -> None:
    nodes = build_groups(seeded_db["db"], seeded_db["run_id"])
    by_title = {n.title: n for n in nodes}
    assert "見本アルバム" in by_title
    assert "乌云典当记" in by_title

    album = by_title["見本アルバム"]
    assert album.n_files == 4
    assert len(album.files) == 4
    assert album.n_with_changes >= 1

    single = by_title["乌云典当记"]
    assert single.n_files == 1
    assert single.consensus_lines() == []  # 单文件目录不做互证


def test_group_consensus_lines_are_human_readable(seeded_db) -> None:
    nodes = build_groups(seeded_db["db"], seeded_db["run_id"])
    album = next(n for n in nodes if n.title == "見本アルバム")
    lines = album.consensus_lines()
    assert any(line.startswith("专辑：見本アルバム") for line in lines), lines
    assert any("首一致" in line for line in lines)


def test_ungrouped_files_land_in_a_visible_bucket(tmp_path) -> None:
    """没跑过分组时，文件必须仍然可见（而不是凭空消失）。"""
    from mds.storage.db import Database

    db = Database(tmp_path / "nogroup.db")
    db.migrate()
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, ["/Music/a/01.flac"])
    nodes = build_groups(db, run_id)
    assert len(nodes) == 1
    assert nodes[0].title == UNGROUPED_TITLE
    assert nodes[0].n_files == 1
    db.close()


# ── 详情 ──────────────────────────────────────────────────
def test_detail_contains_changes_conflicts_and_candidates(seeded_db) -> None:
    db, run_id, paths, rows = seeded_db["db"], seeded_db["run_id"], seeded_db["paths"], seeded_db["rows"]
    nodes = build_groups(db, run_id)
    album = next(n for n in nodes if n.title == "見本アルバム")

    out = detail(db, rows[paths[2]], group=album)
    assert out["verdict"] == "建议补全缺失字段"
    assert [c.field for c in out["changes"]] == ["date"]
    assert out["changes"][0].before == ""
    assert out["changes"][0].after == "2022"
    assert out["changes"][0].kind_label == "同目录补全"
    assert out["conflicts"] and out["conflicts"][0]["field"] == "流派"
    assert out["write"]["allowed"] is True
    assert out["consensus"], "应当带上同目录标注的说明"


def test_detail_tags_are_labelled_in_chinese(seeded_db) -> None:
    db, _run_id, paths, rows = seeded_db["db"], seeded_db["run_id"], seeded_db["paths"], seeded_db["rows"]
    out = detail(db, rows[paths[0]], group=None)
    assert out["tags"]["专辑"] == "見本アルバム"
    assert out["verdict"] == "无需改动"


def test_detail_of_unanalyzed_file_does_not_crash(tmp_path) -> None:
    from mds.storage.db import Database

    db = Database(tmp_path / "x.db")
    db.migrate()
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, ["/Music/a/01.flac"])
    row = dict(next(iter(db.iter_items(run_id))))
    out = detail(db, row, group=None)
    assert out["verdict"] == "未分析"
    assert out["changes"] == []
    assert out["candidates"] == []
    db.close()


# ── 汇总 ──────────────────────────────────────────────────
# ── 渲染 ──────────────────────────────────────────────────
def test_render_detail_html_shows_key_facts(seeded_db) -> None:
    db, run_id, paths, rows = seeded_db["db"], seeded_db["run_id"], seeded_db["paths"], seeded_db["rows"]
    nodes = build_groups(db, run_id)
    album = next(n for n in nodes if n.title == "見本アルバム")
    html = render.render_detail_html(detail(db, rows[paths[2]], group=album))

    assert "03. 境界線.flac" in html
    assert "建议补全缺失字段" in html
    assert "2022" in html
    assert "同目录补全" in html
    assert "不会" in html  # 冲突提示里必须写明「不会自动改动」
    assert "这些字段不会被自动修改" in html, "冲突提示要说清不会自动改"
    assert "同一文件夹内其他曲目" in html, "共识区用正常软件的说法"


def test_render_detail_html_escapes_user_content(seeded_db) -> None:
    db, _run_id, paths, rows = seeded_db["db"], seeded_db["run_id"], seeded_db["paths"], seeded_db["rows"]
    rows[paths[0]]["name"] = "<script>alert(1)</script>"
    html = render.render_detail_html(detail(db, rows[paths[0]], group=None))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_group_label_and_tooltip(seeded_db) -> None:
    nodes = build_groups(seeded_db["db"], seeded_db["run_id"])
    album = next(n for n in nodes if n.title == "見本アルバム")
    label = render.render_group_label(album)
    assert "見本アルバム（4 首）" in label
    tooltip = render.render_group_tooltip(album)
    assert album.folder_path in tooltip
    assert "首一致" in tooltip

    single = next(n for n in nodes if n.title == "乌云典当记")
    assert "单文件目录" in render.render_group_tooltip(single)


def test_render_candidates_note(seeded_db) -> None:
    db, _run_id, paths, rows = seeded_db["db"], seeded_db["run_id"], seeded_db["paths"], seeded_db["rows"]
    out = detail(db, rows[paths[2]], group=None)
    note = render.render_candidates_note(out)
    assert "候选" in note
    assert "見本アルバム" in note
    assert render.render_candidates_note({"candidates": []}) == "未查到匹配候选。"
