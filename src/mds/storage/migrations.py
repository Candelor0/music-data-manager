"""schema 迁移。

规则：
- `schema.sql` 是**全新数据库**的定义（总是最新）；
- 已有数据库靠这里的迁移逐步升级（`CREATE TABLE IF NOT EXISTS` 不会给旧表加列）；
- 迁移是**追加式**的：只加列、只建表，不删不改，保证旧数据可用。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

#: 每个迁移函数负责把版本 N-1 升到 N
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def migration(version: int):
    def deco(fn: Callable[[sqlite3.Connection], None]) -> Callable[[sqlite3.Connection], None]:
        MIGRATIONS[version] = fn
        return fn

    return deco


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _columns(conn, table)
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


@migration(2)
def _v1_to_v2(conn: sqlite3.Connection) -> None:
    """写入链路需要计划、快照引用与写入状态。"""
    _add_columns(
        conn,
        "items",
        {
            "plan_json": "TEXT",
            "user_choice": "TEXT",
            "snap_ref": "TEXT",
            "write_status": "TEXT NOT NULL DEFAULT 'pending'",
            "write_error": "TEXT",
            "before_sha": "TEXT",
            "after_sha": "TEXT",
            "written_at": "TEXT",
        },
    )
    _add_columns(
        conn,
        "changes",
        {
            "after_json": "TEXT",
            "before_sha": "TEXT",
            "after_sha": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'applied'",
        },
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_run ON changes(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_item ON changes(item_id)")


@migration(3)
def _v2_to_v3(conn: sqlite3.Connection) -> None:
    """目录级互证需要分组表与 items.group_id。"""
    _add_columns(conn, "items", {"group_id": "INTEGER"})
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id         TEXT NOT NULL,
            folder_path    TEXT NOT NULL,
            n_files        INTEGER NOT NULL,
            folder_hint    TEXT,
            artist_hint    TEXT,
            consensus_json TEXT,
            created_at     TEXT NOT NULL,
            UNIQUE(run_id, folder_path)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_groups_run ON groups(run_id)")


@migration(4)
def _v3_to_v4(conn: sqlite3.Connection) -> None:
    """界面接通写入需要「批次」与「界面偏好」。

    - changes.batch_id：一次 apply 一个批次 → 支撑「撤销上一批写入」
    - preferences 表：音乐库目录、费用弹窗开关、窗口大小等
      **这里不放任何密钥** —— 密钥走系统凭据库（adapters/secrets.py）
    """
    _add_columns(conn, "changes", {"batch_id": "TEXT"})
    conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_batch ON changes(batch_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS preferences (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
