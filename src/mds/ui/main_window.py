"""主窗口：一个主页面（三态）+ 设置页。

早期反馈：待办页是个大白框、内容只占一小块、两个按钮还重复。
→ 合并成一页，4 组变筛选标签。

**写入永远由用户按下按钮触发**：没有定时写入、自动写入、后台写入。
写入走受控链路（快照 → 副本写入 → 完整性校验 → 原子替换），
界面自己不做任何文件操作（有测试守卫）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..config import Settings, snapshot_dir
from ..config import db_path as default_db_path
from ..logging_setup import get_logger
from ..pipeline.choose import apply_album_choice
from ..storage.db import Database
from . import prefs as ui_prefs
from . import theme
from .pages.main_page import MainPage
from .pages.settings import SettingsPage
from .viewmodel import (
    STATE_FIRST_RUN,
    STATE_READY,
    STATE_RESULTS,
    album_choice_views,
    build_todo_cards,
    group_rows,
    has_actionable,
    view_state,
)
from .widgets.progress_bar import ProgressPanel
from .widgets.stepper import Stepper
from .workers import ApplyWorker, CheckWorker, RollbackWorker, RunAllWorker

log = get_logger("ui.window")

#: 窗口标题只留产品名 —— 后面那句「只有你按写入时才会改文件」是说明文字，
#: 摆在标题栏让软件显得像未成品（早期）
WINDOW_TITLE = "音乐数据管家"

PAGE_MAIN = 0
PAGE_SETTINGS = 1


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        run_id: str | None = None,
        db_file: str | Path | None = None,
        settings: Settings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1280, 820)
        self.setMinimumSize(940, 620)

        self._db = Database(db_file or default_db_path())
        self._db.migrate()
        self._run_id = run_id or ""
        self._library = ""
        self._worker = None
        self._last_estimate = ""
        self._cards = []
        self._prefs: dict[str, object] = {}

        self._build_ui()
        self._load_prefs()
        self.refresh()

    # ── 界面搭建 ─────────────────────────────────────────
    def _build_ui(self) -> None:
        """三段式分区：**顶部分区带 / 中间内容面板 / 底部分区带**。

        设计说明：控件不直接铺在窗口上，而是分三层 ——
        工具栏与状态栏各成一条**带**（浅灰底 + 分隔线），中间是**白面板**，
        三段的界限一眼可见（「甲-2 分区带式」）。
        """
        central = QWidget()
        theme.style_container(central, "desk")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 顶部分区带：工具栏 + 三步进度 + 进度条 ──
        top_band = QWidget()
        theme.style_container(top_band, "bandTop")
        top_layout = QVBoxLayout(top_band)
        top_layout.setContentsMargins(
            theme.PX_PAD, theme.px("sm"), theme.PX_PAD, theme.px("sm")
        )
        top_layout.setSpacing(theme.px("sm"))
        top_layout.addLayout(self._build_toolbar())

        self._stepper = Stepper()
        top_layout.addWidget(self._stepper)

        self._progress = ProgressPanel()
        self._progress.setVisible(False)   # 闲时隐藏（见 _set_busy）
        top_layout.addWidget(self._progress)
        root.addWidget(top_band)

        # ── 中间：内容面板（四周留出桌面，面板才"浮"得起来）──
        body = QWidget()
        theme.style_container(body, "desk")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(
            theme.PX_PAD, theme.PX_PAD, theme.PX_PAD, theme.PX_PAD
        )
        body_layout.setSpacing(0)

        self._main_page = MainPage()
        self._main_page.choose_folder.connect(self._on_browse)
        self._main_page.change_folder.connect(self._on_browse)
        self._main_page.start.connect(self._on_start)
        self._main_page.write_requested.connect(self._on_write_requested)
        self._main_page.album_picked.connect(self._on_album_choice)
        self._main_page.row_selected.connect(self._on_row_selected)
        self._main_page.group_changed.connect(self._on_group_changed)

        self._settings_page = SettingsPage()
        self._settings_page.save_requested.connect(self._on_save_settings)
        self._settings_page.test_requested.connect(self._on_test_connection)
        self._settings_page.back_requested.connect(lambda: self._show_page(PAGE_MAIN))
        self._settings_page.reset_requested.connect(self._on_reset_settings)
        self._settings_page.purge_requested.connect(self._on_purge_data)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._main_page)
        self._stack.addWidget(self._settings_page)
        body_layout.addWidget(self._stack)
        root.addWidget(body, 1)

        # ── 底部分区带：汇总 + 撤销 + 下一步 ──
        bottom_band = QWidget()
        theme.style_container(bottom_band, "bandBottom")
        bottom_layout = QVBoxLayout(bottom_band)
        bottom_layout.setContentsMargins(
            theme.PX_PAD, theme.px("sm"), theme.PX_PAD, theme.px("sm")
        )
        bottom_layout.setSpacing(theme.px("xs"))
        bottom_layout.addLayout(self._build_footer())
        root.addWidget(bottom_band)

        self.setCentralWidget(central)

    def _build_toolbar(self) -> QHBoxLayout:
        self._library_edit = QLineEdit()
        self._library_edit.setPlaceholderText("音乐文件夹路径")
        self._library_edit.editingFinished.connect(self._on_library_typed)
        self._browse = QPushButton("选择目录…")
        self._browse.clicked.connect(self._on_browse)
        self._start = QPushButton("开始")
        self._start.setObjectName("primary")
        self._start.setToolTip("读取标签并匹配发行版信息，不修改文件。")
        self._start.clicked.connect(self._on_start)
        self._cancel = QPushButton("取消")
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self._on_cancel)
        self._settings_btn = QPushButton("设置")
        self._settings_btn.clicked.connect(lambda: self._show_page(PAGE_SETTINGS))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.px("sm"))
        row.addWidget(QLabel("音乐库："))
        row.addWidget(self._library_edit, 1)
        row.addWidget(self._browse)
        row.addWidget(self._start)
        row.addWidget(self._cancel)
        row.addWidget(self._settings_btn)
        return row

    def _build_footer(self) -> QVBoxLayout:
        # 这里只放"最近一次操作的结果"（默认什么都不显示）+ 撤销。
        # 原来还有汇总数字（与上方标签重复）和一句「下一步：…」（像教程）—— 都删了。
        self._summary = QLabel("")
        self._summary.setObjectName("caption")

        self._undo = QPushButton("撤销上一批写入")
        self._undo.setEnabled(False)
        self._undo.setToolTip("回滚最近一次写入；文件被外部修改时会跳过。")
        self._undo.clicked.connect(self._on_undo)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._summary, 1)
        row.addWidget(self._undo)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.px("xs"))
        layout.addLayout(row)
        return layout

    # ── 偏好 ─────────────────────────────────────────────
    def _load_prefs(self) -> None:
        self._library = self._db.get_pref(ui_prefs.LIBRARY_PATH, "")
        geometry = self._db.get_pref(ui_prefs.WINDOW_GEOMETRY, "")
        if geometry:
            try:
                self.restoreGeometry(bytes.fromhex(geometry))
            except ValueError:
                log.debug("窗口尺寸记录损坏，忽略")
        ui_prefs.bump_usage(self._db)
        self._prefs = ui_prefs.interface_prefs(self._db)
        # 启动就套上主题（字号档位来自偏好）—— 放在这里，开窗即是正确的字号
        self._apply_theme()
        if not self._run_id:
            self._run_id = self._db.get_pref("last_run_id", "")
        if not self._run_id:
            runs = self._db.list_runs(limit=1)
            self._run_id = str(runs[0]["id"]) if runs else ""

        # 老用户升级的兜底：偏好里没记音乐库，但库里有跑过的 run →
        # 用那个 run 的音乐库。不然明明有结果，却被当成第一次用（实测踩到）。
        if not self._library and self._run_id:
            row = self._db.get_run(self._run_id)
            if row is not None and row["music_root"]:
                self._library = str(row["music_root"])

        if self._library:
            self._library_edit.setText(self._library)

    def _apply_theme(self) -> None:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            theme.apply(app, scale=str(self._prefs.get("font_scale", "standard")))

    def _save_geometry(self) -> None:
        self._db.set_pref(ui_prefs.WINDOW_GEOMETRY, bytes(self.saveGeometry()).hex())

    # ── 刷新（纯读取）────────────────────────────────────
    def refresh(self) -> None:
        cards, headline = ([], "")
        if self._run_id:
            cards, headline = build_todo_cards(self._db, self._run_id)
        self._cards = cards

        row = self._db.get_run(self._run_id) if self._run_id else None
        state = view_state(
            library=self._library_edit.text() or self._library,
            run_exists=row is not None,
            has_actionable=has_actionable(cards),
        )
        self._render_state(state, cards, headline)
        self._update_stepper(state)
        # 没选文件夹时「开始」不可点（而不是点了弹一句"还没选音乐库"）
        has_library = bool((self._library_edit.text() or self._library).strip())
        self._start.setEnabled(has_library and not self._is_running())
        batch = self._db.latest_batch_id(self._run_id) if self._run_id else ""
        # 起始页（从没用过）不该出现「撤销上一批写入」；用过但没写过也不该出现
        self._undo.setVisible(state != STATE_FIRST_RUN and bool(batch))
        self._undo.setEnabled(bool(batch))

    def _render_state(self, state: str, cards, headline: str) -> None:
        if state == STATE_FIRST_RUN:
            self._main_page.show_first_run()
            return
        if state == STATE_READY:
            self._main_page.show_ready(
                library=self._library_edit.text() or self._library,
                stats_line=self._library_stats_line(),
                cost_line=self._estimate_line(),
                note=self._last_run_note(),
            )
            return
        selected = self._selected_tab()
        self._main_page.show_results(cards, headline=headline, selected=selected)
        self._load_group_rows(selected)

    def _selected_tab(self) -> str:
        key = str(self._prefs.get("selected_tab") or "") or ui_prefs.get_pref(
            self._db, ui_prefs.FILTER_TAB, "safe"
        )
        if key not in {card.key for card in self._cards}:
            key = "safe"
        # 默认选 🟢；但若 🟢 是空的而别的组有内容，就选第一个有内容的
        by_key = {card.key: card for card in self._cards}
        if by_key.get(key) is not None and by_key[key].n_items:
            return key
        for candidate in ("safe", "choose", "conflict", "none"):
            if by_key.get(candidate) is not None and by_key[candidate].n_items:
                return candidate
        return "safe"

    def _on_group_changed(self, key: str) -> None:
        """用户换了筛选标签：记住它，并把那一组的数据填进去。"""
        self._db.set_pref(ui_prefs.FILTER_TAB, key)
        self._load_group_rows(key)

    def _load_group_rows(self, key: str) -> None:
        if not self._run_id:
            return
        if key == "choose":
            views = [dataclasses.asdict(v) for v in album_choice_views(self._db, self._run_id)]
            self._main_page.set_albums(views)
            return
        self._main_page.set_group_rows(key, group_rows(self._db, self._run_id, key))

    def _library_stats_line(self) -> str:
        if not self._run_id:
            return ""
        cards = self._cards
        total = sum(card.n_items for card in cards)
        return f"上一次读过 {total} 首" if total else ""

    def _estimate_line(self) -> str:
        if not self._run_id:
            return "预计花费：扫描后给出"
        settings = ui_prefs.effective_settings(self._db)
        from ..pipeline.run_all import estimate_cost, pending_count

        pending = pending_count(self._db, self._run_id, use_llm=settings.has_deepseek)
        cost = estimate_cost(pending, settings.estimated_cost_per_100)
        if pending == 0:
            return "没有新歌要处理"
        return (
            f"本次需要处理 {pending} 首，预计花费 ¥{cost:.2f}"
            f"（上限 ¥{settings.budget_per_100_tracks:.2f} / 100 首，超了会自动停）"
        )

    def _last_run_note(self) -> str:
        if not self._run_id:
            return ""
        row = self._db.get_run(self._run_id)
        if row is None:
            return ""
        return f"上次处理：{str(row['started_at'])[:10]}"

    def _update_stepper(self, state: str) -> None:
        """三步状态指示。设置页关掉「显示界面提示」→ **整条隐藏**。

        （曾写错成"关掉只收成一行细条"，导致关掉后它仍在显示）
        """
        current = 0 if state == STATE_FIRST_RUN else (2 if state == STATE_RESULTS else 1)
        show_hints = bool(self._prefs.get("show_hints", True))
        self._stepper.setVisible(show_hints)
        self._stepper.set_state(current)

    # ── 一键流程 ─────────────────────────────────────────
    def _on_library_typed(self) -> None:
        text = self._library_edit.text().strip()
        if text and text != self._library:
            self._library = text
            self._db.set_pref(ui_prefs.LIBRARY_PATH, text)

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "选择音乐库文件夹", self._library_edit.text() or self._library
        )
        if chosen:
            self._library_edit.setText(chosen)
            self._library = chosen
            self._db.set_pref(ui_prefs.LIBRARY_PATH, chosen)
            self.refresh()

    def _on_start(self) -> None:
        if self._busy():
            return
        library = self._library_edit.text().strip()
        if not library:
            QMessageBox.warning(self, "还没选音乐库", "请先点「选择目录…」挑一个文件夹。")
            return
        self._library = library
        self._db.set_pref(ui_prefs.LIBRARY_PATH, library)
        self._start_worker("prepare")

    def _start_worker(self, mode: str, run_id: str = "") -> None:
        settings = ui_prefs.effective_settings(self._db)
        guard = self._preflight(mode, settings)
        if guard is not None:
            QMessageBox.warning(self, "还差一点就能跑了", guard)
            return
        self._set_busy(True)
        self._progress.set_progress(0, 0, "")
        worker = RunAllWorker(
            mode,
            db_path=str(self._db.path),
            settings=settings,
            library=self._library,
            run_id=run_id or self._run_id,
            use_llm=settings.has_deepseek,
            parent=self,
        )
        worker.line.connect(self._progress.set_message)
        worker.progress.connect(self._progress.set_progress)
        worker.prepared.connect(self._on_prepared)
        worker.finished_ok.connect(self._on_run_done)
        worker.failed.connect(self._on_worker_failed)
        self._worker = worker
        worker.start()

    def _preflight(self, mode: str, settings: Settings) -> str | None:
        if mode == "prepare":
            return None
        missing = []
        if not settings.has_acoustid:
            missing.append("AcoustID Key（免费注册：https://acoustid.org/new-application）")
        if not settings.has_user_agent:
            missing.append("MusicBrainz 邮箱（填你自己的邮箱即可，不用注册）")
        if not missing:
            return None
        return (
            "还不能开始分析，先到「设置」里补上：\n\n"
            + "\n".join(f"· {item}" for item in missing)
            + "\n\n只想先看看有哪些文件的话，扫描是免费且不需要 Key 的。"
        )

    def _on_prepared(self, result) -> None:
        """扫描与分组做完了（这一步不花钱）。要不要继续花钱，问一句。"""
        self._run_id = result.run_id
        self._db.set_pref("last_run_id", result.run_id)
        self._last_estimate = (
            f"本次需要处理 {result.pending_before} 首，预估云端花费 ¥{result.estimated_cost:.2f}"
        )
        settings = ui_prefs.effective_settings(self._db)
        if result.pending_before == 0:
            self._progress.set_message("没有新文件，结果没变")
            self._set_busy(False)
            self.refresh()
            QMessageBox.information(
                self,
                "已是最新",
                "没有发现新文件，上次的结果仍然有效，所以这次什么都没改。",
            )
            return
        if settings.cost_estimate_confirm:
            answer = QMessageBox.question(
                self,
                "要花一点钱，先跟你确认",
                f"{self._last_estimate}\n"
                f"（上限 ¥{settings.budget_per_100_tracks:.2f} / 100 首，超了会自动停）\n\n"
                "随时可以取消；关掉窗口下次会接着跑。\n\n现在开始分析吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                self._set_busy(False)
                self.refresh()
                return
        QTimer.singleShot(0, lambda: self._start_worker("process", result.run_id))

    def _on_run_done(self, result) -> None:
        self._set_busy(False)
        if getattr(result, "processed", 0):
            self._progress.set_message(
                f"{self._last_estimate}　实际花费 ¥{result.cost_usd * 7.1:.4f}"
                f"　用时 {result.elapsed_sec} 秒"
            )
        self.refresh()

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._cancel.setEnabled(False)

    # ── 写入 ─────────────────────────────────────────────
    def _on_write_requested(self, ids: list[int]) -> None:
        if self._busy():
            return
        ids = [int(i) for i in ids]
        if not self._confirm_write(ids):
            return
        self._start_apply(ids)

    def _confirm_write(self, ids: list[int]) -> bool:
        """写入前的总确认。

        **必须**如实说清"哪些是往空字段填值、哪些会改动已有值" ——
        这是不能碰的红线，不能含糊成一句"不会覆盖"。
        """
        fills = cleanups = overwrites = 0
        for item_id in ids:
            plan = self._plan_of(self._db.get_item(item_id))
            if plan is None:
                continue
            for change in plan.changes:
                if change.kind == "cleanup":
                    cleanups += 1
                elif not (change.before or "").strip():
                    fills += 1
                else:
                    overwrites += 1

        lines = [f"将修改 {len(ids)} 个文件：", ""]
        if fills:
            lines.append(f"· 往空字段填值　　{fills} 处")
        if cleanups:
            lines.append(f"· 去掉杂质（如版本说明）　{cleanups} 处")
        if overwrites:
            lines.append(f"· ⚠️ 改动你已有的值　{overwrites} 处")
        if not overwrites:
            lines.append("")
            lines.append("没有一处会把你已有的标签换成别的值。")
        lines.append("")
        lines.append("写入前会自动生成快照，写完可以一键撤销。")
        return (
            QMessageBox.question(
                self,
                "确认写入",
                "\n".join(lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )

    def _plan_of(self, row: dict | None):
        if not row:
            return None
        from ..core.models import WritePlan
        from ..pipeline.common import load_json

        payload = load_json(row.get("plan_json"))
        if not payload:
            return None
        try:
            return WritePlan(**payload)
        except Exception:  # noqa: BLE001
            return None

    def _prepare_picard(self) -> bool:
        """在主线程把 Picard 上下文初始化好，失败就给人话并中止。

        为什么必须在主线程做：`setup_config()` 会建 QObject（日志处理器），
        它们归属调用线程。放到 worker 线程里建，后面主线程用起来就是错的。
        """
        from ..adapters.picard_headless import HeadlessPicard, PicardUnavailable

        try:
            HeadlessPicard(self._picard_config_file()).setup()
        except PicardUnavailable as exc:
            QMessageBox.critical(self, "读写标签的组件不可用", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 兜底也要说人话
            QMessageBox.critical(
                self, "读写标签的组件初始化失败", f"{type(exc).__name__}: {exc}"
            )
            return False
        return True

    def _start_apply(self, ids: list[int]) -> None:
        if not self._prepare_picard():
            return
        settings = ui_prefs.effective_settings(self._db)
        self._set_busy(True)
        worker = ApplyWorker(
            db_path=str(self._db.path),
            settings=settings,
            run_id=self._run_id,
            item_ids=ids,
            snapshot_root=str(snapshot_dir()),
            picard_config_file=self._picard_config_file(),
            parent=self,
        )
        worker.line.connect(self._progress.set_message)
        worker.finished_ok.connect(self._on_apply_done)
        worker.failed.connect(self._on_worker_failed)
        self._worker = worker
        worker.start()

    def _on_apply_done(self, stats) -> None:
        self._set_busy(False)
        self._progress.set_message(
            f"已写入并校验 {stats.verified} 条｜跳过 {stats.skipped}｜失败 {stats.failed}"
            f"　（{stats.batch_id}）"
        )
        if stats.failed:
            detail = "\n".join(f"· {name}：{error}" for name, error in stats.errors[:8])
            QMessageBox.warning(
                self,
                "有文件没写成",
                f"成功 {stats.verified} 条，失败 {stats.failed} 条。\n\n{detail}",
            )
        else:
            QMessageBox.information(
                self,
                "写完了",
                f"成功写入 {stats.verified} 条。\n\n"
                "如果发现不对，点「撤销上一批写入」可以整批恢复。",
            )
        self.refresh()

    def _on_undo(self) -> None:
        if self._busy():
            return
        batch = self._db.latest_batch_id(self._run_id)
        if not batch:
            return
        changes = self._db.iter_batch_changes(batch)
        answer = QMessageBox.question(
            self,
            "撤销上一批写入",
            f"将把最近这一批（{len(changes)} 个文件）恢复成写入前的样子。\n\n"
            "如果某个文件在写入后又被别的程序改过，会跳过它并列出来，不会强行覆盖。\n\n"
            "确定撤销吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if not self._prepare_picard():
            return
        settings = ui_prefs.effective_settings(self._db)
        self._set_busy(True)
        worker = RollbackWorker(
            db_path=str(self._db.path),
            settings=settings,
            run_id=self._run_id,
            batch_id=batch,
            snapshot_root=str(snapshot_dir()),
            picard_config_file=self._picard_config_file(),
            parent=self,
        )
        worker.line.connect(self._progress.set_message)
        worker.finished_ok.connect(self._on_undo_done)
        worker.failed.connect(self._on_worker_failed)
        self._worker = worker
        worker.start()

    def _on_undo_done(self, stats) -> None:
        self._set_busy(False)
        self._progress.set_message(
            f"已撤销 {stats.rolled_back} 条｜跳过 {stats.skipped}｜失败 {stats.failed}"
        )
        self.refresh()

    # ── 按专辑裁决 ───────────────────────────────────────
    def _on_album_choice(self, folder: str, release_mbid: str) -> None:
        if self._busy():
            return
        stats = apply_album_choice(self._db, self._run_id, folder, release_mbid)
        notice = f"已为这张专辑的 {stats.n_chosen}/{stats.n_total} 首选定版本"
        if stats.n_unmatched:
            notice += f"；{stats.n_unmatched} 首的候选里没有它，仍待你单独决定"
        self._progress.set_message(notice)
        self.refresh()
        self._load_group_rows("choose")

    def _on_row_selected(self, item_id: int) -> None:
        """选中一行 → 右侧显示这一条的完整详情（旧值/新值/来源/候选）。"""
        row = self._db.get_item(item_id)
        if row is None:
            return
        from .viewmodel import detail

        self._main_page.set_detail(
            detail(
                self._db,
                row,
                group=self._group_node_for(str(row.get("path", ""))),
                terms_enabled=bool(self._prefs.get("term_tooltips", True)),
            )
        )

    def _group_node_for(self, path: str):
        """找到某个文件所属的「专辑目录」节点（详情里要显示同目录共识）。

        注意返回的是**界面层**的 `GroupNode`，不是核心层的 `FolderGroup` ——
        两者字段同名但接口不同。传错类型时槽里会抛 AttributeError，
        而 Qt 会把它吞掉，界面上只表现为"点了没反应"（实测踩到）。
        """
        from pathlib import Path as _Path

        from .viewmodel import GroupNode

        folder = str(_Path(path).parent)
        for group in self._db.list_groups(self._run_id):
            if str(group.get("folder_path")) != folder:
                continue
            from ..pipeline.common import load_json

            return GroupNode(
                group_id=int(group.get("id") or 0),
                folder_path=folder,
                title=_Path(folder).name or folder,
                n_files=int(group.get("n_files") or 0),
                folder_hint=str(group.get("folder_hint") or ""),
                artist_hint=str(group.get("artist_hint") or ""),
                consensus=load_json(group.get("consensus_json")),
            )
        return None

    # ── 设置 ─────────────────────────────────────────────
    def _show_page(self, index: int) -> None:
        if index == PAGE_SETTINGS:
            self._settings_page.show_view(
                ui_prefs.settings_view(self._db), prefs=self._prefs
            )
            self._settings_page.set_usage_note(self._usage_note())
        self._stack.setCurrentIndex(index)

    def _usage_note(self) -> str:
        """数据占用说明 —— 让人知道"清空"会清掉多少东西。"""
        from ..pipeline.maintenance import data_usage

        usage = data_usage(self._db, snapshot_root=snapshot_dir())
        if not usage["runs"] and not usage["snapshot_files"]:
            return "当前没有分析数据。"
        db_mb = usage["db_bytes"] / 1024 / 1024
        snap_mb = usage["snapshots_bytes"] / 1024 / 1024
        return (
            f"当前：分析记录 {usage['runs']} 个（{usage['items']} 首）、"
            f"数据库 {db_mb:.1f} MB、快照 {usage['snapshot_files']} 份（{snap_mb:.1f} MB）。"
        )

    def _on_purge_data(self) -> None:
        """清空分析数据。

        ⚠️ 必须说清"删什么、留什么、动不到什么" —— 尤其"删了快照就不能撤销"。
        """
        from ..pipeline.maintenance import purge_analysis

        answer = QMessageBox.question(
            self,
            "清空分析数据",
            "会删除：\n"
            "· 全部分析结果与待办条目\n"
            "· 指纹 / 候选 / AI 调用的缓存\n"
            "· 全部写入快照（**删除后，之前写入的批次就不能再撤销**）\n\n"
            "会保留：\n"
            "· 密钥、邮箱、界面设置、音乐库目录\n\n"
            "不会改动：\n"
            "· 你的任何音乐文件\n\n"
            "确定清空吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        result = purge_analysis(self._db, snapshot_root=snapshot_dir())
        self._db.delete_pref("last_run_id")
        self._run_id = ""
        self._prefs = ui_prefs.interface_prefs(self._db)
        self.refresh()
        self._settings_page.set_usage_note(self._usage_note())
        QMessageBox.information(
            self,
            "已清空",
            f"{result.summary()}\n\n密钥与设置已保留，音乐文件未改动。",
        )

    def _on_save_settings(self) -> None:
        values = self._settings_page.collect()
        report = ui_prefs.save_profile(
            self._db,
            email=str(values["email"]),
            acoustid_key=str(values["acoustid_key"]) or None,
            deepseek_key=str(values["deepseek_key"]) or None,
        )
        ui_prefs.set_bool(self._db, ui_prefs.COST_CONFIRM, bool(values["cost_confirm"]))
        ui_prefs.set_bool(self._db, ui_prefs.SHOW_HINTS, bool(values["show_hints"]))
        ui_prefs.set_bool(self._db, ui_prefs.TERM_TOOLTIPS, bool(values["term_tooltips"]))
        self._db.set_pref(ui_prefs.FONT_SCALE, str(values["font_scale"]))
        self._db.set_pref("batch_limit", str(int(values["batch_limit"])))

        self._prefs = ui_prefs.interface_prefs(self._db)
        self._apply_theme()
        # ⚠️ 必须刷新一次：否则"显示界面提示"这类开关要等下次刷新才生效 ——
        #    关掉后三步进度条还在显示，就是这里漏了（实测踩到）
        self.refresh()
        self._progress.set_message(report.message())
        self._settings_page.show_view(ui_prefs.settings_view(self._db), prefs=self._prefs)
        if report.ok:
            QMessageBox.information(self, "已保存", report.message())
        else:
            QMessageBox.warning(self, "没完全存成", report.message())

    def _on_reset_settings(self) -> None:
        """恢复默认设置。

        ⚠️ **只重置界面偏好与开关，绝不动密钥和音乐库目录** ——
        那两样是用户辛苦填的，一键重置掉会让人骂人。
        """
        answer = QMessageBox.question(
            self,
            "恢复默认设置",
            "会把这些恢复成默认：界面字号、两个提示开关、费用预估、单批上限。\n\n"
            "**你的密钥、邮箱、音乐库目录都会保留。**\n\n确定吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for key in (
            ui_prefs.FONT_SCALE,
            ui_prefs.SHOW_HINTS,
            ui_prefs.TERM_TOOLTIPS,
            ui_prefs.COST_CONFIRM,
            ui_prefs.FILTER_TAB,
            "batch_limit",
        ):
            self._db.delete_pref(key)
        self._prefs = ui_prefs.interface_prefs(self._db)
        self._apply_theme()
        self._settings_page.show_view(ui_prefs.settings_view(self._db), prefs=self._prefs)
        self._progress.set_message("已恢复默认设置（密钥与音乐库目录未动）")

    def _on_test_connection(self) -> None:
        values = self._settings_page.collect()
        self._settings_page.set_busy(True)
        worker = CheckWorker(
            email=str(values["email"]),
            acoustid_key=str(values["acoustid_key"]),
            deepseek_key=str(values["deepseek_key"]),
            parent=self,
        )
        worker.finished_ok.connect(self._on_check_done)
        self._worker = worker
        worker.start()

    def _on_check_done(self, text: str) -> None:
        self._settings_page.set_busy(False)
        self._settings_page.show_test_result(text)

    # ── 状态 ─────────────────────────────────────────────
    def _is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _busy(self) -> bool:
        if self._is_running():
            QMessageBox.information(self, "还在忙", "上一个任务还没结束，请先等它完成或点「取消」。")
            return True
        return False

    def _set_busy(self, busy: bool) -> None:
        # 进度条只在真的在跑时出现；闲时留一条空进度条 + "就绪" 是噪音
        self._progress.setVisible(busy)
        for widget in (self._start, self._browse, self._settings_btn, self._undo):
            widget.setEnabled(not busy)
        self._cancel.setEnabled(busy)
        self._main_page.write_button().setEnabled(not busy)
        if not busy:
            self._progress.set_message("完成")
            self.refresh()

    def _on_worker_failed(self, message: str) -> None:
        self._set_busy(False)
        self._progress.set_message(f"失败：{message}")
        QMessageBox.critical(
            self, "没跑成", f"{message}\n\n已完成的进度都保存了，再点一次可以接着跑。"
        )

    @staticmethod
    def _picard_config_file():
        from ..adapters.picard_headless import make_config_file

        return make_config_file(config.data_dir())

    # ── 收尾 ─────────────────────────────────────────────
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        if getattr(self, "_closed", False):
            # 关闭可能被调用不止一次（测试框架收尾时会再来一次）。
            # 第二次再去写偏好就会撞上"database is closed"（实测踩到）。
            event.accept()
            return

        worker = self._worker
        running = worker is not None and worker.isRunning()

        if running:
            answer = QMessageBox.question(
                self,
                "还有任务在跑",
                "现在关掉窗口的话，任务会停在这里。\n\n"
                "已经完成的进度都保存在数据库里了，下次打开可以接着跑。\n\n要关掉吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                # ⚠️ 这里**不能**设 _closed：否则"第一次选了不关"之后，
                # 之后再关窗口就直接放行、不再拦截（实测踩到）。
                event.ignore()
                return

        self._closed = True  # 只有真的要关了才设
        if running and worker is not None:
            if hasattr(worker, "stop"):
                worker.stop()
            worker.wait(20000)
        self._save_geometry()
        self._db.close()
        super().closeEvent(event)
