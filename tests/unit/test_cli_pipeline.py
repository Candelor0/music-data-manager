"""后加命令的测试：run / todo / choose，以及 rollback 的批次参数。

命令行的测试重点在**参数解析与输出**，业务逻辑已在各自的模块里测过。
"""

from __future__ import annotations

import pytest

from mds import config
from mds.cli import build_parser, main
from mds.core.models import Candidate, Decision
from mds.storage.db import Database


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    database = Database(path)
    database.migrate()
    monkeypatch.setattr(config, "db_path", lambda: path)
    yield database
    database.close()


def seed(db: Database, *, action: str = "fill_missing", n: int = 2, folder: str = "/Music/专辑甲"):
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, [f"{folder}/{i:02d}.flac" for i in range(1, n + 1)])
    rows = [dict(r) for r in db.iter_items(run_id)]
    for row in rows:
        db.update_item(
            row["id"],
            tags_json={"title": row["name"], "artist": "A", "album": ""},
            recording_mbid="rec-1",
            decision_json=Decision(action=action).model_dump(mode="json"),  # type: ignore[arg-type]
            plan_json={
                "allowed": True,
                "changes": [{"field": "date", "before": "", "after": "2022", "kind": "fill"}],
                "source": "fill_missing",
            },
        )
    return run_id, rows


def run(*args: str) -> int:
    """按命令行那样调用（`run("todo", run_id)`）。"""
    return main(list(args))


# ── 命令注册 ─────────────────────────────────────────────
def test_new_commands_are_registered() -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    for name in ("run", "todo", "choose", "group", "gui"):
        assert name in choices, f"缺少命令 {name}"


def test_run_accepts_expected_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "/Music", "--no-llm", "--limit", "10", "--yes"])
    assert args.directory == "/Music"
    assert args.no_llm is True
    assert args.limit == 10
    assert args.yes is True


def test_rollback_accepts_batch() -> None:
    parser = build_parser()
    assert parser.parse_args(["rollback", "r1", "--batch", "latest"]).batch == "latest"
    assert parser.parse_args(["rollback", "r1"]).batch is None


def test_choose_accepts_folder_and_candidate() -> None:
    parser = build_parser()
    args = parser.parse_args(["choose", "r1", "--folder", "/Music/x", "--candidate", "2"])
    assert args.folder == "/Music/x"
    assert args.candidate == 2


# ── todo ─────────────────────────────────────────────────
def test_todo_prints_four_groups_only(db, capsys) -> None:
    """默认输出是 4 行待办，不是几百行文件列表（S30）。"""
    run_id, _ = seed(db, action="fill_missing", n=600)
    assert run("todo", run_id) == 0
    out = capsys.readouterr().out
    assert "共 600 首" in out
    assert "600 首可以直接写入" in out
    assert "600 首" in out
    # 600 首不该出现 600 行文件
    assert out.count(".flac") == 0


def test_todo_limit_expands_folders(db, capsys) -> None:
    run_id, _ = seed(db, action="keep_existing", n=3, folder="/Music/某专辑")
    assert run("todo", run_id, "--limit", "5") == 0
    out = capsys.readouterr().out
    assert "某专辑" in out
    assert "需要你决定" in out


def test_todo_unknown_run_is_reported(db, capsys) -> None:
    assert run("todo", "不存在的run") == 1
    assert "找不到 run" in capsys.readouterr().out


def test_todo_empty_run_tells_what_to_do(db, capsys) -> None:
    run_id = db.create_run("/Music", {})
    assert run("todo", run_id) == 0
    out = capsys.readouterr().out
    assert "还没有分析结果" in out
    assert "mds run" in out


# ── choose ───────────────────────────────────────────────
def _with_candidates(db: Database, run_id: str, rows: list[dict]) -> None:
    db.mb_put(
        "rec-1",
        {
            "candidates": [
                Candidate(release_mbid="rel-A", album="专辑甲", date="2020").model_dump(),
                Candidate(release_mbid="rel-B", album="专辑甲（初回）", date="2020").model_dump(),
            ],
            "error": None,
        },
    )
    for row in rows:
        db.update_item(row["id"], decision_json=Decision(action="ask_user").model_dump(mode="json"))


def test_choose_lists_albums_when_no_folder(db, capsys) -> None:
    run_id, rows = seed(db, action="ask_user", n=3)
    _with_candidates(db, run_id, rows)
    assert run("choose", run_id) == 0
    out = capsys.readouterr().out
    assert "专辑甲" in out
    assert "3 首待定" in out
    assert "[0]" in out
    assert "mds choose" in out


def test_choose_applies_one_choice_to_whole_album(db, capsys) -> None:
    """S28：同目录 3 首，命令执行一次即全部确定。"""
    run_id, rows = seed(db, action="ask_user", n=3)
    _with_candidates(db, run_id, rows)
    assert run("choose", run_id, "--folder", "/Music/专辑甲", "--candidate", "0") == 0
    out = capsys.readouterr().out
    assert "覆盖 3/3 首" in out

    for row in rows:
        fresh = next(dict(r) for r in db.iter_items(run_id) if int(r["id"]) == int(row["id"]))
        assert fresh["user_choice"] == "picked:0"
        assert fresh["decision_json"] is not None
        assert "ask_user" not in str(fresh["decision_json"])  # 已裁决，不再待决

    # 再列一次：已经没有需要裁决的了
    assert run("choose", run_id) == 0
    assert "没有需要你选" in capsys.readouterr().out


def test_choose_without_candidate_is_rejected(db, capsys) -> None:
    run_id, rows = seed(db, action="ask_user", n=1)
    _with_candidates(db, run_id, rows)
    assert run("choose", run_id, "--folder", "/Music/专辑甲") == 1
    assert "--candidate" in capsys.readouterr().out


def test_choose_bad_candidate_index_is_rejected(db, capsys) -> None:
    run_id, rows = seed(db, action="ask_user", n=1)
    _with_candidates(db, run_id, rows)
    assert run("choose", run_id, "--folder", "/Music/专辑甲", "--candidate", "99") == 1
    assert "超出范围" in capsys.readouterr().out


def test_choose_unknown_folder_is_rejected(db, capsys) -> None:
    run_id, _ = seed(db, action="ask_user", n=1)
    assert run("choose", run_id, "--folder", "/Music/不存在", "--candidate", "0") == 1
    assert "没有待裁决" in capsys.readouterr().out


# ── rollback --batch ─────────────────────────────────────
def test_rollback_latest_batch_preview(db, capsys) -> None:
    run_id, rows = seed(db, n=2)
    for row in rows:
        db.record_change(
            run_id=run_id, item_id=int(row["id"]), path=row["path"],
            before_json={}, after_json={}, snapshot_ref={"mode": "tags"},
            before_sha="a", after_sha="b", batch_id="batch-9",
        )
    assert run("rollback", run_id, "--batch", "latest") == 0
    out = capsys.readouterr().out
    assert "最近一批：batch-9" in out
    assert "将回滚 2 条" in out
    assert "默认不执行" in out


def test_rollback_without_batches_says_so(db, capsys) -> None:
    run_id, _ = seed(db, n=1)
    assert run("rollback", run_id, "--batch", "latest") == 0
    assert "没有可撤销的批次" in capsys.readouterr().out
