"""SQLite 存储层。

设计要点：
- WAL 模式：读写并发更稳，崩溃后更容易恢复；
- **每个 item 的每一步即时提交**，使续跑能精确定位到"卡在哪一步"（S6）；
- 所有 JSON 字段统一用 ensure_ascii=False 存，便于人工排查。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .migrations import MIGRATIONS

SCHEMA_VERSION = 4
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    # ── 生命周期 ──────────────────────────────────────────
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def migrate(self) -> None:
        """建表 + 升级。

        顺序很要紧：
          1. 先读现有版本（新库没有 schema_version 表）
          2. 跑 schema.sql（对旧库是 no-op，因为都是 CREATE ... IF NOT EXISTS）
          3. 按需跑迁移（追加式，只加列/建表）
        """
        current = self._current_version()
        self.conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))

        if current == 0:
            self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            self._ensure_extra_indexes()
        elif current < SCHEMA_VERSION:
            for version in range(current + 1, SCHEMA_VERSION + 1):
                migration = MIGRATIONS.get(version)
                if migration is None:
                    raise RuntimeError(f"缺少迁移脚本：v{version - 1} → v{version}")
                migration(self.conn)
            self.conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            self._ensure_extra_indexes()
        elif current > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库 schema 版本（{current}）高于程序要求的 {SCHEMA_VERSION}，"
                "请升级程序或使用新的数据目录"
            )
        self.conn.commit()

    def _ensure_extra_indexes(self) -> None:
        """补建「迁移才加出来的列」上的索引。

        为什么不在 schema.sql 里直接建：`migrate()` 是先跑 schema.sql、再跑迁移。
        老库在跑 schema.sql 时，`changes` 表还没有 `batch_id` 列，建索引会直接报
        `no such column: batch_id`（实测踩到）。所以这类索引统一放在这里，
        新库老库迁移之后都会走到，且都是 `IF NOT EXISTS`，重复执行无害。
        """
        if "batch_id" in self._columns("changes"):
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_changes_batch ON changes(batch_id)"
            )

    def _columns(self, table: str) -> set[str]:
        return {str(row[1]) for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def _current_version(self) -> int:
        try:
            row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        except sqlite3.OperationalError:
            return 0  # 表还不存在 = 全新库
        return int(row["version"]) if row else 0

    # ── runs ─────────────────────────────────────────────
    def create_run(self, music_root: str, params: dict[str, Any]) -> str:
        run_id = new_run_id()
        self.conn.execute(
            "INSERT INTO runs(id, music_root, started_at, status, params_json) VALUES (?,?,?,?,?)",
            (run_id, music_root, now_iso(), "running", json.dumps(params, ensure_ascii=False)),
        )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, stats: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, stats_json=? WHERE id=?",
            (now_iso(), status, json.dumps(stats or {}, ensure_ascii=False), run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def find_resumable_run(self, music_root: str) -> str | None:
        """找同一个音乐库目录下仍在 running 的 run —— 用于续跑而不是新建。"""
        row = self.conn.execute(
            "SELECT id FROM runs WHERE music_root=? AND status='running' "
            "ORDER BY started_at DESC LIMIT 1",
            (music_root,),
        ).fetchone()
        return str(row["id"]) if row else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, music_root, started_at, finished_at, status FROM runs "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── items ────────────────────────────────────────────
    def insert_items(self, run_id: str, paths: Iterable[str]) -> int:
        rows = [(run_id, p, Path(p).name, now_iso()) for p in paths]
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO items(run_id, path, name, updated_at) VALUES (?,?,?,?)", rows
        )
        self.conn.commit()
        return cur.rowcount

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        """按 id 取一行条目（界面逐条查看时用）。"""
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def iter_items(self, run_id: str) -> Iterator[sqlite3.Row]:
        yield from self.conn.execute(
            "SELECT * FROM items WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()

    def update_item(self, item_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k}=?" for k in fields)
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                  for v in fields.values()]
        values.append(item_id)
        self.conn.execute(f"UPDATE items SET {assignments} WHERE id=?", values)
        self.conn.commit()

    def item_counts(self, run_id: str) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "  SUM(CASE WHEN fp_status='ok' THEN 1 ELSE 0 END) AS fp_ok, "
            "  SUM(CASE WHEN ac_status='ok' THEN 1 ELSE 0 END) AS ac_ok, "
            "  SUM(CASE WHEN cand_status='ok' THEN 1 ELSE 0 END) AS cand_ok, "
            "  SUM(CASE WHEN llm_status='ok' THEN 1 ELSE 0 END) AS llm_ok, "
            "  SUM(CASE WHEN llm_status='error' THEN 1 ELSE 0 END) AS llm_error, "
            "  SUM(CASE WHEN llm_status='schema_error' THEN 1 ELSE 0 END) AS llm_schema_error, "
            "  SUM(CASE WHEN llm_status='skipped' THEN 1 ELSE 0 END) AS llm_skipped "
            "FROM items WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    # ── 缓存 ─────────────────────────────────────────────
    def fingerprint_get(self, path: str, size: int, mtime: float) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT fingerprint, duration_sec FROM cache_fingerprint "
            "WHERE path_key=? AND size=? AND mtime=?",
            (path, size, mtime),
        ).fetchone()
        return dict(row) if row else None

    def fingerprint_put(self, path: str, size: int, mtime: float, fingerprint: str,
                        duration_sec: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache_fingerprint"
            "(path_key, size, mtime, fingerprint, duration_sec, updated_at) VALUES (?,?,?,?,?,?)",
            (path, size, mtime, fingerprint, duration_sec, now_iso()),
        )
        self.conn.commit()

    def mb_get(self, recording_mbid: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM cache_mb WHERE recording_mbid=?", (recording_mbid,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def mb_put(self, recording_mbid: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache_mb(recording_mbid, payload_json, updated_at) VALUES (?,?,?)",
            (recording_mbid, json.dumps(payload, ensure_ascii=False), now_iso()),
        )
        self.conn.commit()

    def llm_get(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json, model FROM cache_llm WHERE prompt_hash=?", (key,)
        ).fetchone()
        return {"payload": json.loads(row["payload_json"]), "model": row["model"]} if row else None

    def llm_put(self, key: str, payload: dict[str, Any], model: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache_llm(prompt_hash, payload_json, model, updated_at) "
            "VALUES (?,?,?,?)",
            (key, json.dumps(payload, ensure_ascii=False), model, now_iso()),
        )
        self.conn.commit()

    def record_llm_call(self, **fields: Any) -> None:
        fields["created_at"] = now_iso()
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        self.conn.execute(
            f"INSERT INTO llm_calls({columns}) VALUES ({placeholders})", list(fields.values())
        )
        self.conn.commit()

    # ── 目录分组──────────────────────────────
    def clear_groups(self, run_id: str) -> None:
        self.conn.execute("UPDATE items SET group_id=NULL WHERE run_id=?", (run_id,))
        self.conn.execute("DELETE FROM groups WHERE run_id=?", (run_id,))
        self.conn.commit()

    def insert_group(
        self,
        run_id: str,
        *,
        folder_path: str,
        n_files: int,
        folder_hint: str,
        artist_hint: str,
        consensus: dict,
        item_ids: list[int],
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO groups(run_id, folder_path, n_files, folder_hint, artist_hint, "
            "consensus_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                run_id,
                folder_path,
                n_files,
                folder_hint,
                artist_hint,
                json.dumps(consensus, ensure_ascii=False),
                now_iso(),
            ),
        )
        group_id = int(cur.lastrowid or 0)
        self.conn.executemany(
            "UPDATE items SET group_id=? WHERE id=?", [(group_id, i) for i in item_ids]
        )
        self.conn.commit()
        return group_id

    def list_groups(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM groups WHERE run_id=? ORDER BY folder_path", (run_id,)
            )
        ]

    # ── 写入计划 ─────────────────────────────────────────
    def set_plan(self, item_id: int, plan: dict[str, Any], *, write_status: str) -> None:
        self.conn.execute(
            "UPDATE items SET plan_json=?, write_status=?, updated_at=? WHERE id=?",
            (json.dumps(plan, ensure_ascii=False), write_status, now_iso(), item_id),
        )
        self.conn.commit()

    def set_user_choice(self, item_id: int, choice: str) -> None:
        self.conn.execute(
            "UPDATE items SET user_choice=?, updated_at=? WHERE id=?",
            (choice, now_iso(), item_id),
        )
        self.conn.commit()

    def plan_counts(self, run_id: str) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT "
            "  COUNT(*) AS total, "
            "  SUM(CASE WHEN write_status='planned' THEN 1 ELSE 0 END) AS planned, "
            "  SUM(CASE WHEN write_status='pending' THEN 1 ELSE 0 END) AS no_plan, "
            "  SUM(CASE WHEN write_status='verified' THEN 1 ELSE 0 END) AS verified, "
            "  SUM(CASE WHEN write_status='failed' THEN 1 ELSE 0 END) AS failed "
            "FROM items WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    def pending_write_count(self, run_id: str) -> int:
        """还有多少条“该写、但还没写成”的条目（含上次失败的，可重试）。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE run_id=? AND plan_json IS NOT NULL "
            "AND write_status IN ('planned','failed','snapshotted','written')",
            (run_id,),
        ).fetchone()
        return int(row["n"] or 0)

    # ── 变更记录 ─────────────────────────────────────────
    def record_change(
        self,
        *,
        run_id: str,
        item_id: int,
        path: str,
        before_json: dict[str, Any],
        after_json: dict[str, Any] | None,
        snapshot_ref: dict[str, Any] | None,
        before_sha: str,
        after_sha: str,
        status: str = "applied",
        batch_id: str = "",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO changes(run_id, item_id, path, before_json, after_json, snapshot_ref, "
            "before_sha, after_sha, status, created_at, batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                item_id,
                path,
                json.dumps(before_json, ensure_ascii=False),
                json.dumps(after_json or {}, ensure_ascii=False),
                json.dumps(snapshot_ref or {}, ensure_ascii=False),
                before_sha,
                after_sha,
                status,
                now_iso(),
                batch_id or None,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def iter_changes(self, run_id: str, *, only_applied: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM changes WHERE run_id=?"
        if only_applied:
            sql += " AND status='applied'"
        sql += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(sql, (run_id,))]

    # ── 写入批次────────────
    def latest_batch_id(self, run_id: str, *, only_applied: bool = True) -> str:
        """最近一个**还有内容可撤销**的批次。

        不用时间戳猜批次：同一秒可能有多个批次，那样会撤错。
        """
        sql = (
            "SELECT batch_id FROM changes WHERE run_id=? AND batch_id IS NOT NULL"
        )
        if only_applied:
            sql += " AND status='applied'"
        sql += " ORDER BY id DESC LIMIT 1"
        row = self.conn.execute(sql, (run_id,)).fetchone()
        return str(row["batch_id"]) if row and row["batch_id"] else ""

    def iter_batch_changes(self, batch_id: str, *, only_applied: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM changes WHERE batch_id=?"
        if only_applied:
            sql += " AND status='applied'"
        sql += " ORDER BY id"
        return [dict(r) for r in self.conn.execute(sql, (batch_id,))]

    # ── 界面偏好（**不放任何密钥**）──────────────────────
    def get_pref(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_pref(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO preferences(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), now_iso()),
        )
        self.conn.commit()

    def get_prefs(self) -> dict[str, str]:
        return {str(r["key"]): str(r["value"]) for r in self.conn.execute("SELECT * FROM preferences")}

    def delete_pref(self, key: str) -> None:
        self.conn.execute("DELETE FROM preferences WHERE key=?", (key,))
        self.conn.commit()

    def mark_change_rolled_back(self, change_id: int, status: str = "rolled_back") -> None:
        self.conn.execute(
            "UPDATE changes SET status=?, rolled_back_at=? WHERE id=?",
            (status, now_iso(), change_id),
        )
        self.conn.commit()

    def update_change(self, change_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                  for v in fields.values()]
        values.append(change_id)
        self.conn.execute(f"UPDATE changes SET {assignments} WHERE id=?", values)
        self.conn.commit()

    def get_change(self, change_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()
        return dict(row) if row else None

    def find_open_change(self, item_id: int) -> dict[str, Any] | None:
        """找该条目"已写入但尚未落账"的记录（用于崩溃后对账）。"""
        row = self.conn.execute(
            "SELECT * FROM changes WHERE item_id=? AND status='applying' ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None
