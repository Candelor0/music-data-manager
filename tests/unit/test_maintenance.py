"""清空分析数据 / 占用统计的测试。

背景：
    试用便携版时，一进去看到的是**上次留下的结果列表**（数据在用户目录里，
    包本身不带数据），于是需要一个"清空"入口 —— 这就是本模块。
"""

from __future__ import annotations

from mds.core.models import Decision, TagChange
from mds.pipeline.maintenance import data_usage, purge_analysis, purge_snapshots
from mds.storage.db import Database


def _seed(db: Database) -> str:
    run_id = db.create_run("/Music", {})
    db.insert_items(run_id, ["/Music/al/01.flac", "/Music/al/02.flac"])
    row = dict(next(iter(db.iter_items(run_id))))
    db.update_item(
        row["id"],
        tags_json={"title": "x"},
        decision_json=Decision(
            action="fill_missing",
            changes=[TagChange(field="date", before="", after="2022", kind="fill")],
        ).model_dump(mode="json"),
    )
    db.record_change(
        run_id=run_id, item_id=int(row["id"]), path=row["path"],
        before_json={}, after_json={}, snapshot_ref={}, before_sha="a", after_sha="b",
    )
    db.set_pref("library_path", "/Music")
    db.set_pref("cost_estimate_confirm", "1")
    return run_id


def test_purge_clears_analysis_data(tmp_path) -> None:
    db = Database(tmp_path / "t.db")
    db.migrate()
    _seed(db)

    result = purge_analysis(db, snapshot_root=tmp_path / "snapshots")
    assert result.rows["runs"] == 1
    assert result.rows["items"] == 2
    assert result.rows["changes"] == 1
    assert result.total_rows >= 4

    # 清空后列表是空的
    assert db.list_runs() == []
    assert db.latest_batch_id("") == ""


def test_purge_keeps_settings_and_secrets(tmp_path) -> None:
    """**设置必须留着** —— 清数据不是恢复出厂设置，不能把密钥和音乐库目录也清了。"""
    db = Database(tmp_path / "t.db")
    db.migrate()
    _seed(db)
    purge_analysis(db, snapshot_root=tmp_path / "snapshots")

    assert db.get_pref("library_path") == "/Music"
    assert db.get_pref("cost_estimate_confirm") == "1"
    db.close()


def test_purge_removes_snapshots(tmp_path) -> None:
    db = Database(tmp_path / "t.db")
    db.migrate()
    _seed(db)
    snapshots = tmp_path / "snapshots"
    (snapshots / "batch-1").mkdir(parents=True)
    (snapshots / "batch-1" / "a.bin").write_bytes(b"x" * 2048)
    (snapshots / "b.bin").write_bytes(b"y" * 1024)

    result = purge_analysis(db, snapshot_root=snapshots)
    assert result.snapshots_removed == 2
    assert result.snapshots_bytes == 3072
    assert list(snapshots.rglob("*")) == [] or not any(
        p.is_file() for p in snapshots.rglob("*")
    )
    db.close()


def test_purge_on_empty_db_is_harmless(tmp_path) -> None:
    db = Database(tmp_path / "t.db")
    db.migrate()
    result = purge_analysis(db, snapshot_root=tmp_path / "nope")
    assert result.total_rows == 0
    assert result.snapshots_removed == 0
    assert "本来就没有数据" in result.summary()
    db.close()


def test_purge_snapshots_missing_dir(tmp_path) -> None:
    assert purge_snapshots(tmp_path / "不存在") == (0, 0)


def test_data_usage_reports_counts(tmp_path) -> None:
    db = Database(tmp_path / "t.db")
    db.migrate()
    _seed(db)
    snapshots = tmp_path / "snapshots"
    (snapshots / "b").mkdir(parents=True)
    (snapshots / "b" / "f.bin").write_bytes(b"z" * 512)

    usage = data_usage(db, snapshot_root=snapshots)
    assert usage["runs"] == 1
    assert usage["items"] == 2
    assert usage["db_bytes"] > 0
    assert usage["snapshot_files"] == 1
    assert usage["snapshots_bytes"] == 512
    db.close()


def test_purge_never_touches_music_files(tmp_path) -> None:
    """清数据**永远不碰音乐文件**（这条是红线）。"""
    music = tmp_path / "Music" / "al" / "01.flac"
    music.parent.mkdir(parents=True)
    music.write_bytes(b"FAKE-FLAC")

    db = Database(tmp_path / "t.db")
    db.migrate()
    _seed(db)
    purge_analysis(db, snapshot_root=tmp_path / "snapshots")

    assert music.is_file() and music.read_bytes() == b"FAKE-FLAC"
    db.close()


# ── 界面接线（设置页按钮 → 清空）────────────────────────
def test_settings_page_has_purge_button(qtbot, seeded_db) -> None:
    """设置页要有「清空分析数据」按钮（实测反馈：需要清空入口）。"""
    import pytest

    pytest.importorskip("PyQt5")
    from mds.config import Settings
    from mds.ui.main_window import MainWindow

    win = MainWindow(
        run_id=seeded_db["run_id"],
        db_file=seeded_db["path"],
        settings=Settings(acoustid_api_key="k", musicbrainz_user_agent="x"),
    )
    qtbot.addWidget(win)
    win._show_page(1)
    assert win._settings_page.purge_button.text() == "清空分析数据"
    # 占用说明要能说清"有多少东西"
    note = win._settings_page._data_note.text()
    assert "分析记录" in note


def test_purge_from_ui_clears_runs_but_keeps_settings(qtbot, seeded_db, monkeypatch) -> None:
    import pytest

    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QMessageBox

    from mds.config import Settings
    from mds.ui import prefs as ui_prefs
    from mds.ui.main_window import MainWindow

    db_path = seeded_db["path"]
    win = MainWindow(
        run_id=seeded_db["run_id"],
        db_file=db_path,
        settings=Settings(acoustid_api_key="k", musicbrainz_user_agent="x"),
    )
    qtbot.addWidget(win)
    win._db.set_pref(ui_prefs.LIBRARY_PATH, "/Music/我的库")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    win._on_purge_data()

    assert win._db.list_runs() == [], "清空后不应还有 run"
    assert win._db.get_pref(ui_prefs.LIBRARY_PATH) == "/Music/我的库", "设置必须保留"
    assert win._run_id == ""
