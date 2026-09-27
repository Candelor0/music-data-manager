"""让 Qt 能找到自己的插件 —— 尤其是**安装路径含中文时**。

踩过的坑（实测，2026-09-22）：
    Qt 通过 `QLibraryInfo` 用**窄字符编码**推导插件目录。工程路径里带中文时，
    推导出来的路径会变成：

        /Users/<用户名>/Documents/projects/????/ai-product-starter-kit-main/...

    中文被吃掉了，于是启动时报：
        Could not find the Qt platform plugin "cocoa" in ""
        This application failed to start because no Qt platform plugin could be initialized.

    这个坑在 Windows 上同样会踩到 —— 只要程序装在 `D:\\软件\\音乐数据管家\\` 这种
    中文目录里就复现。而我们的交付形态恰恰是"双击即用、用户自己选安装位置"。

解法：用 Python 算路径（Python 处理 Unicode 没问题），再把结果通过环境变量交给 Qt。
`QT_PLUGIN_PATH` 管所有插件类别（platforms / imageformats / styles …），
`QT_QPA_PLATFORM_PLUGIN_PATH` 专门管平台插件。
"""

from __future__ import annotations

import os
from pathlib import Path


def qt_plugin_root() -> Path | None:
    """PyQt5 自带的 Qt 插件根目录；找不到就返回 None。"""
    try:
        import PyQt5
    except ImportError:  # 没装 PyQt5 的环境
        return None
    root = Path(PyQt5.__file__).resolve().parent / "Qt5" / "plugins"
    return root if root.is_dir() else None


def configure_plugin_paths() -> dict[str, str]:
    """补齐插件路径环境变量，返回本次实际设置的值。

    只补不覆盖：用户/系统已经设好的值优先。
    """
    root = qt_plugin_root()
    if root is None:
        return {}

    applied: dict[str, str] = {}

    platforms = root / "platforms"
    if platforms.is_dir() and not os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)
        applied["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)

    # 追加而不是覆盖：别的工具可能也往这里加了路径
    existing = [p for p in os.environ.get("QT_PLUGIN_PATH", "").split(os.pathsep) if p]
    if str(root) not in existing:
        existing.append(str(root))
        value = os.pathsep.join(existing)
        os.environ["QT_PLUGIN_PATH"] = value
        applied["QT_PLUGIN_PATH"] = value

    return applied
