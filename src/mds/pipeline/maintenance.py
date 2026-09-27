"""维护操作：清空分析数据、查看占用。

为什么要有这个
--------------
程序把分析结果、缓存、快照存在**用户数据目录**（`%LOCALAPPDATA%\\MusicDataManager\\`），
这个位置跟着电脑走、不跟着软件文件夹走。所以：

- 试用之后想正式开始 → 需要把试用的结果清掉
- 把软件拷给别人之前 → 需要把自己电脑上的结果清掉

清空时必须说清两件事（界面上也要写）：
1. **会删什么**：分析结果、缓存、快照（快照删了，旧批次就不能再撤销了）
2. **不会删什么**：你的密钥、邮箱、界面设置、**以及你的音乐文件**

本模块只动数据库与快照目录，**永远不碰音乐文件**。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ..logging_setup import get_logger
from ..storage.db import Database

log = get_logger("maintenance")

#: 会被清空的表（**不含 preferences / schema_version** —— 设置要留着）
PURGE_TABLES: tuple[str, ...] = (
    "llm_calls",
    "changes",
    "items",
    "groups",
    "runs",
    "cache_fingerprint",
    "cache_mb",
    "cache_llm",
)


@dataclass
class PurgeResult:
    rows: dict[str, int]
    snapshots_removed: int = 0
    snapshots_bytes: int = 0

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    def summary(self) -> str:
        parts = [f"{name} {count}" for name, count in self.rows.items() if count]
        text = "已清空：" + ("、".join(parts) if parts else "（本来就没有数据）")
        if self.snapshots_removed:
            size_mb = self.snapshots_bytes / 1024 / 1024
            text += f"；快照 {self.snapshots_removed} 份（{size_mb:.1f} MB）"
        return text


def purge_snapshots(snapshot_root: str | Path) -> tuple[int, int]:
    """删掉快照目录里的内容，返回 (文件数, 字节数)。

    ⚠️ 删掉快照后，对应批次的写入**就不能再撤销**了 —— 所以界面上必须先确认。
    """
    root = Path(snapshot_root)
    if not root.is_dir():
        return 0, 0
    files = 0
    size = 0
    for child in root.iterdir():
        if child.is_dir():
            for item in child.rglob("*"):
                if item.is_file():
                    files += 1
                    size += item.stat().st_size
            shutil.rmtree(child, ignore_errors=True)
        elif child.is_file():
            files += 1
            size += child.stat().st_size
            child.unlink(missing_ok=True)
    return files, size


def purge_analysis(db: Database, *, snapshot_root: str | Path = "") -> PurgeResult:
    """清空全部分析数据（结果 / 缓存 / 快照）。**设置与密钥保留。**"""
    rows: dict[str, int] = {}
    for table in PURGE_TABLES:
        try:
            count = int(db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:  # noqa: BLE001 - 表可能不存在（老库）
            continue
        if count:
            db.conn.execute(f"DELETE FROM {table}")
        rows[table] = count
    db.conn.commit()

    files, size = purge_snapshots(snapshot_root) if snapshot_root else (0, 0)
    result = PurgeResult(rows=rows, snapshots_removed=files, snapshots_bytes=size)
    log.info("已清空分析数据：%s", result.summary())
    return result


def data_usage(db: Database, *, snapshot_root: str | Path = "") -> dict[str, int]:
    """数据占用（给设置页显示用）。"""
    usage = {"db_bytes": 0, "snapshots_bytes": 0, "snapshot_files": 0, "runs": 0, "items": 0}
    try:
        usage["db_bytes"] = Path(db.path).stat().st_size
    except OSError:
        pass
    for key, table in (("runs", "runs"), ("items", "items")):
        try:
            usage[key] = int(db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:  # noqa: BLE001
            pass
    root = Path(snapshot_root) if snapshot_root else None
    if root and root.is_dir():
        for item in root.rglob("*"):
            if item.is_file():
                usage["snapshot_files"] += 1
                usage["snapshots_bytes"] += item.stat().st_size
    return usage
