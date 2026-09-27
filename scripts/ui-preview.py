#!/usr/bin/env python3
"""生成界面预览图（开发与预览用）。

用法：
    uv run python scripts/ui-preview.py [输出目录] [--run RUN_ID]

默认用**数据目录里最新的那个 run**（通常是真实样例库那次），
这样预览图里的表格是满的、有代表性 —— 而不是测试用的 3 条数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 要出的 7 张图：(文件名, 说明)
SHOTS = (
    "01-结果页",
    "02-勾选整行高亮",
    "03-切到有不同判断",
    "04-第一次打开",
    "05-待开始页",
    "06-设置页",
)


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv and not argv[0].startswith("--") else Path("docs/界面预览")
    run_arg = None
    if "--run" in argv:
        run_arg = argv[argv.index("--run") + 1]

    from PyQt5.QtWidgets import QApplication

    from mds.adapters.picard_headless import make_app_class
    from mds.storage.db import Database
    from mds.ui import prefs as ui_prefs
    from mds.ui import theme
    from mds.ui.main_window import MainWindow

    app = make_app_class(QApplication)(sys.argv[:1])
    theme.apply(app)

    db = Database(Path.home() / "Library/Application Support/音乐数据管家/mds.db")
    db.migrate()
    runs = db.list_runs(limit=5)
    if not runs:
        raise SystemExit("数据目录里还没有 run，先跑一次 mds scan")
    run_id = run_arg or str(runs[0]["id"])
    title = next((r for r in runs if r["id"] == run_id), runs[0])
    print(f"用 run {run_id}（{title['music_root']}）出图")

    ui_prefs.set_bool(db, ui_prefs.SHOW_HINTS, True)
    db.delete_pref(ui_prefs.FILTER_TAB)
    db.close()

    out.mkdir(parents=True, exist_ok=True)
    win = MainWindow(run_id=run_id)
    win.resize(1320, 860)
    win.show()
    app.processEvents()

    win._main_page.model.set_all_checked(True)
    app.processEvents()
    win.grab().save(str(out / f"{SHOTS[0]}.png"))

    win._main_page.tabs.set_current("conflict")
    app.processEvents()
    win.grab().save(str(out / f"{SHOTS[2]}.png"))

    win._library = ""
    win._library_edit.setText("")
    win.refresh()
    app.processEvents()
    win.grab().save(str(out / f"{SHOTS[3]}.png"))

    win._library = str(title["music_root"])
    win._library_edit.setText(win._library)
    win.refresh()
    app.processEvents()
    win.grab().save(str(out / f"{SHOTS[4]}.png"))

    win._show_page(1)
    app.processEvents()
    win.grab().save(str(out / f"{SHOTS[5]}.png"))

    win._show_page(0)
    win.refresh()
    win._main_page.tabs.set_current("safe")
    win._main_page.model.set_all_checked(True)
    app.processEvents()
    win.grab().save(str(out / f"{SHOTS[1]}.png"))

    print(f"已出 {len(SHOTS)} 张 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
