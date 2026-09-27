"""回滚 —— 把写过的标签撤回原样。

两条路径：
    tags 模式：用快照里的**原始标签**整体写回（先删多出来的键，再写回原键）
    full 模式：直接把快照的整文件复制回去（逐字节还原）

回滚前会先检查文件是否被**外部改过**（sha256 与写入后不一致）：
若改过则拒绝回滚，免得把用户后来的修改覆盖掉。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import integrity, tagreader
from ..adapters.picard_headless import HeadlessPicard, PicardUnavailable
from ..adapters.snapshot import MODE_FULL, SnapshotStore, sha256_file
from ..core.models import SnapshotRef
from ..core.writeplan import ALLOWED_FIELDS
from ..logging_setup import get_logger
from ..storage.db import Database

log = get_logger("rollback")


@dataclass
class RollbackStats:
    total: int = 0
    rolled_back: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def rollback_run(
    db: Database,
    run_id: str,
    *,
    snapshot_root: str | Path,
    picard_config_file: str | Path | None = None,
    only: list[int] | None = None,
    change_ids: list[int] | None = None,
    batch_id: str = "",
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> RollbackStats:
    say = progress or (lambda _m: None)
    stats = RollbackStats()
    if batch_id:
        # 按批次回滚（界面上的「撤销上一批写入」走这条路）
        changes = db.iter_batch_changes(batch_id, only_applied=True)
    else:
        changes = [c for c in db.iter_changes(run_id, only_applied=True)
                   if (not only or int(c["item_id"]) in set(only))
                   and (not change_ids or int(c["id"]) in set(change_ids))]
    stats.total = len(changes)
    if not changes:
        say("没有需要回滚的条目。")
        return stats

    store = SnapshotStore(snapshot_root, mode=MODE_FULL)  # mode 由每条快照自己决定
    ctx: HeadlessPicard | None = None

    for change in changes:
        path = Path(change["path"])
        name = path.name
        ref_data = _loads(change.get("snapshot_ref"))
        if not ref_data:
            stats.failed += 1
            stats.errors.append((name, "缺少快照引用，无法回滚"))
            say(f"  {name}  ❌ 缺少快照引用")
            continue
        ref = SnapshotRef(**ref_data)
        ref.after_sha = change.get("after_sha") or ref.after_sha

        if not path.is_file():
            stats.failed += 1
            stats.errors.append((name, "文件不存在"))
            say(f"  {name}  ❌ 文件不存在")
            continue

        # 外部改动保护
        if ref.after_sha and not force and sha256_file(path) != ref.after_sha:
            stats.skipped += 1
            stats.errors.append((name, "文件在写入后又被外部改过，已跳过（用 --force 强制）"))
            say(f"  {name}  ⏭ 写入后又被改过，跳过回滚")
            continue

        try:
            if ref.mode == MODE_FULL:
                store.restore_bytes(ref)
            else:
                if ctx is None:
                    import os

                    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
                    ctx = HeadlessPicard(picard_config_file)
                    ctx.setup()
                store.restore_tags(ref, ctx)

            # 语义校验：回滚后标签应与快照一致
            expected = _snapshot_tags(store, ref)
            current = tagreader.read_tags(path).tags
            mismatch = [
                f for f in ALLOWED_FIELDS
                if expected.value_of(f) and current.value_of(f) != expected.value_of(f)
            ]
            if mismatch:
                stats.failed += 1
                stats.errors.append((name, f"回滚后校验不一致：{', '.join(mismatch)}"))
                say(f"  {name}  ❌ 回滚后校验不一致：{', '.join(mismatch)}")
                continue

            # 完整性校验：音频与内嵌图片必须与写入前一致
            if ref.audio_sha:
                after_digest = integrity.digest(path)
                if (after_digest.audio_sha != ref.audio_sha
                        or after_digest.pictures_sha != ref.pictures_sha
                        or after_digest.audio_len != ref.audio_len):
                    stats.failed += 1
                    stats.errors.append((name, "回滚后音频/图片与写入前不一致"))
                    say(f"  {name}  ❌ 回滚后音频/图片与写入前不一致")
                    continue

            db.mark_change_rolled_back(int(change["id"]))
            db.update_item(
                int(change["item_id"]),
                write_status="pending",
                write_error=None,
                after_sha=None,
                written_at=None,
            )
            stats.rolled_back += 1
            say(f"  {name}  ↩️ 已回滚")
        except PicardUnavailable as exc:
            stats.failed += 1
            stats.errors.append((name, str(exc)))
            say(f"  {name}  ❌ {exc}")
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"[:200]
            stats.failed += 1
            stats.errors.append((name, message))
            say(f"  {name}  ❌ {message}")

    return stats


def _loads(raw: object) -> dict:
    import json

    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _snapshot_tags(store: SnapshotStore, ref: SnapshotRef):
    """快照里记录的\"标签视图\"（用于回滚后的语义校验）。"""
    from ..core.models import Tags

    if ref.mode == MODE_FULL:
        return tagreader.read_tags(ref.blob).tags
    try:
        data = store.load(ref)
    except FileNotFoundError:
        return Tags()
    return Tags(**(data.get("tags") or {}))
