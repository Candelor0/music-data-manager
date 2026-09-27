"""快照：写入前的"存档"，回滚的依据。

两种模式：
    tags  默认 —— 只存原标签（含 Picard 的全部原始键值），约占文件大小的 1%
    full  安全模式 —— 整文件字节备份，逐字节还原

**回滚完整性靠 sha256 判定**（S9）：回滚后必须与写入前逐字节一致。
还原之前会先校验文件是否被外部改动过 —— 若已改过则拒绝回滚并报错，
免得把用户后来的修改覆盖掉。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.models import SnapshotRef, Tags
from ..logging_setup import get_logger

log = get_logger("snapshot")

MODE_TAGS = "tags"
MODE_FULL = "full"

_CHUNK = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    """流式 sha256（大文件不占内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SnapshotStore:
    def __init__(self, root: str | Path, *, mode: str = MODE_TAGS) -> None:
        if mode not in (MODE_TAGS, MODE_FULL):
            raise ValueError(f"未知快照模式：{mode}")
        self.root = Path(root)
        self.mode = mode
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "tags").mkdir(exist_ok=True)
        (self.root / "files").mkdir(exist_ok=True)

    # ── 建快照 ──────────────────────────────────────────────
    def create(
        self,
        item_id: int,
        path: str | Path,
        *,
        raw_tags: dict[str, list[str]] | None = None,
        tags: Tags | None = None,
    ) -> SnapshotRef:
        """建立快照。**必须在任何写入动作之前调用。**"""
        src = Path(path)
        stat = src.stat()
        before_sha = sha256_file(src)
        ref = SnapshotRef(
            item_id=item_id,
            mode=self.mode,
            path=str(src),
            before_sha=before_sha,
            size=int(stat.st_size),
            mtime=float(stat.st_mtime),
            created_at=_now(),
        )

        if self.mode == MODE_FULL:
            blob = self.root / "files" / f"{item_id}{src.suffix}"
            shutil.copy2(src, blob)
        else:
            blob = self.root / "tags" / f"{item_id}.json"
            blob.write_text(
                json.dumps(
                    {
                        "path": str(src),
                        "size": ref.size,
                        "mtime": ref.mtime,
                        "before_sha": before_sha,
                        "created_at": ref.created_at,
                        "raw_tags": raw_tags or {},
                        "tags": (tags or Tags()).model_dump(),
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        ref.blob = str(blob)
        log.debug("快照已建 item=%s mode=%s sha=%s", item_id, self.mode, before_sha[:12])
        return ref

    # ── 读取 ────────────────────────────────────────────────
    def load(self, ref: SnapshotRef) -> dict[str, Any]:
        blob = Path(ref.blob)
        if not blob.is_file():
            raise FileNotFoundError(f"快照不存在：{blob}")
        if ref.mode == MODE_FULL:
            return {"mode": MODE_FULL, "blob": str(blob)}
        return json.loads(blob.read_text(encoding="utf-8"))

    def is_intact(self, ref: SnapshotRef) -> bool:
        """快照文件本身是否可用。"""
        blob = Path(ref.blob)
        if not blob.is_file():
            return False
        if ref.mode == MODE_FULL:
            return True
        try:
            data = json.loads(blob.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        return bool(data.get("before_sha"))

    # ── 外部改动检测 ────────────────────────────────────────
    def file_changed_since(self, ref: SnapshotRef) -> bool:
        """文件在写入后是否又被外部改过。

        回滚前必须检查：如果用户后来又用别的工具改过标签，
        直接回滚会覆盖掉他后来的修改 —— 所以宁可拒绝回滚。
        """
        path = Path(ref.path)
        if not path.is_file():
            return True
        return sha256_file(path) != ref.after_sha if ref.after_sha else False

    # ── 还原 ────────────────────────────────────────────────
    def restore_bytes(self, ref: SnapshotRef) -> None:
        """full 模式：整文件复制回去（逐字节还原，S9）。"""
        if ref.mode != MODE_FULL:
            raise ValueError("restore_bytes 只适用于 full 模式")
        shutil.copy2(ref.blob, ref.path)
        # copy2 已带 mtime；再显式设一次以防文件系统差异
        Path(ref.path).touch()
        import os

        os.utime(ref.path, (ref.mtime, ref.mtime))

    def restore_tags(self, ref: SnapshotRef, ctx: Any) -> None:
        """tags 模式：用 Picard 把原标签整体写回。

        注意是**整体替换**（先删掉多出来的键，再写回快照里的键），
        而不是逐字段改 —— 这样才可能做到"和原来一样"。
        """
        data = self.load(ref)
        raw: dict[str, list[str]] = data.get("raw_tags") or {}
        path = ref.path

        f = ctx.open(path)
        current_keys = list(f.metadata.keys())
        for key in current_keys:
            if key not in raw:
                try:
                    del f.metadata[key]
                except Exception:  # noqa: BLE001
                    pass
        for key, values in raw.items():
            f.metadata[key] = values
        ctx.save(f)
        import os

        os.utime(path, (ref.mtime, ref.mtime))
