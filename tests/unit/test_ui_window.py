"""主窗口测试（offscreen，不弹窗）。

这一版结构变了：待办页 / 清单页 / 按专辑页 **合并成一个主页面**，
用"筛选标签"切换。所以断言也跟着更新。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QCloseEvent  # noqa: E402

from mds.adapters.secrets import MemoryStore  # noqa: E402
from mds.config import Settings  # noqa: E402
from mds.ui import prefs as ui_prefs  # noqa: E402
from mds.ui.main_window import PAGE_SETTINGS, MainWindow  # noqa: E402
from mds.ui.pages.main_page import LIST_ALBUMS, LIST_TABLE, PANE_RESULTS, PANE_START  # noqa: E402


def _settings() -> Settings:
    return Settings(
        acoustid_api_key="k",
        deepseek_api_key="",
        musicbrainz_user_agent="mds/0.1 ( mailto:a@b.c )",
        acoustid_rate=1000.0,
        musicbrainz_rate=1000.0,
    )


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """测试里不许弹模态框 —— offscreen 下它会永久阻塞（实测把整轮测试挂死）。"""
    from PyQt5.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr("mds.ui.prefs.secret_store", lambda: store)
    monkeypatch.setattr("mds.ui.main_window.ui_prefs.secret_store", lambda: store)
    return store


def _window(qtbot, seeded_db, *, library: str | None = None) -> MainWindow:
    win = MainWindow(run_id=seeded_db["run_id"], db_file=seeded_db["path"], settings=_settings())
    qtbot.addWidget(win)
    if library is not None:
        win._db.set_pref(ui_prefs.LIBRARY_PATH, library)
        win._library = library
        win._library_edit.setText(library)
        win.refresh()
    return win


@pytest.fixture()
def window(qtbot, seeded_db) -> MainWindow:
    """带音乐库的窗口（seeded_db 里有 4 条：1 可写 / 1 待选 / 2 不用管）。"""
    return _window(qtbot, seeded_db, library="/Music")


# ── 三态 ─────────────────────────────────────────────────
def test_first_run_shows_only_one_action(window: MainWindow) -> None:
    """没选过音乐库 → 起始页。**页面上只有一件事可做，且不提"看说明书"。**"""
    win = MainWindow(run_id=window._run_id, db_file=window._db.path, settings=_settings())
    win._db.delete_pref(ui_prefs.LIBRARY_PATH)
    win._library = ""
    win._library_edit.setText("")
    win.refresh()

    assert win._main_page.current_pane == PANE_START
    assert win._main_page.start_panel.mode == "first_run"
    text = win._main_page.start_panel.text_content()
    assert "选择文件夹" in text
    assert "不做任何修改" in text, "要说明它不修改文件"
    assert "说明书" not in text, "界面上不应出现「请看说明书」这种引导"


def test_ready_state_when_no_results(qtbot, seeded_db) -> None:
    """选过目录但还没有结果 → 待开始页（带音乐库与花费说明）。"""
    win = _window(qtbot, seeded_db)
    win._run_id = ""
    win.refresh()
    assert win._main_page.start_panel.mode == "ready"
    assert "开  始" in win._main_page.start_panel.text_content()


def test_ready_state_when_everything_done(qtbot, seeded_db) -> None:
    """上次已经处理完 → **不显示陈旧结果**（早期反馈）。"""
    from mds.storage.db import Database

    win = _window(qtbot, seeded_db, library="/Music")
    # 把结果全部标成"无需处理"：删掉 plan 让所有条目归入 none
    with Database(seeded_db["path"]) as db:
        for row in list(db.iter_items(seeded_db["run_id"])):
            db.update_item(int(row["id"]), plan_json={}, decision_json={"action": "no_op"})
    win.refresh()
    assert win._main_page.current_pane == PANE_START
    assert win._main_page.start_panel.mode == "ready"


def test_results_state_shows_tabs(window: MainWindow) -> None:
    assert window._main_page.current_pane == PANE_RESULTS
    counts = {key: window._main_page.tabs.count_of(key) for key in ("safe", "choose", "conflict", "none")}
    assert sum(counts.values()) == 5


def test_default_tab_is_safe(window: MainWindow) -> None:
    window._db.delete_pref(ui_prefs.FILTER_TAB)
    window.refresh()
    assert window._main_page.tabs.current_key() == "safe"


# ── 筛选标签（原待办 4 组）───────────────────────────────
def test_switching_tab_loads_that_group(window: MainWindow) -> None:
    window._main_page.tabs.set_current("conflict")
    rows = window._main_page.model.rows
    assert rows, "🟠 组应当有条目"
    assert all(row.action == "keep_existing" for row in rows)

    window._main_page.tabs.set_current("safe")
    assert all(row.action in ("fill_missing", "cleanup") for row in window._main_page.model.rows)


def test_choose_tab_shows_album_picker(window: MainWindow) -> None:
    """🟡 组的操作方式不一样：不是勾选，而是"每张专辑选一个版本"。"""
    window._main_page.tabs.set_current("choose")
    assert window._main_page.current_list == LIST_ALBUMS
    assert "张专辑" in window._main_page.albums.text_content()


def test_other_tabs_show_table(window: MainWindow) -> None:
    for key in ("safe", "conflict", "none"):
        window._main_page.tabs.set_current(key)
        assert window._main_page.current_list == LIST_TABLE


def test_tab_choice_is_remembered(window: MainWindow) -> None:
    window._main_page.tabs.set_current("conflict")
    assert window._db.get_pref(ui_prefs.FILTER_TAB) == "conflict"


# ── 表格：5 列 + 勾选可见性 ──────────────────────────────
def test_table_has_six_columns(window: MainWindow) -> None:
    """6 列：比原来的 8 列少，但比"挤成一格两行"可靠。

    （试过把「文件名 / 曲名」放一格两行 —— Qt 在样式表 + macOS 下只画一行，
      第二行被吞掉，反复验证过。所以分成两列。）
    """
    window._main_page.tabs.set_current("safe")
    assert window._main_page.model.columnCount() == 6
    headers = [window._main_page.model.headerData(i, Qt.Horizontal) for i in range(6)]
    assert headers[0] == "✓"
    assert headers[1] == "文件名"
    assert headers[2] == "曲名"
    assert "状态" not in headers, "状态列去掉了（用行首图标代替）"
    assert "艺术家" not in headers, "艺术家移到曲名列的提示里了"


def test_artist_is_in_the_tooltip_not_a_column(window: MainWindow) -> None:
    """艺术家不占列，但要能在提示里看到（同一张专辑里通常都一样）。"""
    window._main_page.tabs.set_current("safe")
    model = window._main_page.model
    row = model.row_at(0)
    if not row.artist:
        pytest.skip("这条没有艺术家")
    tooltip = model.data(model.index(0, 2), Qt.ToolTipRole)
    assert row.artist in tooltip


def test_select_all_does_not_resize_columns(window: MainWindow) -> None:
    """全选**不能**改变列宽。

    实测踩到：全选时发了 layoutChanged，表头趁机重算「按内容自适应」的列宽，
    结果专辑列被长名字撑开、把文件名挤成「0…」。
    """
    window._main_page.tabs.set_current("safe")
    view = window._main_page._table
    before = [view.columnWidth(c) for c in range(window._main_page.model.columnCount())]
    window._main_page.model.set_all_checked(True)
    window.centralWidget().layout().activate()
    after = [view.columnWidth(c) for c in range(window._main_page.model.columnCount())]
    assert after == before


def test_checked_rows_are_visibly_highlighted(window: MainWindow) -> None:
    """勾选的行整行淡蓝 —— 解决"看不出到底选中了没有"。"""
    from mds.ui import theme

    window._main_page.tabs.set_current("safe")
    model = window._main_page.model
    row = model.row_at(0)
    assert row.writable is True
    model.setData(model.index(0, 0), Qt.Checked, Qt.CheckStateRole)
    background = model.data(model.index(0, 0), Qt.BackgroundRole)
    assert background.color().name() == theme.COLOR["accent_weak"]
    # 整行都要重画（不然只有第一列变蓝）
    for column in range(model.columnCount()):
        bg = model.data(model.index(0, column), Qt.BackgroundRole)
        assert bg.color().name() == theme.COLOR["accent_weak"]


def test_row_highlight_delegate_exists(window: MainWindow) -> None:
    from mds.ui.models import RowHighlightDelegate

    assert isinstance(window._main_page._table.itemDelegate(), RowHighlightDelegate)


def test_select_all_and_none(window: MainWindow) -> None:
    window._main_page.tabs.set_current("safe")
    model = window._main_page.model
    assert model.checked_ids()
    model.set_all_checked(False)
    assert model.checked_ids() == []
    model.set_all_checked(True)
    assert len(model.checked_ids()) == model.rowCount()


def test_double_click_toggles_checkbox(window: MainWindow) -> None:
    window._main_page.tabs.set_current("safe")
    model = window._main_page.model
    index = model.index(0, 1)
    before = model.row_at(0).checked
    window._main_page._on_double_click(index)
    assert model.row_at(0).checked is not before


def test_non_writable_row_cannot_be_checked(window: MainWindow) -> None:
    window._main_page.tabs.set_current("none")
    model = window._main_page.model
    if model.rowCount() == 0:
        pytest.skip("这一组没有条目")
    assert model.setData(model.index(0, 0), Qt.Checked, Qt.CheckStateRole) is False


def test_search_filters_rows(window: MainWindow) -> None:
    window._main_page.tabs.set_current("safe")
    model = window._main_page.model
    total = model.rowCount()
    window._main_page.set_search("绝对不存在的关键词")
    assert model.rowCount() == 0
    window._main_page.set_search("")
    assert model.rowCount() == total


# ── 勾选规则（🟠 里安全的填空也要默认勾）─────────────────
def test_conflict_group_safe_part_is_checked_by_rule(window: MainWindow) -> None:
    """🟠 组里"只有安全填空"的条目默认勾上（既定规则）。

    「是否默认勾选」由 `core/selectrules.py` 按**计划里的改动**决定，
    不按标签一刀切。
    """
    window._main_page.tabs.set_current("conflict")
    rows = window._main_page.model.rows
    for row in rows:
        expected = row.writable and row.n_changes > 0
        assert row.checked is expected or row.n_conflicts == 0


# ── 底部「下一步」────────────────────────────────────────
# ── 三步进度 ─────────────────────────────────────────────
def test_stepper_reflects_state(window: MainWindow) -> None:
    assert "①" in window._stepper.plain_text()
    assert window._stepper.current == 2, "有结果时应当停在第 ③ 步"


def test_stepper_hidden_when_hints_off(window: MainWindow) -> None:
    """关掉「显示界面提示」→ 三步状态指示**整条隐藏**。

    实测踩到的疏漏：原来写成"关掉只收成一行细条"，关掉后发现它还在。
    """
    window._db.set_pref(ui_prefs.SHOW_HINTS, "0")
    window._prefs = ui_prefs.interface_prefs(window._db)
    window.refresh()
    # 用 isHidden() 而不是 isVisible()：窗口没 show() 时（测试里不 show，
    # 因为 pytest-qt 下显示窗口会段错误）子控件的 isVisible() 恒为 False
    assert window._stepper.isHidden() is True


def test_stepper_has_no_tutorial_text(window: MainWindow) -> None:
    """状态指示只写"①②③ + 步骤名"，不写说明文字（写说明就像教程）。"""
    text = window._stepper.plain_text()
    assert "①" in text and "②" in text and "③" in text
    for word in ("选一个放音乐的文件夹", "读标签", "看一眼"):
        assert word not in text


# ── 设置页 ───────────────────────────────────────────────
def test_settings_has_font_scale_and_toggles(window: MainWindow) -> None:
    window._show_page(PAGE_SETTINGS)
    assert window._stack.currentIndex() == PAGE_SETTINGS
    page = window._settings_page
    # 只有两档 → 用单选按钮（下拉框要把选项点开才看得全，还容易被样式搞坏）
    assert page.font_standard_radio.isChecked() is True
    assert page.font_large_radio.isChecked() is False
    assert page.show_hints_check.isChecked() is True
    assert page.term_tooltips_check.isChecked() is True


def test_settings_persist_font_scale_and_toggles(window: MainWindow, _no_real_keyring) -> None:
    window._show_page(PAGE_SETTINGS)
    page = window._settings_page
    page.font_large_radio.setChecked(True)
    page.show_hints_check.setChecked(False)
    page.term_tooltips_check.setChecked(False)
    window._on_save_settings()

    assert ui_prefs.font_scale(window._db) == "large"
    assert ui_prefs.get_bool(window._db, ui_prefs.SHOW_HINTS) is False
    assert ui_prefs.get_bool(window._db, ui_prefs.TERM_TOOLTIPS) is False


def test_reset_settings_keeps_secrets_and_library(window: MainWindow, _no_real_keyring, monkeypatch) -> None:
    """「恢复默认设置」**不能动密钥与音乐库目录** —— 那是用户辛苦填的。"""
    from PyQt5.QtWidgets import QMessageBox

    _no_real_keyring.set("ACOUSTID_API_KEY", "keep-me-1234")
    window._db.set_pref(ui_prefs.FONT_SCALE, "large")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    window._on_reset_settings()

    assert _no_real_keyring.get("ACOUSTID_API_KEY") == "keep-me-1234"
    assert window._library_edit.text() == "/Music"
    assert ui_prefs.font_scale(window._db) == "standard"


def test_toggling_hints_takes_effect_immediately(window: MainWindow) -> None:
    """关掉「显示界面提示」并保存后，三步状态指示要**立刻消失**。

    实测踩到：`_on_save_settings` 里漏了 refresh，开关存进去了但界面没变 ——
    关了之后发现它还在显示（实测发现）。
    """
    assert window._stepper.isHidden() is False
    window._show_page(PAGE_SETTINGS)
    window._settings_page.show_hints_check.setChecked(False)
    window._on_save_settings()
    assert window._stepper.isHidden() is True, "关掉开关后应当立刻隐藏"

    window._settings_page.show_hints_check.setChecked(True)
    window._on_save_settings()
    assert window._stepper.isHidden() is False


def test_settings_never_show_plain_key(window: MainWindow, _no_real_keyring) -> None:
    _no_real_keyring.set("ACOUSTID_API_KEY", "sk-abcdef123456")
    window._show_page(PAGE_SETTINGS)
    placeholder = window._settings_page.acoustid_edit.edit.placeholderText()
    assert "sk-abcdef123456" not in placeholder
    assert "3456" in placeholder


# ── 动手前检查 ───────────────────────────────────────────
def test_preflight_allows_free_scan_without_keys(window: MainWindow) -> None:
    assert window._preflight("prepare", _settings()) is None


def test_preflight_blocks_analysis_without_keys(window: MainWindow) -> None:
    empty = Settings(acoustid_api_key="", musicbrainz_user_agent="")
    message = window._preflight("process", empty)
    assert message is not None
    assert "邮箱" in message


# ── 忙碌状态 ─────────────────────────────────────────────
def test_busy_state_toggles_buttons(window: MainWindow) -> None:
    window._set_busy(True)
    assert window._start.isEnabled() is False
    assert window._cancel.isEnabled() is True
    assert window._undo.isEnabled() is False
    window._set_busy(False)
    assert window._start.isEnabled() is True
    assert window._cancel.isEnabled() is False


# ── 关闭拦截 ─────────────────────────────────────────────
class _FakeWorker:
    def __init__(self) -> None:
        self.stop_called = False
        self.waited = False

    def isRunning(self) -> bool:  # noqa: N802
        return True

    def stop(self) -> None:
        self.stop_called = True

    def wait(self, _ms: int) -> None:  # noqa: N802
        self.waited = True


def _close_event() -> QCloseEvent:
    return QCloseEvent()


def test_close_is_intercepted_while_task_running(window: MainWindow) -> None:
    worker = _FakeWorker()
    window._worker = worker
    event = _close_event()
    window.closeEvent(event)
    assert event.isAccepted() is False
    assert worker.stop_called is False
    assert getattr(window, "_closed", False) is False


def test_close_interception_is_not_one_shot(window: MainWindow) -> None:
    """第一次选「不关」之后，第二次关窗口仍要拦（实测踩到的 bug）。"""
    window._worker = _FakeWorker()
    for round_index in range(3):
        event = _close_event()
        window.closeEvent(event)
        assert event.isAccepted() is False, f"第 {round_index + 1} 次关闭没有被拦截"


def test_close_after_confirm_stops_task(window: MainWindow, monkeypatch) -> None:
    from PyQt5.QtWidgets import QMessageBox

    worker = _FakeWorker()
    window._worker = worker
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    event = _close_event()
    window.closeEvent(event)
    assert event.isAccepted() is True
    assert worker.stop_called is True
    assert worker.waited is True


def test_closing_twice_is_safe(qtbot, seeded_db) -> None:
    win = MainWindow(db_file=seeded_db["path"], settings=_settings())
    qtbot.addWidget(win)
    win.close()
    win.close()


# ── 偏好持久化 ───────────────────────────────────────────
def test_library_path_is_remembered(qtbot, seeded_db) -> None:
    first = MainWindow(db_file=seeded_db["path"], settings=_settings())
    qtbot.addWidget(first)
    first._db.set_pref(ui_prefs.LIBRARY_PATH, "/Music/我的库")
    first.close()
    second = MainWindow(db_file=seeded_db["path"], settings=_settings())
    qtbot.addWidget(second)
    assert second._library_edit.text() == "/Music/我的库"


# ── worker（沿用）────────────────────────────────────────
def test_worker_rejects_unknown_task(seeded_db) -> None:
    from mds.ui.workers import PipelineWorker

    with pytest.raises(ValueError):
        PipelineWorker("rm-rf", db_path=str(seeded_db["path"]), settings=_settings())


def test_worker_reports_missing_run_as_readable_error(seeded_db) -> None:
    from mds.ui.workers import PipelineWorker

    worker = PipelineWorker("analyze", db_path=str(seeded_db["path"]), settings=_settings())
    captured: list[str] = []
    worker.failed.connect(captured.append)
    worker.run()
    assert captured and "先扫描" in captured[0]


def test_qt_plugin_paths_are_resolved_by_python(monkeypatch) -> None:
    """Qt 自己推导的插件路径在**含中文的目录**下会被吃掉（实测变 ????），
    所以必须由 Python 算好再交给 Qt。"""
    import os

    from mds.ui.qt_env import configure_plugin_paths, qt_plugin_root

    monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
    monkeypatch.delenv("QT_PLUGIN_PATH", raising=False)
    root = qt_plugin_root()
    assert root is not None and (root / "platforms").is_dir()
    applied = configure_plugin_paths()
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" in applied
    assert Path(os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"]).is_dir()


def test_long_text_never_widens_the_window(window: MainWindow) -> None:
    """长文本不许撑宽窗口（写入时进度行长度在变，窗口会跟着跳）。"""
    layout = window.centralWidget().layout()
    layout.activate()
    baseline = window.minimumSizeHint().width()
    long_text = (
        "  [1/3] /home/user/Music/Sample Land!/Star Beats/单曲&专辑/"
        "[Hi-Res]Star Beats - 見本曲(テスト バージョン)最高(いこう)!/01.flac"
    ) * 2
    for text in ("就绪", long_text):
        window._progress.set_message(text)
        window._summary.setText(text)
        layout.activate()
        assert window.minimumSizeHint().width() <= baseline


def test_long_progress_line_is_elided_with_full_text_in_tooltip(window: MainWindow) -> None:
    long_text = "  [1/3] " + "很长的文件名" * 30
    window._progress.set_message(long_text)
    window.centralWidget().layout().activate()
    shown = window._progress._current.text()
    assert len(shown) < len(long_text)
    assert shown.endswith("…")
    assert window._progress.full_message == long_text
