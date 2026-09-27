"""后台 worker：扫描 / 分析 / 分组跑在子线程里，界面不冻结。

界面线程只负责显示。要「取消」时调用 `stop()`，pipeline 会在下一个文件前退出，
**已完成的进度都留在数据库里，重跑即续跑**（沿用 S6 的续跑语义）。

本模块只调用 `pipeline.scan` / `pipeline.analyze` / `pipeline.group` ——
这三个都不修改音乐文件。
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from ..config import Settings
from ..logging_setup import get_logger
from ..logging_setup import setup as setup_logging
from ..pipeline.analyze import AnalyzeOptions, analyze
from ..pipeline.group import build_groups
from ..pipeline.run_all import RunAllOptions, prepare, process
from ..pipeline.scan import scan
from ..storage.db import Database
from .viewmodel import parse_progress_line

log = get_logger("ui.worker")

TASK_LABELS = {
    "scan": "扫描音乐库",
    "analyze": "分析（指纹 → AcoustID → MusicBrainz → AI）",
    "group": "按专辑目录分组 + 同目录共识",
}


class PipelineWorker(QThread):
    """跑一次 scan / analyze / group。"""

    line = pyqtSignal(str)            # 一行消息
    progress = pyqtSignal(int, int, str)  # 已完成 / 总数 / 当前文件
    succeeded = pyqtSignal(str)       # 完成，附一句话摘要
    failed = pyqtSignal(str)          # 失败

    def __init__(
        self,
        task: str,
        *,
        db_path: str,
        settings: Settings,
        library: str = "",
        run_id: str | None = None,
        limit: int = 0,
        new: bool = False,
        use_llm: bool = True,
        assume_no_album_tag: bool = False,
        mode: str = "full",
        parent=None,
    ) -> None:
        super().__init__(parent)
        if task not in TASK_LABELS:
            raise ValueError(f"未知任务：{task}")
        self.task = task
        self._db_path = db_path
        self._settings = settings
        self._library = library
        self._run_id = run_id or ""
        self._limit = limit
        self._new = new
        self._use_llm = use_llm
        self._assume_no_album_tag = assume_no_album_tag
        self._mode = mode
        self._stop = False
        self.result_run_id = ""

    # ── 取消 ─────────────────────────────────────────────
    def stop(self) -> None:
        self._stop = True
        self.line.emit("已请求停止，等当前文件做完就退出（进度已保存，可续跑）…")

    def should_stop(self) -> bool:
        return self._stop

    # ── 线程体 ───────────────────────────────────────────
    def run(self) -> None:  # noqa: D102 - QThread 接口
        setup_logging("INFO", self._settings.secret_values())
        try:
            # 每个线程用自己的数据库连接（sqlite 连接不能跨线程共用）
            with Database(self._db_path) as db:
                db.migrate()
                if self.task == "scan":
                    summary = self._run_scan(db)
                elif self.task == "analyze":
                    summary = self._run_analyze(db)
                else:
                    summary = self._run_group(db)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成一个可读的失败提示
            log.exception("worker 失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.succeeded.emit(summary)

    def _say(self, text: str) -> None:
        self.line.emit(text)
        parsed = parse_progress_line(text)
        if parsed is not None:
            self.progress.emit(*parsed)

    def _run_scan(self, db: Database) -> str:
        result = scan(
            db,
            self._library,
            params={
                "limit": self._limit,
                "acoustid_rate": self._settings.acoustid_rate,
                "musicbrainz_rate": self._settings.musicbrainz_rate,
                "model": self._settings.model,
                "source": "gui",
            },
            limit=self._limit,
            resume=not self._new,
        )
        self.result_run_id = result.run_id
        counts = db.item_counts(result.run_id)
        return f"扫描完成：发现 {result.found} 个文件（新入库 {result.inserted}），共 {counts['total']} 个"

    def _run_analyze(self, db: Database) -> str:
        if not self._run_id:
            raise ValueError("没有可分析的 run，请先扫描音乐库")
        stats = analyze(
            db,
            self._settings,
            self._run_id,
            options=AnalyzeOptions(
                mode=self._mode,
                use_llm=self._use_llm,
                assume_no_album_tag=self._assume_no_album_tag,
            ),
            progress=self._say,
            should_stop=self.should_stop,
        )
        self.result_run_id = self._run_id
        if self._stop:
            return f"已停止（本次处理 {stats.processed} 首，进度已保存，可续跑）"
        return (
            f"分析完成：处理 {stats.processed} 首，跳过已完成 {stats.skipped_done} 首，"
            f"失败 {stats.errors} 首，用时 {stats.elapsed_sec} 秒"
        )

    def _run_group(self, db: Database) -> str:
        if not self._run_id:
            raise ValueError("没有可分组的数据，请先扫描音乐库")
        stats = build_groups(db, self._run_id)
        self.result_run_id = self._run_id
        return (
            f"分组完成：{stats.n_groups} 个专辑目录（多文件 {stats.n_multi} 个），"
            f"用同目录共识补全 {stats.consensus_changes} 处空字段"
        )


class RunAllWorker(QThread):
    """一键流程。分两段跑：

    - `mode="prepare"`：扫描 + 分组 + 费用预估（**完全免费、不联网**）
    - `mode="process"`：分析 + 生成计划（要花钱）
    - `mode="all"`：两段连着跑（命令行那种）

    拆开是为了让**费用确认弹窗能在界面主线程里弹** —— worker 里不许碰界面。
    """

    line = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    prepared = pyqtSignal(object)     # RunAllResult（第一段结果）
    finished_ok = pyqtSignal(object)  # RunAllResult
    failed = pyqtSignal(str)

    def __init__(
        self,
        mode: str,
        *,
        db_path: str,
        settings: Settings,
        library: str = "",
        run_id: str = "",
        limit: int = 0,
        use_llm: bool = True,
        assume_no_album_tag: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if mode not in ("prepare", "process", "all"):
            raise ValueError(f"未知的一键流程模式：{mode}")
        self.mode = mode
        self._db_path = db_path
        self._settings = settings
        self._library = library
        self._run_id = run_id
        self._limit = limit
        self._use_llm = use_llm
        self._assume_no_album_tag = assume_no_album_tag
        self._stop = False
        self.result_run_id = ""

    def stop(self) -> None:
        self._stop = True
        self.line.emit("已请求停止，等当前文件做完就退出（进度已保存，可续跑）…")

    def should_stop(self) -> bool:
        return self._stop

    def _say(self, text: str) -> None:
        self.line.emit(text)
        parsed = parse_progress_line(text)
        if parsed is not None:
            self.progress.emit(*parsed)

    def run(self) -> None:  # noqa: D102 - QThread 接口
        setup_logging("INFO", self._settings.secret_values())
        options = RunAllOptions(
            library=self._library or self._settings.music_library_path,
            limit=self._limit,
            use_llm=self._use_llm,
            assume_no_album_tag=self._assume_no_album_tag,
        )
        prepared = None
        try:
            with Database(self._db_path) as db:
                db.migrate()
                if self.mode in ("prepare", "all"):
                    prepared = prepare(
                        db, self._settings, options=options,
                        progress=self._say, should_stop=self.should_stop,
                    )
                    self.result_run_id = prepared.run_id
                    self.prepared.emit(prepared)
                    if self.mode == "prepare" or prepared.stopped:
                        self.finished_ok.emit(prepared)
                        return
                    self._run_id = prepared.run_id

                if not self._run_id:
                    raise ValueError("没有可分析的 run，请先点「开始」扫描音乐库")
                result = process(
                    db, self._settings, self._run_id, options=options,
                    progress=self._say, should_stop=self.should_stop,
                )
                if self.mode == "all" and prepared is not None:
                    result.scanned = prepared.scanned
                    result.inserted = prepared.inserted
                    result.estimated_cost = prepared.estimated_cost
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成一句人话
            log.exception("一键流程失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.result_run_id = self.result_run_id or result.run_id
        self.finished_ok.emit(result)


class CheckWorker(QThread):
    """设置页的「测试连接」：三个服务各请求一次，不花钱。"""

    finished_ok = pyqtSignal(str)

    def __init__(
        self,
        *,
        email: str,
        acoustid_key: str,
        deepseek_key: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._email = email
        self._acoustid_key = acoustid_key
        self._deepseek_key = deepseek_key

    def run(self) -> None:  # noqa: D102
        from ..adapters.health import check_services
        from ..config import build_user_agent

        settings = Settings(
            acoustid_api_key=self._acoustid_key,
            deepseek_api_key=self._deepseek_key,
            musicbrainz_user_agent=build_user_agent(self._email),
        )
        try:
            checks = check_services(settings)
        except Exception as exc:  # noqa: BLE001
            self.finished_ok.emit(f"测试失败：{type(exc).__name__}: {exc}")
            return
        lines = []
        for check in checks:
            lines.append(f"{'✅' if check.ok else '❌'} {check.name}：{check.detail}")
            if check.hint and not check.ok:
                lines.append(f"　　{check.hint}")
        self.finished_ok.emit("\n".join(lines))


class ApplyWorker(QThread):
    """批量写入（走写入链路：快照 → 副本写入 → 校验 → 原子替换）。"""

    line = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # ApplyStats
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        db_path: str,
        settings: Settings,
        run_id: str,
        item_ids: list[int],
        snapshot_root: str,
        picard_config_file: str | None = None,
        limit: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._settings = settings
        self._run_id = run_id
        self._item_ids = list(item_ids)
        self._snapshot_root = snapshot_root
        self._picard_config_file = picard_config_file
        self._limit = limit
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102
        from ..pipeline.apply import ApplyOptions, apply_run

        setup_logging("INFO", self._settings.secret_values())
        try:
            with Database(self._db_path) as db:
                db.migrate()
                stats = apply_run(
                    db,
                    self._run_id,
                    snapshot_root=self._snapshot_root,
                    picard_config_file=self._picard_config_file,
                    options=ApplyOptions(only=self._item_ids, limit=self._limit),
                    progress=self.line.emit,
                    should_stop=lambda: self._stop,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("写入失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_ok.emit(stats)


class RollbackWorker(QThread):
    """撤销上一批写入（回滚前会检查文件是否被外部改过）。"""

    line = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # RollbackStats
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        db_path: str,
        settings: Settings,
        run_id: str,
        batch_id: str,
        snapshot_root: str,
        picard_config_file: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._settings = settings
        self._run_id = run_id
        self._batch_id = batch_id
        self._snapshot_root = snapshot_root
        self._picard_config_file = picard_config_file

    def run(self) -> None:  # noqa: D102
        from ..pipeline.rollback import rollback_run

        setup_logging("INFO", self._settings.secret_values())
        try:
            with Database(self._db_path) as db:
                db.migrate()
                stats = rollback_run(
                    db,
                    self._run_id,
                    snapshot_root=self._snapshot_root,
                    picard_config_file=self._picard_config_file,
                    batch_id=self._batch_id,
                    progress=self.line.emit,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("撤销失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_ok.emit(stats)
