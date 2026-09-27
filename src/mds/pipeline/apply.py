"""执行写入 —— **本项目第一次真正修改用户的文件**。

单条流程（每一步都可中断而不损坏原文件）：

    1. 取计划，不允许写 → 跳过
    2. 计算 before_sha，建快照（原标签 / 或整文件）
    3. 复制一份到临时文件（同目录，保证 os.replace 是同一文件系统内的原子操作）
    4. **在副本上**写标签 + 用 mutagen 读回校验
    5. 写一条 \"applying\" 日志记录（崩溃后靠它对账）
    6. os.replace(tmp, 原文件)  ← 到这一刻之前，原文件一个字节都没被动过
    7. 计算 after_sha，把记录改为 applied，条目状态 → verified

失败处理：
    - 第 6 步之前任何失败 → 删临时文件，原文件完好，条目标 failed
    - 第 6 步之后崩溃 → 下次运行时靠 \"applying\" 日志对账（看文件里到底是新值还是旧值）
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import integrity, tagreader, tagwriter
from ..adapters.picard_headless import HeadlessPicard, PicardUnavailable
from ..adapters.snapshot import MODE_TAGS, SnapshotStore
from ..core.models import Tags, WritePlan
from ..core.writeplan import ALLOWED_FIELDS
from ..logging_setup import get_logger
from ..storage.db import Database, now_iso
from .common import item_view, load_json
from .plan import _load_plan

log = get_logger("apply")

TEMP_PREFIX = ".mds-tmp-"
REPLACE_RETRIES = 3


@dataclass
class ApplyOptions:
    snapshot_mode: str = MODE_TAGS
    preserve_mtime: bool = True
    verify_after_write: bool = True
    only: list[int] | None = None
    limit: int = 0
    dry_run: bool = False
    #: 写入前的"音频+图片"完整性校验。大库可临时关掉（代价是少了那道证据）
    skip_integrity: bool = False
    #: 批次号。留空则自动生成；「撤销上一批写入」按它回滚。
    batch_id: str = ""


@dataclass
class ApplyStats:
    total: int = 0
    written: int = 0
    verified: int = 0
    skipped: int = 0
    failed: int = 0
    reconciled: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    #: 本次写入的批次号 —— 界面拿它做「撤销上一批写入」
    batch_id: str = ""


def _temp_path(target: Path) -> Path:
    return target.parent / f"{TEMP_PREFIX}{uuid.uuid4().hex[:8]}-{target.name}"


def _replace_with_retry(src: Path, dst: Path) -> None:
    """os.replace 的原子替换；Windows 上文件被占用时会失败，做有限重试。"""
    last: Exception | None = None
    for attempt in range(1, REPLACE_RETRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:  # 被其它程序占用
            last = exc
            if attempt < REPLACE_RETRIES:
                import time

                time.sleep(1.0 * attempt)
    if last is not None:
        raise last


def _cleanup_temp(target: Path) -> None:
    for leftover in target.parent.glob(f"{TEMP_PREFIX}*-{target.name}"):
        try:
            leftover.unlink()
        except OSError:
            pass


def _apply_change_to_copy(
    ctx: HeadlessPicard, path: Path, target_tags: Tags
) -> tagwriter.WriteOutcome:
    """复制到临时文件 → 在副本上写 → 读回校验。**不碰原文件。**"""
    temp = _temp_path(path)
    shutil.copy2(path, temp)
    try:
        outcome = tagwriter.write_tags(ctx, temp, target_tags)
    except Exception as exc:  # noqa: BLE001
        temp.unlink(missing_ok=True)
        return tagwriter.WriteOutcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
    if not outcome.ok:
        temp.unlink(missing_ok=True)
        return outcome
    return outcome


def apply_run(
    db: Database,
    run_id: str,
    *,
    snapshot_root: str | Path,
    picard_config_file: str | Path | None = None,
    options: ApplyOptions | None = None,
    progress: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ApplyStats:
    opts = options or ApplyOptions()
    say = progress or (lambda _m: None)
    only_set = set(opts.only) if opts.only else None
    stats = ApplyStats()
    # 一次 apply 一个批次：撤销入口靠它精确定位"上一批"，不用时间戳猜
    # （同一秒可能有多个批次，猜就会撤错）
    batch_id = opts.batch_id or uuid.uuid4().hex[:12]
    stats.batch_id = batch_id
    rows = [dict(r) for r in db.iter_items(run_id)]
    stats.total = len(rows)

    store = SnapshotStore(snapshot_root, mode=opts.snapshot_mode)
    ctx: HeadlessPicard | None = None
    processed = 0

    try:
        for index, row in enumerate(rows, 1):
            if should_stop is not None and should_stop():
                say("已请求停止，保存进度后退出。")
                break
            item_id = int(row["id"])
            if only_set is not None and item_id not in only_set:
                continue
            if opts.limit and processed >= opts.limit:
                break

            plan = _load_plan(row)
            if plan is None or not plan.allowed:
                if row.get("write_status") not in ("skipped",):
                    db.set_plan(item_id, load_json(row.get("plan_json")), write_status="skipped")
                stats.skipped += 1
                continue
            if row.get("write_status") == "verified":
                stats.verified += 1
                continue

            processed += 1
            path = Path(row["path"])
            label = f"[{index}/{stats.total}] {row['name'][:50]}"

            if ctx is None:
                os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
                ctx = HeadlessPicard(picard_config_file)
                ctx.setup()

            try:
                _apply_one(db, ctx, store, run_id, row, plan, path, opts, stats, say, label, batch_id)
            except PicardUnavailable as exc:
                stats.failed += 1
                stats.errors.append((row["name"], str(exc)))
                db.update_item(item_id, write_status="failed", write_error=str(exc))
                say(f"  {label}  ❌ {exc}")
            except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮整批
                message = f"{type(exc).__name__}: {exc}"[:200]
                stats.failed += 1
                stats.errors.append((row["name"], message))
                db.update_item(item_id, write_status="failed", write_error=message)
                say(f"  {label}  ❌ {message}")
    finally:
        pass

    return stats


def _apply_one(
    db: Database,
    ctx: HeadlessPicard,
    store: SnapshotStore,
    run_id: str,
    row: dict,
    plan: WritePlan,
    path: Path,
    opts: ApplyOptions,
    stats: ApplyStats,
    say: Callable[[str], None],
    label: str,
    batch_id: str = "",
) -> None:
    item_id = int(row["id"])
    _cleanup_temp(path)  # 清掉上次崩溃可能留下的临时文件

    # 崩溃对账：上次已经写过但没落账
    open_change = db.find_open_change(item_id)
    if open_change is not None:
        _reconcile(db, ctx, open_change, plan, path, stats, say, label)

    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")

    target = plan.target or item_view(row).tags
    before_stat = path.stat()
    before_digest = integrity.digest(path)
    before_sha = before_digest.file_sha

    # 1) 快照（必须在任何写入动作之前）
    ref = store.create(
        item_id,
        path,
        raw_tags=tagwriter.read_raw_metadata(ctx, path),
        tags=tagreader.read_tags(path).tags,
    )
    ref.before_sha = before_sha
    ref.audio_sha = before_digest.audio_sha
    ref.audio_len = before_digest.audio_len
    ref.pictures_sha = before_digest.pictures_sha
    ref.n_pictures = before_digest.n_pictures
    db.update_item(
        item_id,
        snap_ref=ref.model_dump(mode="json"),
        before_sha=before_sha,
        write_status="snapshotted",
    )

    if opts.dry_run:
        say(f"  {label}  🔍 dry-run：快照已建，未写入")
        stats.skipped += 1
        return

    # 2) 在副本上写并校验
    outcome = _apply_change_to_copy(ctx, path, target)
    if not outcome.ok:
        db.update_item(item_id, write_status="failed", write_error=outcome.error)
        stats.failed += 1
        stats.errors.append((row["name"], outcome.error))
        say(f"  {label}  ❌ 副本写入失败：{outcome.error}")
        return
    temp = _find_temp(path)
    if temp is None:
        raise RuntimeError("临时文件丢失（副本写入后找不到）")

    # 2.5) 完整性校验：副本的音频与内嵌图片必须与原文件完全一致
    #      （FLAC 的 PADDING 会被重算 → 文件变小，但那不是数据，见 adapters/integrity.py）
    integrity_note = ""
    if not opts.skip_integrity:
        intact, integrity_note = integrity.compare(before_digest, integrity.digest(temp))
        if not intact:
            temp.unlink(missing_ok=True)
            db.update_item(item_id, write_status="failed",
                           write_error=f"完整性校验未通过：{integrity_note}")
            stats.failed += 1
            stats.errors.append((row["name"], integrity_note))
            say(f"  {label}  ❌ {integrity_note}（已中止，原文件未被修改）")
            return

    # 3) journal：先落一条 applying，再原子替换（崩溃后可对账）
    change_id = db.record_change(
        run_id=run_id,
        item_id=item_id,
        path=str(path),
        before_json={"raw_tags": (store.load(ref).get("raw_tags") or {}) if ref.mode == MODE_TAGS else {},
                     "tags": tagreader.read_tags(path).tags.model_dump()},
        after_json=target.model_dump(),
        snapshot_ref=ref.model_dump(mode="json"),
        before_sha=before_sha,
        after_sha="",
        status="applying",
        batch_id=batch_id,
    )

    try:
        _replace_with_retry(temp, path)
    except Exception as exc:  # noqa: BLE001
        temp.unlink(missing_ok=True)
        db.update_change(change_id, status="failed")
        db.update_item(item_id, write_status="failed", write_error=f"原子替换失败：{exc}"[:200])
        stats.failed += 1
        stats.errors.append((row["name"], f"原子替换失败：{type(exc).__name__}"))
        say(f"  {label}  ❌ 原子替换失败（原文件未受影响）")
        return

    # 4) 收尾：还原 mtime、算 after_sha、落账
    if opts.preserve_mtime:
        os.utime(path, (before_stat.st_atime, before_stat.st_mtime))
    after_sha = integrity.digest(path).file_sha
    db.update_change(change_id, status="applied", after_sha=after_sha)
    db.update_item(
        item_id,
        write_status="verified",
        after_sha=after_sha,
        written_at=now_iso(),
        write_error=None,
    )
    stats.written += 1
    stats.verified += 1
    changes_text = "; ".join(f"{c.field}={c.after}" for c in plan.changes)
    size_delta = path.stat().st_size - before_stat.st_size
    size_note = f"  大小{size_delta:+,}" if size_delta else ""
    say(f"  {label}  ✅ 已写入并校验{size_note}  {changes_text[:60]}")
    if integrity_note:
        log.debug("%s：%s", row["name"], integrity_note)


def _find_temp(path: Path) -> Path | None:
    found = sorted(path.parent.glob(f"{TEMP_PREFIX}*-{path.name}"))
    return found[0] if found else None


def _reconcile(
    db: Database,
    ctx: HeadlessPicard,
    change: dict,
    plan: WritePlan,
    path: Path,
    stats: ApplyStats,
    say: Callable[[str], None],
    label: str,
) -> None:
    """上次写到一半崩了的对账：判断文件里到底是新值还是旧值。"""
    current = tagreader.read_tags(path).tags if path.is_file() else Tags()
    target = plan.target or Tags()
    looks_written = all(
        not target.value_of(f) or current.value_of(f) == target.value_of(f)
        for f in ALLOWED_FIELDS
    )
    if looks_written:
        sha = integrity.digest(path).file_sha
        db.update_change(int(change["id"]), status="applied", after_sha=sha)
        db.update_item(int(change["item_id"]), write_status="verified", after_sha=sha)
        say(f"  {label}  🔧 对账：上次已写入，已补记")
    else:
        db.update_change(int(change["id"]), status="failed")
        db.update_item(int(change["item_id"]), write_status="failed",
                       write_error="上次写入未完成，已标记失败可重试")
        say(f"  {label}  🔧 对账：上次未写入，已标记失败")
    stats.reconciled += 1


def verify_run(db: Database, run_id: str, *, progress: Callable[[str], None] | None = None) -> dict:
    """重新读文件，核对已写入条目的标签是否与计划一致（S14）。"""
    say = progress or (lambda _m: None)
    ok = bad = 0
    details: list[str] = []
    for row in db.iter_items(run_id):
        row = dict(row)
        if row.get("write_status") != "verified":
            continue
        plan = _load_plan(row)
        if plan is None or plan.target is None:
            continue
        target = plan.target
        current = tagreader.read_tags(row["path"]).tags
        mismatched = [
            f for f in ALLOWED_FIELDS
            if target.value_of(f) and current.value_of(f) != target.value_of(f)
        ]
        if mismatched:
            bad += 1
            details.append(f"{row['name']}: {', '.join(mismatched)}")
        else:
            ok += 1
    say(f"校验完成：一致 {ok}｜不一致 {bad}")
    for line in details[:20]:
        say(f"  ❌ {line}")
    return {"ok": ok, "bad": bad, "details": details}
