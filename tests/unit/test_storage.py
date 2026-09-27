"""存储层测试：schema、续跑判据、缓存失效。"""

from __future__ import annotations

import sqlite3

import pytest

from mds.storage.db import SCHEMA_VERSION, Database, prompt_hash


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "test.db")
    database.migrate()
    yield database
    database.close()


def test_migrate_creates_all_tables(db: Database) -> None:
    names = {
        r["name"]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for expected in (
        "schema_version", "runs", "items", "llm_calls",
        "cache_fingerprint", "cache_mb", "cache_llm", "changes",
    ):
        assert expected in names, f"缺少表 {expected}"


def test_schema_version_is_recorded(db: Database) -> None:
    row = db.conn.execute("SELECT version FROM schema_version").fetchone()
    assert int(row["version"]) == SCHEMA_VERSION


def test_schema_version_mismatch_raises(tmp_path) -> None:
    """数据库版本高于程序要求时必须拒绝启动（避免用旧代码写新库）。"""
    path = tmp_path / "bad.db"
    with Database(path) as d:
        d.migrate()
        d.conn.execute("UPDATE schema_version SET version = 999")
        d.conn.commit()
    other = Database(path)
    with pytest.raises(RuntimeError, match="高于程序要求"):
        other.migrate()
    other.close()


def test_migration_from_v1_adds_write_columns(tmp_path) -> None:
    """v1 的旧库升级后应该具备写入相关列，且旧数据不丢。"""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE schema_version(version INTEGER NOT NULL);"
        "INSERT INTO schema_version(version) VALUES (1);"
        "CREATE TABLE runs(id TEXT PRIMARY KEY, music_root TEXT, started_at TEXT, "
        "  finished_at TEXT, status TEXT, params_json TEXT, stats_json TEXT);"
        "CREATE TABLE items(id INTEGER PRIMARY KEY, run_id TEXT, path TEXT, name TEXT, "
        "  tags_json TEXT, verdict TEXT, updated_at TEXT);"
        "INSERT INTO items(id, run_id, path, name, updated_at) "
        "  VALUES (1, 'r1', '/m/a.flac', 'a.flac', 'now');"
        "CREATE TABLE changes(id INTEGER PRIMARY KEY, run_id TEXT, item_id INTEGER, "
        "  path TEXT, before_json TEXT, created_at TEXT);"
    )
    conn.commit()
    conn.close()

    with Database(path) as db:
        db.migrate()
        item_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(items)")}
        for col in ("plan_json", "snap_ref", "write_status", "before_sha", "after_sha"):
            assert col in item_cols, f"迁移后缺少列 {col}"
        change_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(changes)")}
        for col in ("after_json", "status", "before_sha"):
            assert col in change_cols, f"changes 表缺少列 {col}"
        # 旧数据必须还在
        assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        assert db._current_version() == SCHEMA_VERSION


def test_wal_mode_enabled(db: Database) -> None:
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_scan_items_are_idempotent(db: Database) -> None:
    run_id = db.create_run("/music", {})
    assert db.insert_items(run_id, ["/music/a.flac", "/music/b.flac"]) == 2
    # 同一 run 重复扫描不应产生重复条目（UNIQUE(run_id, path)）
    db.insert_items(run_id, ["/music/a.flac", "/music/b.flac"])
    assert db.item_counts(run_id)["total"] == 2


def test_find_resumable_run_only_matches_running(db: Database) -> None:
    run_id = db.create_run("/music", {})
    assert db.find_resumable_run("/music") == run_id
    db.finish_run(run_id, "done")
    assert db.find_resumable_run("/music") is None


def test_item_counts_by_stage(db: Database) -> None:
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/a.flac", "/b.flac", "/c.flac"])
    rows = [dict(r) for r in db.iter_items(run_id)]
    db.update_item(rows[0]["id"], fp_status="ok", ac_status="ok", cand_status="ok",
                   llm_status="ok", decision_json={"action": "no_op"})
    db.update_item(rows[1]["id"], fp_status="ok", ac_status="no_result")
    counts = db.item_counts(run_id)
    assert counts["total"] == 3
    assert counts["fp_ok"] == 2
    assert counts["ac_ok"] == 1
    assert counts["llm_ok"] == 1


def test_fingerprint_cache_invalidates_on_mtime_change(db: Database) -> None:
    db.fingerprint_put("/a.flac", 100, 1.0, "FP", 200)
    assert db.fingerprint_get("/a.flac", 100, 1.0)["fingerprint"] == "FP"
    # 文件被改过（mtime 变）→ 必须失效，否则会用旧指纹查新文件
    assert db.fingerprint_get("/a.flac", 100, 2.0) is None
    # 大小变 → 同样失效
    assert db.fingerprint_get("/a.flac", 999, 1.0) is None


def test_mb_cache_roundtrip(db: Database) -> None:
    payload = {"track_title": "T", "candidates": [{"album": "A"}]}
    db.mb_put("mbid-1", payload)
    assert db.mb_get("mbid-1") == payload
    assert db.mb_get("missing") is None


def test_llm_cache_roundtrip(db: Database) -> None:
    key = prompt_hash("hello")
    db.llm_put(key, {"content": '{"chosen_index":0}'}, "deepseek-flash")
    got = db.llm_get(key)
    assert got is not None and got["model"] == "deepseek-flash"
    assert "chosen_index" in got["payload"]["content"]


def test_json_columns_store_unicode_readably(db: Database) -> None:
    run_id = db.create_run("/music", {"note": "中文"})
    db.insert_items(run_id, ["/m/サンプル年.flac"])
    row = next(iter(db.iter_items(run_id)))
    db.update_item(row["id"], tags_json={"album": "サンプル、テスト盤"})
    raw = db.conn.execute(
        "SELECT tags_json FROM items WHERE id=?", (row["id"],)
    ).fetchone()[0]
    assert "サンプル、テスト盤" in raw  # ensure_ascii=False，便于人工排查


def test_llm_call_audit_is_recorded(db: Database) -> None:
    db.record_llm_call(run_id="r1", item_id=1, model="m", mode="full",
                       prompt_tokens=100, cache_hit_tokens=20, completion_tokens=30,
                       latency_ms=900, cost_usd=0.001, status="ok")
    row = db.conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["prompt_tokens"] == 100
    assert row["status"] == "ok"


def test_foreign_key_enforced(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO items(run_id, path, name, updated_at) VALUES ('ghost','/x','x','now')"
        )


def test_env_file_with_bom_is_parsed(tmp_path) -> None:
    """Windows 记事本保存 UTF-8 会加 BOM；不剥掉的话第一个键名会带 BOM 读不到。"""
    from mds.config import parse_env_file

    path = tmp_path / ".env"
    path.write_bytes(b"\xef\xbb\xbfACOUSTID_API_KEY=abc\nDEEPSEEK_API_KEY=xyz\n")
    parsed = parse_env_file(path)
    assert parsed["ACOUSTID_API_KEY"] == "abc"
    assert parsed["DEEPSEEK_API_KEY"] == "xyz"


def test_migration_from_v2_adds_groups_table(tmp_path) -> None:
    """v2 的旧库升级后应具备分组表与 items.group_id，且旧数据不丢。"""
    import sqlite3

    path = tmp_path / "v2.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE schema_version(version INTEGER NOT NULL);"
        "INSERT INTO schema_version(version) VALUES (2);"
        "CREATE TABLE runs(id TEXT PRIMARY KEY, music_root TEXT, started_at TEXT, "
        "  finished_at TEXT, status TEXT, params_json TEXT, stats_json TEXT);"
        "CREATE TABLE items(id INTEGER PRIMARY KEY, run_id TEXT, path TEXT, name TEXT, "
        "  tags_json TEXT, verdict TEXT, updated_at TEXT);"
        "INSERT INTO items(id, run_id, path, name, updated_at) "
        "  VALUES (1, 'r1', '/m/a.flac', 'a.flac', 'now');"
        "CREATE TABLE changes(id INTEGER PRIMARY KEY, run_id TEXT, item_id INTEGER, "
        "  path TEXT, before_json TEXT, created_at TEXT);"
    )
    conn.commit()
    conn.close()

    with Database(path) as db:
        db.migrate()
        item_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(items)")}
        assert "group_id" in item_cols, "迁移后 items 应带上 group_id"
        tables = {
            r["name"]
            for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "groups" in tables, "迁移后应存在 groups 表"
        # 旧数据必须还在
        assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        assert db._current_version() == SCHEMA_VERSION


def test_groups_round_trip(db: Database) -> None:
    """分组写入 / 读取 / 重复分组不重复累积。"""
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/music/al/01.flac", "/music/al/02.flac"])
    ids = [int(r["id"]) for r in db.iter_items(run_id)]

    db.insert_group(
        run_id,
        folder_path="/music/al",
        n_files=2,
        folder_hint="al",
        artist_hint="A",
        consensus={"date": {"value": "2022", "votes": 2, "total": 2, "ratio": 1.0}},
        item_ids=ids,
    )
    groups = db.list_groups(run_id)
    assert len(groups) == 1
    assert groups[0]["folder_hint"] == "al"
    assert {int(r["group_id"]) for r in db.iter_items(run_id)} == {int(groups[0]["id"])}

    # 重跑分组：先清空再写，不应出现两条
    db.clear_groups(run_id)
    assert db.list_groups(run_id) == []
    assert {r["group_id"] for r in db.iter_items(run_id)} == {None}
    db.insert_group(
        run_id, folder_path="/music/al", n_files=2, folder_hint="al",
        artist_hint="A", consensus={}, item_ids=ids,
    )
    assert len(db.list_groups(run_id)) == 1


# ─────────────────────
def test_migration_from_v3_adds_batch_and_preferences(tmp_path) -> None:
    """v3 旧库升级：changes 加 batch_id、出现 preferences 表，旧数据不丢。

    ⚠️ 这条测试是有来历的：`migrate()` 先跑 schema.sql 再跑迁移，
    如果 schema.sql 里对 batch_id 建索引，老库会直接报
    `no such column: batch_id` 而启动不了。索引因此改由
    `db._ensure_extra_indexes()` 在迁移之后补建。
    """
    import sqlite3

    path = tmp_path / "v3.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE schema_version(version INTEGER NOT NULL);"
        "INSERT INTO schema_version(version) VALUES (3);"
        "CREATE TABLE runs(id TEXT PRIMARY KEY, music_root TEXT, started_at TEXT, "
        "  finished_at TEXT, status TEXT, params_json TEXT, stats_json TEXT);"
        "CREATE TABLE items(id INTEGER PRIMARY KEY, run_id TEXT, path TEXT, name TEXT, "
        "  tags_json TEXT, verdict TEXT, updated_at TEXT, group_id INTEGER);"
        "CREATE TABLE changes(id INTEGER PRIMARY KEY, run_id TEXT, item_id INTEGER, "
        "  path TEXT NOT NULL, before_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "  status TEXT NOT NULL DEFAULT 'applied');"
        "INSERT INTO changes(id, run_id, item_id, path, before_json, created_at) "
        "  VALUES (1, 'r1', 1, '/m/a.flac', '{}', 'now');"
        "CREATE TABLE groups(id INTEGER PRIMARY KEY, run_id TEXT, folder_path TEXT, "
        "  n_files INTEGER, folder_hint TEXT, artist_hint TEXT, consensus_json TEXT, "
        "  created_at TEXT, UNIQUE(run_id, folder_path));"
    )
    conn.commit()
    conn.close()

    with Database(path) as db:
        db.migrate()
        change_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(changes)")}
        assert "batch_id" in change_cols
        tables = {
            r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "preferences" in tables
        # 索引也必须补上（新库老库都要有）
        indexes = {
            r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_changes_batch" in indexes
        # 旧数据不丢
        assert db.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 1
        assert db._current_version() == SCHEMA_VERSION


def test_fresh_db_has_batch_index(db: Database) -> None:
    """全新库也要有批次索引（不然「撤销上一批」在大库上会慢）。"""
    indexes = {
        r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_changes_batch" in indexes


def test_preferences_round_trip(db: Database) -> None:
    assert db.get_pref("cost_estimate_confirm", "true") == "true"
    db.set_pref("cost_estimate_confirm", "false")
    assert db.get_pref("cost_estimate_confirm") == "false"
    db.set_pref("cost_estimate_confirm", "true")  # 覆盖写
    assert db.get_pref("cost_estimate_confirm") == "true"
    db.set_pref("library_path", "/Music")
    assert db.get_prefs() == {"cost_estimate_confirm": "true", "library_path": "/Music"}
    db.delete_pref("library_path")
    assert db.get_prefs() == {"cost_estimate_confirm": "true"}


def test_preferences_never_store_secrets(db: Database) -> None:
    """偏好表里**不得**出现密钥类键名（密钥走系统凭据库）。"""
    forbidden = ("KEY", "SECRET", "PASSWORD", "TOKEN")
    for key in db.get_prefs():
        assert not any(word in key.upper() for word in forbidden), f"偏好表里出现了疑似密钥键：{key}"


def test_change_batches_are_isolated(db: Database) -> None:
    run_id = db.create_run("/music", {})
    db.insert_items(run_id, ["/a.flac", "/b.flac", "/c.flac"])

    def rec(item_id: int, batch: str) -> None:
        db.record_change(
            run_id=run_id, item_id=item_id, path=f"/{item_id}.flac",
            before_json={}, after_json={}, snapshot_ref={},
            before_sha="x", after_sha="y", batch_id=batch,
        )

    rec(1, "batch-1")
    rec(2, "batch-1")
    rec(3, "batch-2")

    assert db.latest_batch_id(run_id) == "batch-2"
    assert len(db.iter_batch_changes("batch-1")) == 2
    assert len(db.iter_batch_changes("batch-2")) == 1

    # 撤销 batch-2 之后，最近可撤销的批次变成 batch-1
    change = db.iter_batch_changes("batch-2")[0]
    db.mark_change_rolled_back(int(change["id"]))
    assert db.latest_batch_id(run_id) == "batch-1"
    assert db.iter_batch_changes("batch-2") == []
    # 不筛状态时仍然看得到历史
    assert len(db.iter_batch_changes("batch-2", only_applied=False)) == 1


def test_no_batch_returns_empty(db: Database) -> None:
    run_id = db.create_run("/music", {})
    assert db.latest_batch_id(run_id) == ""
