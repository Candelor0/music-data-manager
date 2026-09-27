"""`mds gui` 的入口：建 QApplication 并打开主窗口。

只读界面 —— 这里不会、也没有能力修改任何音乐文件。
"""

from __future__ import annotations

import sys

from ..config import load_settings
from ..logging_setup import setup as setup_logging


def app_icon():
    """应用图标（`assets/icon.ico`，没有就退回 png）。

    图标是用 `scripts/make-icon.py` 画出来的，仓库里不放二进制美术资源。
    """
    from PyQt5.QtGui import QIcon

    from .. import config

    for base in (config.resource_dir(), config.project_root()):
        for name in ("icon.ico", "icon.png"):
            path = base / "assets" / name
            if path.is_file():
                return QIcon(str(path))
    return None


def run_gui(run_id: str | None = None) -> int:
    # ⚠️ 必须在 import/构造 QApplication **之前**做：Qt 找插件的路径一旦定下就改不了。
    # 程序装在中文目录时，Qt 自己推导出来的路径会把中文吃掉（见 qt_env.py 顶部注释）。
    from .qt_env import configure_plugin_paths

    configure_plugin_paths()

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    # ⚠️ 必须用 Picard 友好的 QApplication 子类：
    # Picard 要 `QCoreApplication.instance()` 上带 tagger_stats_changed 信号，
    # 并由 event() 派发后台线程回调。用普通 QApplication 的话，写标签会
    # **静默失败** —— 界面显示"写完了"，文件一个字节没变。
    from ..adapters.picard_headless import make_app_class

    app_class = make_app_class(QApplication)

    settings = load_settings()
    setup_logging("INFO", settings.secret_values())

    # 高 DPI 屏幕不糊（必须在 QApplication 之前设置）
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = app_class(sys.argv[:1])

    from .main_window import MainWindow

    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)   # 任务栏 / Alt+Tab 用这个

    try:
        window = MainWindow(run_id=run_id, settings=settings)
    except Exception as exc:  # noqa: BLE001
        # 便携版用 pythonw 启动（没有黑窗口），所以出错必须弹出来给人看，
        # 否则用户只会看到"双击了没反应"
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.critical(
            None,
            "启动失败",
            f"{type(exc).__name__}: {exc}\n\n"
            "可以双击同目录的「诊断工具.bat」查看详细环境信息。",
        )
        return 1

    if icon is not None:
        window.setWindowIcon(icon)
    window.show()
    window.raise_()
    window.activateWindow()

    if not owns_app:  # 测试环境下已有 QApplication，交回调用方
        return 0
    return int(app.exec_())
