"""扫描：列文件 + 读标签 + 建 run。不联网、不动文件。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..adapters import tagreader
from ..logging_setup import get_logger
from ..storage.db import Database

log = get_logger("scan")


@dataclass
class ScanResult:
    run_id: str
    music_root: str
    found: int
    inserted: int
    skipped_unsupported: int = 0


def scan(
    db: Database,
    music_root: str,
    *,
    params: dict,
    limit: int = 0,
    resume: bool = True,
) -> ScanResult:
    """建立一次 run。若同一目录已有 running 的 run 且 resume=True，则复用它（续跑语义）。"""
    root = str(Path(music_root).expanduser().resolve())
    if not Path(root).is_dir():
        raise FileNotFoundError(f"音乐库目录不存在：{root}")

    paths = tagreader.iter_audio_files(root, limit=limit)

    run_id = db.find_resumable_run(root) if resume else None
    resumed = run_id is not None
    if run_id is None:
        run_id = db.create_run(root, params)

    inserted = db.insert_items(run_id, [str(p) for p in paths])

    # 读标签 + 文件属性（本地、快、不联网）
    for row in db.iter_items(run_id):
        if row["tags_json"]:
            continue
        read = tagreader.read_tags(row["path"])
        if read.error:
            db.update_item(row["id"], error=read.error, fp_status="error", fp_error=read.error)
            continue
        db.update_item(
            row["id"],
            size=read.size,
            mtime=read.mtime,
            ext=Path(read.path).suffix.lower(),
            tags_json=read.tags.model_dump(),
        )

    log.info("%s：发现 %d 个文件", "续跑" if resumed else "新建", len(paths))
    return ScanResult(run_id=run_id, music_root=root, found=len(paths), inserted=inserted)
