"""命令行入口。

    mds doctor                      环境自检
    mds scan <dir> [--limit N]      扫描并建 run
    mds analyze <run_id>            指纹→AcoustID→MusicBrainz→AI→决策（可重复执行=续跑）
    mds run <音乐库>                一键：扫描 → 分组 → 分析 → 生成计划
    mds todo <run_id>              待办 4 组（🟢可写入 / 🟡需你选 / 🟠有冲突 / ⚪无需处理）
    mds choose <run_id>            按专辑裁决：看候选 / 选定（同一张专辑只选一次）
    mds group <run_id>              按专辑目录分组 + 同目录共识补空字段（本地，不联网）
    mds gui [run_id]                打开只读界面（看分组/共识/建议）
    mds report <run_id> [--md P] [--csv P]
    mds runs                        列出历史 run

**本版本全部命令都不修改任何音乐文件。**
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from . import __version__, config
from .config import load_settings
from .logging_setup import setup as setup_logging
from .pipeline.analyze import AnalyzeOptions, analyze
from .pipeline.choose import apply_album_choice, render_album_choices
from .pipeline.group import build_groups, render_groups
from .pipeline.plan import build_plans, render_decisions, render_plan
from .pipeline.report import write_csv, write_markdown
from .pipeline.run_all import RunAllOptions, prepare, process, render_todo
from .pipeline.scan import scan
from .storage.db import Database

EXIT_OK, EXIT_FATAL, EXIT_PARTIAL = 0, 1, 2


def _enable_utf8_console() -> None:
    """把 Windows 控制台切成 UTF-8。

    背景（Windows 实测）：简体中文 Windows 的控制台默认代码页是 GBK，
    Python 以 UTF-8 输出中文时会被它误读 —— 表现为每个汉字之间插一个空格，
    看起来像乱码（数据没错，只是显示错）。

    这里把控制台代码页改成 65001（UTF-8），并把 stdout/stderr 也固定为 UTF-8。
    非 Windows 平台直接跳过。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001 - 没有控制台（如重定向到文件）时忽略
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _picard_config_file():
    """延迟导入：只有真正要读写标签的命令才需要 PyQt5。"""
    from .adapters.picard_headless import make_config_file

    return make_config_file(config.data_dir())


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# ── doctor ────────────────────────────────────────────────
def _check_http(url: str, *, method: str = "GET", **kwargs) -> tuple[bool, str]:
    """只判断"能不能连上"，不判断业务是否成功（4xx 也算连通）。"""
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.request(method, url, **kwargs)
        return True, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}"


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings()
    ok = True

    _print("=" * 72)
    _print(f"{config.APP_DIR_NAME} 环境自检 · v{__version__}")
    _print("=" * 72)

    # 1. Python
    py_ok = sys.version_info[:2] == (3, 12)
    _print(f"[{'✅' if py_ok else '❌'}] Python {sys.version.split()[0]}（要求 3.12）")
    ok &= py_ok

    # 2. fpcalc
    exe = config.fpcalc_path()
    from .adapters import fpcalc as fpcalc_adapter

    ver = fpcalc_adapter.version(exe)
    fp_ok = exe.is_file() and "不可用" not in ver
    _print(f"[{'✅' if fp_ok else '❌'}] fpcalc：{exe}")
    if fp_ok:
        _print(f"      {ver}")
    else:
        _print("      怎么办：把 fpcalc 可执行文件放到 tools/ 目录"
               "（Windows 版从 Chromaprint 官方 release 下载，无需编译）")
    ok &= fp_ok

    # 3. 密钥（只报有无，绝不打印内容）
    _print(f"[{'✅' if settings.has_acoustid else '❌'}] AcoustID Key：{'已配置' if settings.has_acoustid else '缺失'}")
    _print(f"[{'✅' if settings.has_deepseek else '⚠️'}] DeepSeek Key：{'已配置' if settings.has_deepseek else '缺失（不影响扫描与候选，只影响 AI 消歧）'}")
    _print(f"[{'✅' if settings.has_user_agent else '❌'}] MusicBrainz User-Agent：{'已配置' if settings.has_user_agent else '缺失（必须是 应用名/版本 ( 邮箱 )）'}")
    _print(f"      配置文件：{settings.env_file or '（未找到 .env，将只读系统凭据库）'}")
    ok &= settings.has_acoustid and settings.has_user_agent

    # 4. 数据目录
    data = config.data_dir()
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _print(f"[✅] 数据目录可写：{data}")
    except OSError as exc:
        _print(f"[❌] 数据目录不可写：{data}（{type(exc).__name__}）")
        ok = False

    # 5. 数据库
    try:
        with Database(config.db_path()) as db:
            db.migrate()
        _print(f"[✅] 数据库可用：{config.db_path()}")
    except Exception as exc:  # noqa: BLE001
        _print(f"[❌] 数据库不可用：{type(exc).__name__}: {exc}")
        ok = False

    # 6. 联网 + 密钥有效性（共用 adapters/health.py，与界面设置页的「测试连接」同一套）
    #    只请求一次，不做压力测试
    from .adapters.health import check_services

    _print("      密钥有效性（AcoustID 用假指纹探测；DeepSeek 用不花钱的 /models）：")
    for check in check_services(settings):
        _print(f"      {check.line()}")
        if check.hint and not check.ok:
            _print(f"        怎么办：{check.hint}")
        if check.name != "DeepSeek":
            ok &= check.ok

    _print()
    _print("全部通过。" if ok else "存在问题，见上面标 ❌ 的项。")
    return EXIT_OK if ok else EXIT_FATAL


# ── scan ──────────────────────────────────────────────────
def cmd_scan(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())
    root = args.directory or settings.music_library_path
    if not root:
        _print("❌ 请给出音乐库目录，或在 .env 配置 MUSIC_LIBRARY_PATH")
        return EXIT_FATAL

    with Database(config.db_path()) as db:
        db.migrate()
        result = scan(
            db, root,
            params={
                "limit": args.limit,
                "acoustid_rate": settings.acoustid_rate,
                "musicbrainz_rate": settings.musicbrainz_rate,
                "model": settings.model,
                "app_version": __version__,
            },
            limit=args.limit,
            resume=not args.new,
        )
        counts = db.item_counts(result.run_id)

    _print(f"run_id = {result.run_id}")
    _print(f"音乐库 = {result.music_root}")
    _print(f"发现文件 {result.found} 个（新入库 {result.inserted}）")
    _print(f"当前状态：总数 {counts['total']}")
    _print()
    _print("下一步：")
    _print(f"  mds analyze {result.run_id}")
    return EXIT_OK


# ── analyze ───────────────────────────────────────────────
def cmd_analyze(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())

    options = AnalyzeOptions(
        mode=args.mode,
        use_llm=not args.no_llm,
        retry_errors=args.retry_errors,
        refresh_decisions=args.refresh_decisions,
        assume_no_album_tag=args.assume_no_album_tag,
        max_candidates=args.max_candidates,
        low_evidence_candidates=args.low_evidence_candidates,
        budget_per_100_tracks=args.budget
        if args.budget is not None
        else settings.budget_per_100_tracks,
        fingerprint_length=settings.fingerprint_length,
    )

    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        _print(f"开始分析 run {args.run_id}（mode={options.mode}）")
        _print(f"限流：AcoustID {settings.acoustid_rate:g}/秒｜MusicBrainz {settings.musicbrainz_rate:g}/秒")
        _print("提示：随时可以按 Ctrl-C 中断；再次运行同一命令即为续跑。")
        _print()
        try:
            stats = analyze(db, settings, args.run_id, options=options, progress=_print)
        except KeyboardInterrupt:
            _print()
            _print("已中断。进度已保存 —— 再次运行同一命令即可续跑。")
            return EXIT_PARTIAL

        _print()
        _print("=" * 72)
        _print("分析完成")
        _print("=" * 72)
        _print(f"  处理条目        {stats.processed} / {stats.total}（已跳过完成的 {stats.skipped_done}）")
        _print(f"  指纹命中        {stats.fp_ok}（缓存 {stats.fp_cache_hits}）")
        _print(f"  AcoustID 命中   {stats.ac_ok}（无结果 {stats.ac_no_result}）")
        _print(f"  有候选          {stats.cand_ok}（MusicBrainz 缓存 {stats.mb_cache_hits}）")
        _print(f"  AI 成功         {stats.llm_ok}（缓存 {stats.llm_cache_hits}｜"
               f"格式错误 {stats.llm_schema_error}｜预算跳过 {stats.llm_skipped}）")
        _print(f"  失败            {stats.errors}")
        cost_cny = stats.cost_usd * 7.1
        _print(f"  云端成本        ¥{cost_cny:.4f}（tokens：入 {stats.prompt_tokens} / 出 {stats.completion_tokens}）")
        _print(f"  耗时            {stats.elapsed_sec} 秒")
        _print()
        _print("  分类：")
        for verdict, count in sorted(stats.verdicts.items(), key=lambda kv: -kv[1]):
            _print(f"    {verdict:<28} {count}")
        _print()
        _print(f"下一步：mds report {args.run_id} --md 建议报告.md")

    return EXIT_OK


# ── plan（只读：只生成"打算改什么"）─────────────────────────
def cmd_plan(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())

    # 人工选择：item id → 选择
    choices: dict[int, str] = {}
    for item_id in args.adopt_ai or []:
        choices[int(item_id)] = "adopted_ai"
    for item_id in args.keep_existing or []:
        choices[int(item_id)] = "kept_existing"
    for item_id in args.skip or []:
        choices[int(item_id)] = "skipped"
    for spec in args.pick or []:
        try:
            choices[int(spec[0])] = f"picked:{int(spec[1])}"
        except (ValueError, IndexError):
            _print(f"❌ --pick 参数格式应为：--pick <条目编号> <候选序号>，收到 {spec}")
            return EXIT_FATAL

    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL

        stats = build_plans(
            db,
            args.run_id,
            choices=choices,
            assume_no_album_tag=args.assume_no_album_tag,
            refresh=args.refresh or bool(choices),
            only=args.only,
        )

        _print("=" * 72)
        _print(f"写入计划 · run {args.run_id}")
        _print("=" * 72)
        _print(f"  条目总数            {stats.total}")
        _print(f"  已有决策            {stats.with_decision}")
        _print(f"  可以写入            {stats.allowed}（其中含专辑改动 {stats.touches_album}）")
        _print(f"  不写入              {stats.not_allowed}")
        if stats.by_field:
            _print("  计划改动的字段：")
            for field, count in stats.by_field.items():
                _print(f"    {field:<10} {count}")
        if stats.by_source:
            _print("  来源：")
            for source, count in stats.by_source.items():
                _print(f"    {source:<16} {count}")
        if stats.by_reason:
            _print("  不写入的原因：")
            for reason, count in stats.by_reason.items():
                _print(f"    {reason:<34} {count}")

        if args.decisions:
            _print()
            _print("\n".join(render_decisions(db, args.run_id)))

        if args.show:
            _print()
            lines = render_plan(
                db, args.run_id, only_allowed=args.only_allowed, limit=args.limit, only=args.only
            )
            _print("\n".join(lines) if lines else "（没有可显示的计划）")

        _print()
        _print("常用操作：")
        _print(f"  mds plan {args.run_id} --decisions                 # 看哪些条目需要你决定")
        _print(f"  mds plan {args.run_id} --adopt-ai 12 15 --refresh   # 采纳 AI 对 #12 #15 的专辑判断")
        _print(f"  mds plan {args.run_id} --skip 20 --refresh          # 跳过 #20")
        _print(f"  mds plan {args.run_id} --pick 30 2 --refresh        # #30 选第 3 个候选（从 0 数）")
        _print()
        _print("⚠️ 本命令**只读**：没有建快照、没有修改任何音乐文件。")
    return EXIT_OK


# ── apply（真正写入；默认 dry-run）─────────────────────────
def cmd_apply(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())

    from .adapters.snapshot import MODE_FULL, MODE_TAGS
    from .pipeline.apply import ApplyOptions, apply_run

    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL

        counts = db.plan_counts(args.run_id)
        pending = db.pending_write_count(args.run_id)
        if pending == 0:
            if counts["verified"]:
                _print(f"这条 run 的计划已全部写完（{counts['verified']} 条）。")
                _print("如需重写，先回滚：mds rollback <run_id> --yes，或重新生成计划。")
            else:
                _print("⚠️ 这条 run 还没有可写入的计划。请先运行：")
                _print(f"    mds plan {args.run_id}")
            return EXIT_FATAL

        if not args.yes:
            from .pipeline.plan import render_plan

            lines = render_plan(db, args.run_id, only_allowed=True, limit=args.limit,
                                only_pending=True)
            _print("\n".join(lines))
            _print("=" * 72)
            _print(f"即将写入 {pending} 条（已完成 {counts['verified']} 条不会重复处理）。")
            _print("本条命令**默认不写入**。确认无误后，加上 --yes 重新运行：")
            _print(f"    mds apply {args.run_id} --yes")
            _print("=" * 72)
            return EXIT_OK

        opts = ApplyOptions(
            snapshot_mode=MODE_FULL if args.snapshot_mode == "full" else MODE_TAGS,
            preserve_mtime=not args.no_preserve_mtime,
            only=args.only,
            limit=args.limit,
        )
        _print(f"开始写入 （快照模式：{opts.snapshot_mode}）")
        _print(f"快照位置：{config.snapshot_dir()}")
        _print("提示：可随时 Ctrl-C 中断；原文件在原子替换之前不会被修改。")
        _print()
        try:
            stats = apply_run(
                db,
                args.run_id,
                snapshot_root=config.snapshot_dir(),
                picard_config_file=_picard_config_file(),
                options=opts,
                progress=_print,
            )
        except KeyboardInterrupt:
            _print()
            _print("已中断。原文件未被修改（原子替换之前不会动原文件）。")
            return EXIT_PARTIAL

        _print()
        _print("=" * 72)
        _print("写入完成")
        _print("=" * 72)
        _print(f"  已写入并校验   {stats.verified}")
        _print(f"  跳过          {stats.skipped}")
        _print(f"  失败          {stats.failed}")
        _print(f"  批次号        {stats.batch_id}")
        _print("  撤销这一批：mds rollback <run_id> --batch " + stats.batch_id + " --yes")
        for name, error in stats.errors[:10]:
            _print(f"    ❌ {name}: {error}")
        _print()
        _print("下一步：mds verify <run_id> 或 mds rollback <run_id>")
    return EXIT_OK if stats.failed == 0 else EXIT_PARTIAL


# ── verify ───────────────────────────────────────────────
def cmd_verify(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())
    from .pipeline.apply import verify_run

    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        result = verify_run(db, args.run_id, progress=_print)
    return EXIT_OK if result["bad"] == 0 else EXIT_PARTIAL


# ── rollback ─────────────────────────────────────────────
def cmd_rollback(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())
    from .pipeline.rollback import rollback_run

    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        batch_id = args.batch or ""
        if args.batch == "latest":
            batch_id = db.latest_batch_id(args.run_id)
            if not batch_id:
                _print("没有可撤销的批次。")
                return EXIT_OK
            _print(f"最近一批：{batch_id}")
        if not args.yes:
            changes = (
                db.iter_batch_changes(batch_id)
                if batch_id
                else db.iter_changes(args.run_id, only_applied=True)
            )
            _print(f"将回滚 {len(changes)} 条"
                   + (f"（批次 {batch_id}）" if batch_id else "")
                   + "。本条命令**默认不执行**。")
            for change in changes[:20]:
                _print(f"  #{change['id']}  {Path(change['path']).name}")
            _print()
            _print("确认后加上 --yes：")
            _print(f"    mds rollback {args.run_id} --batch {batch_id} --yes"
                   if batch_id else f"    mds rollback {args.run_id} --yes")
            return EXIT_OK

        stats = rollback_run(
            db,
            args.run_id,
            snapshot_root=config.snapshot_dir(),
            picard_config_file=_picard_config_file(),
            only=args.only,
            batch_id=batch_id,
            force=args.force,
            progress=_print,
        )
    _print()
    _print(f"已回滚 {stats.rolled_back}｜跳过 {stats.skipped}｜失败 {stats.failed}")
    for name, error in stats.errors[:10]:
        _print(f"  ❌ {name}: {error}")
    return EXIT_OK if stats.failed == 0 else EXIT_PARTIAL


# ── changes ──────────────────────────────────────────────
def cmd_changes(args: argparse.Namespace) -> int:
    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        rows = db.iter_changes(args.run_id)
    if not rows:
        _print("（还没有任何变更记录）")
        return EXIT_OK
    for row in rows:
        before = load_json_str(row.get("before_json"))
        after = load_json_str(row.get("after_json"))
        tags_before = before.get("tags", {}) if isinstance(before, dict) else {}
        diff = []
        for field in ("title", "artist", "album", "date", "genre"):
            old = (tags_before or {}).get(field, "")
            new = (after or {}).get(field, "")
            if new and old != new:
                diff.append(f"{field}: {old or '（空）'} → {new}")
        _print(f"#{row['id']}  [{row['status']}]  {Path(row['path']).name}")
        if diff:
            _print(f"      {'; '.join(diff)[:110]}")
        _print(f"      before_sha={str(row.get('before_sha') or '')[:12]}… "
               f"after_sha={str(row.get('after_sha') or '')[:12]}…"
               + (f"  回滚于 {row['rolled_back_at']}" if row.get("rolled_back_at") else ""))
    return EXIT_OK


def load_json_str(raw: object) -> dict:
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


# ── run / todo / choose───────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    """一键流程：扫描 → 分组 → 分析 → 计划。**不写入任何音乐文件。**"""
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())

    with Database(config.db_path()) as db:
        db.migrate()
        opts = RunAllOptions(
            library=args.directory,
            limit=args.limit,
            use_llm=not args.no_llm,
            assume_no_album_tag=args.assume_no_album_tag,
        )
        # 第一段：扫描 + 分组 + 预估（本地、免费）
        prepared = prepare(db, settings, options=opts, progress=_print)
        if prepared.stopped:
            return EXIT_PARTIAL

        # 费用确认：默认关闭；开了就必须确认（与界面同一个设置项）
        if settings.cost_estimate_confirm and prepared.pending_before > 0 and not args.yes:
            _print()
            _print("=" * 72)
            _print(f"将处理 {prepared.pending_before} 首，预估云端花费 ¥{prepared.estimated_cost:.2f}。")
            _print("你打开了「开始前显示费用预估」，所以这里先停下等你确认：")
            _print(f"    mds run {args.directory} --yes")
            _print("=" * 72)
            return EXIT_OK

        result = process(db, settings, prepared.run_id, options=opts, progress=_print)
        result.scanned = prepared.scanned
        result.inserted = prepared.inserted
        result.pending_before = prepared.pending_before
        result.estimated_cost = prepared.estimated_cost
        lines = render_todo(db, result.run_id)

    _print()
    _print("=" * 72)
    _print(f"run_id = {result.run_id}")
    _print(f"新入库 {result.inserted} 首｜本次处理 {result.processed} 首"
           f"｜跳过已完成 {result.skipped_done} 首｜失败 {result.errors} 首")
    _print(f"实际花费 ¥{result.cost_usd * 7.1:.4f}（按 1 美元 ≈ 7.1 元估）"
           f"｜预估 ¥{result.estimated_cost:.2f}")
    _print(f"用时 {result.elapsed_sec} 秒")
    _print()
    _print("\n".join(lines))
    _print()
    _print("提醒：本命令没有修改任何音乐文件（写入要单独执行 mds apply）。")
    return EXIT_OK if not result.errors else EXIT_PARTIAL


def cmd_todo(args: argparse.Namespace) -> int:
    """待办 4 组：让人只看需要动手指的事，不去看几百行文件列表。"""
    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        lines = render_todo(db, args.run_id, limit=args.limit)
    _print("\n".join(lines))
    return EXIT_OK


def cmd_choose(args: argparse.Namespace) -> int:
    """按专辑裁决：同一张专辑只选一次（同目录 N 首一次搞定）。"""
    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL

        if args.folder is None:
            lines = render_album_choices(db, args.run_id, limit=args.limit)
            _print("\n".join(lines))
            return EXIT_OK

        if args.candidate is None:
            _print("❌ 选定候选时要带 --candidate <序号>（序号见不带参数时的列表）")
            return EXIT_FATAL

        from .pipeline.choose import album_choices

        choice = next((c for c in album_choices(db, args.run_id) if c.folder == args.folder), None)
        if choice is None:
            _print(f"❌ 这个目录没有待裁决的条目：{args.folder}")
            return EXIT_FATAL
        picked = choice.candidate_by_index(args.candidate)
        if picked is None:
            _print(f"❌ 候选序号超出范围（共 {len(choice.candidates)} 个）")
            return EXIT_FATAL

        stats = apply_album_choice(db, args.run_id, args.folder, picked.release_mbid)
        lines = render_todo(db, args.run_id)

    _print(f"已为「{choice.title}」选定：{picked.album or picked.release_mbid}")
    _print(f"  覆盖 {stats.n_chosen}/{stats.n_total} 首"
           + (f"；{stats.n_unmatched} 首的候选列表里没有这个发行版，仍保留待决"
              if stats.n_unmatched else ""))
    _print()
    _print("\n".join(lines))
    return EXIT_OK


# ── group ─────────────────────────────────────────────────
def cmd_group(args: argparse.Namespace) -> int:
    """按专辑目录分组，算同目录共识，并用共识重算决策。

    完全本地：不联网、不调用模型、不修改任何音乐文件。
    """
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())
    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        stats = build_groups(
            db,
            args.run_id,
            min_files=args.min_files,
            min_ratio=args.min_ratio,
            recompute_decisions=not args.no_recompute,
        )
        lines = render_groups(db, args.run_id, limit=args.limit)

    if stats.total == 0:
        _print("这个 run 里还没有文件，先跑 mds scan。")
        return EXIT_OK

    label_of = {
        "album": "专辑", "albumartist": "专辑艺术家", "date": "年份",
        "genre": "流派", "discnumber": "碟号",
    }
    _print("按专辑目录分组（本地完成：未联网、未改动任何音乐文件）")
    _print()
    _print(f"目录 {stats.n_groups} 个：多文件 {stats.n_multi} 个"
           f"（含 {stats.files_in_multi} 首歌）、单文件 {stats.n_single} 个")
    if stats.with_consensus:
        _print(f"其中 {stats.with_consensus} 个目录有同目录共识：")
        for name, count in stats.consensus_fields.items():
            _print(f"  {label_of.get(name, name):<8} {count} 个目录")
    else:
        _print("没有形成同目录共识（目录多为单文件，或字段本身分歧大）")
    _print()
    _print(f"→ 用共识补全空字段 {stats.consensus_changes} 处")
    if stats.conflicts:
        _print(f"→ 发现 {stats.conflicts} 处「已有值与同目录共识不一致」——只提示，不会改动")
    _print()
    if lines:
        _print("── 各目录明细 ──")
        _print()
        for line in lines:
            _print(line)
        if args.limit and len(lines) < stats.n_groups:
            _print(f"（只显示了前 {args.limit} 个目录，去掉 --limit 看全部）")
    _print("提醒：本命令没有修改任何音乐文件。")
    return EXIT_OK


# ── gui ───────────────────────────────────────────────────
def cmd_gui(args: argparse.Namespace) -> int:
    """打开只读界面。本版本界面上没有任何「写入」按钮。"""
    try:
        from .ui.app import run_gui
    except ImportError as exc:  # PyQt5 没装（例如非 Windows 的精简环境）
        _print(f"❌ 界面组件不可用：{exc}")
        _print("   请在项目目录下执行：uv sync")
        return EXIT_FATAL

    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())
    return run_gui(run_id=args.run_id)


# ── report ────────────────────────────────────────────────
def cmd_report(args: argparse.Namespace) -> int:
    settings = load_settings()
    setup_logging(args.log_level, settings.secret_values())
    with Database(config.db_path()) as db:
        db.migrate()
        if db.get_run(args.run_id) is None:
            _print(f"❌ 找不到 run：{args.run_id}")
            return EXIT_FATAL
        counts = write_markdown(db, args.run_id, args.md, disagreements_only=args.disagreements_only)
        n_csv = write_csv(db, args.run_id, args.csv)
    _print(f"Markdown 报告：{args.md}")
    _print(f"CSV 报告：{args.csv}（{n_csv} 行）")
    for verdict, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        _print(f"  {verdict:<28} {count}")
    _print()
    _print("提醒：本阶段没有修改任何音乐文件。")
    return EXIT_OK


# ── runs ──────────────────────────────────────────────────
def cmd_runs(args: argparse.Namespace) -> int:
    with Database(config.db_path()) as db:
        db.migrate()
        rows = db.list_runs()
    if not rows:
        _print("（还没有任何 run）")
        return EXIT_OK
    for row in rows:
        _print(f"{row['id']}  {row['started_at']}  {row['status']:<8}  {row['music_root']}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mds", description=f"{config.APP_DIR_NAME} CLI")
    parser.add_argument("--version", action="version", version=f"mds {__version__}")
    parser.add_argument("--log-level", default="WARNING", help="日志等级（默认 WARNING）")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("scan", parents=[common], help="扫描音乐库并建立 run")
    p.add_argument("directory", nargs="?", help="音乐库目录（缺省用 .env 的 MUSIC_LIBRARY_PATH）")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件")
    p.add_argument("--new", action="store_true", help="强制新建 run，不复用未完成的 run")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("analyze", parents=[common], help="分析（可重复执行=续跑）")
    p.add_argument("run_id")
    p.add_argument("--mode", choices=["full", "no_album", "none"], default="full",
                   help="给模型的标签证据级别（默认 full）")
    p.add_argument("--no-llm", action="store_true", help="不调用云端模型（只到候选）")
    p.add_argument("--no-strip-disc", action="store_true",
                   help="不把专辑名里的「Disc N」拆到 discnumber 字段（默认会拆）")
    p.add_argument("--retry-errors", action="store_true", help="重试之前失败的条目")
    p.add_argument("--refresh-decisions", action="store_true",
                   help="重算决策（使用已缓存结果，不重复调用云端）")
    p.add_argument("--assume-no-album-tag", action="store_true",
                   help="验证用：忽略已有的专辑标签，模拟「标签缺失的音乐库」（用于回归验证）")
    p.add_argument("--max-candidates", type=int, default=None)
    p.add_argument("--low-evidence-candidates", type=int, default=None)
    p.add_argument("--budget", type=float, default=None, help="每 100 首的云端预算上限（元）")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("plan", parents=[common], help="生成写入计划（只读，不改文件）")
    p.add_argument("run_id")
    p.add_argument("--show", action="store_true", help="逐条打印计划内容")
    p.add_argument("--only-allowed", action="store_true", help="只显示会写入的条目")
    p.add_argument("--limit", type=int, default=0, help="--show 时最多显示多少条")
    p.add_argument("--refresh", action="store_true", help="已有计划也重算")
    p.add_argument("--decisions", action="store_true", help="列出需要你决定的条目（带编号）")
    p.add_argument("--adopt-ai", type=int, nargs="*", default=None,
                   metavar="ID", help="采纳 AI 的专辑判断（按条目编号）")
    p.add_argument("--keep-existing", type=int, nargs="*", default=None,
                   metavar="ID", help="保留现有专辑标签（默认行为，用于撤回采纳）")
    p.add_argument("--skip", type=int, nargs="*", default=None,
                   metavar="ID", help="跳过这些条目")
    p.add_argument("--pick", type=int, nargs=2, action="append", default=None,
                   metavar=("ID", "INDEX"), help="为「需你选择」的条目指定候选序号（从 0 数）")
    p.add_argument("--only", type=int, nargs="*", default=None, help="只处理这些 item id")
    p.add_argument("--assume-no-album-tag", action="store_true",
                   help="验证用：忽略已有专辑标签（模拟标签缺失的音乐库）")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", parents=[common], help="执行写入（默认 dry-run，需 --yes）")
    p.add_argument("run_id")
    p.add_argument("--yes", action="store_true", help="真的写入（不加这个只预览）")
    p.add_argument("--snapshot-mode", choices=["tags", "full"], default="tags",
                   help="快照模式：tags（默认，标签级）/ full（整文件字节备份）")
    p.add_argument("--no-preserve-mtime", action="store_true", help="不保留原修改时间")
    p.add_argument("--only", type=int, nargs="*", default=None, help="只处理这些 item id")
    p.add_argument("--limit", type=int, default=0, help="最多处理多少条")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", parents=[common], help="重新读文件校验已写入的标签")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("rollback", parents=[common], help="回滚（默认预览，需 --yes）")
    p.add_argument("run_id")
    p.add_argument("--batch", help="只回滚这一批（写 latest 表示最近一批）")
    p.add_argument("--yes", action="store_true", help="真的回滚")
    p.add_argument("--only", type=int, nargs="*", default=None, help="只回滚这些 item id")
    p.add_argument("--force", action="store_true",
                   help="即使文件在写入后又被外部改过，也强制回滚（危险）")
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("changes", parents=[common], help="查看变更记录")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_changes)

    p = sub.add_parser("report", parents=[common], help="生成报告")
    p.add_argument("run_id")
    p.add_argument("--md", default="建议报告.md")
    p.add_argument("--csv", default="建议报告.csv")
    p.add_argument("--disagreements-only", action="store_true", help="只列出有分歧/需处理的条目")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("run", parents=[common], help="一键：扫描→分组→分析→生成计划（不写入文件）")
    p.add_argument("directory", nargs="?", help="音乐库目录（缺省用 .env 或设置页记下的）")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件")
    p.add_argument("--no-llm", action="store_true", help="不调用云端模型（只到候选）")
    p.add_argument("--assume-no-album-tag", action="store_true",
                   help="验证用：假装文件里没有专辑标签")
    p.add_argument("--yes", action="store_true",
                   help="跳过费用预估确认（仅当你打开了「开始前显示费用预估」时才需要）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("todo", parents=[common], help="待办 4 组（默认只显示 4 行）")
    p.add_argument("run_id")
    p.add_argument("--limit", type=int, default=0, help="展开每组时显示几个目录")
    p.set_defaults(func=cmd_todo)

    p = sub.add_parser("choose", parents=[common], help="按专辑裁决：看候选 / 选定")
    p.add_argument("run_id")
    p.add_argument("--folder", help="专辑目录（不给则列出所有需要裁决的专辑）")
    p.add_argument("--candidate", type=int, help="选定第几个候选（序号从 0 数）")
    p.add_argument("--limit", type=int, default=0, help="列表时最多显示几张专辑")
    p.set_defaults(func=cmd_choose)

    p = sub.add_parser("group", parents=[common], help="按专辑目录分组 + 同目录共识（本地，不联网）")
    p.add_argument("run_id")
    p.add_argument("--min-files", type=int, default=2, help="少于这个文件数的目录不做互证（默认 2）")
    p.add_argument("--min-ratio", type=float, default=0.6,
                   help="有值文件里占比达到多少才算共识（默认 0.6）")
    p.add_argument("--no-recompute", action="store_true", help="只分组，不用共识重算决策")
    p.add_argument("--limit", type=int, default=0, help="只显示前 N 个目录的明细")
    p.set_defaults(func=cmd_group)

    p = sub.add_parser("gui", help="打开只读界面（看分组/共识/建议）")
    p.add_argument("run_id", nargs="?", help="要查看的 run（缺省用最近一次）")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("runs", help="列出历史 run")
    p.set_defaults(func=cmd_runs)
    return parser


def main(argv: list[str] | None = None) -> int:
    _enable_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "max_candidates") and args.max_candidates is None:
        args.max_candidates = load_settings().max_candidates_to_llm
    if hasattr(args, "low_evidence_candidates") and args.low_evidence_candidates is None:
        args.low_evidence_candidates = load_settings().low_evidence_candidates
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        _print(f"❌ {exc}")
        return EXIT_FATAL
    except KeyboardInterrupt:
        _print("\n已中断。")
        return EXIT_PARTIAL


if __name__ == "__main__":
    raise SystemExit(main())
