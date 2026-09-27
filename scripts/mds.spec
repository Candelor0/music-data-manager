# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（生成 Windows 单文件入口 + 运行时目录）。

⚠️ **必须在 Windows 上运行** —— PyInstaller 不支持交叉编译，
   所以这个文件是在 Windows 的构建脚本里被调用的，不是在 macOS 上。

几个必须显式声明的隐藏导入（都是"运行时动态导入"，PyInstaller 静态分析看不出来）：

| 包 | 为什么要 | 踩不到会怎样 |
| --- | --- | --- |
| `picard` | Picard 的 formats 注册表是运行时导入各格式模块的 | 打开文件报"格式不支持" |
| `mutagen` | 同上，各格式子模块按需导入 | 读写某些格式失败 |
| `keyring.backends` | 凭据库后端按平台动态选 | 设置页存密钥失败 |
| `mds` | 我们自己的模块也有延迟导入（CLI 子命令、UI 页面） | 某个功能点了没反应 |

`collect_data_files('picard')` 是为了带上它的翻译等非 Python 文件。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ⚠️ **不要在这里用相对路径**：PyInstaller 解析 spec 里的路径时，
#    参照的是 **spec 文件所在目录**，不是当前工作目录。
#    写成 "scripts/exe_entry.py" 会被拼成 scripts/scripts/exe_entry.py → 找不到
#    （实测踩到：构建到第 4 步报 `script ... not found`，退出码 1）。
#    所以一律基于 spec 自身位置算绝对路径。
def _find_project_root(start: Path) -> Path:
    """从 spec 所在位置往上找含 pyproject.toml 的那一层。

    为什么不写死层级：SPECPATH 在 PyInstaller 各版本里是「spec 所在目录」，
    但写死 parents[N] 容易在换版本时悄悄错位（实测就因为多算了一级，
    报 `script /private/tmp/scripts/exe_entry.py not found`）。
    用「找标记文件」的方式，怎么变都不会错。
    """
    node = start.resolve()
    for _ in range(5):
        if (node / "pyproject.toml").is_file():
            return node
        node = node.parent
    return start.resolve().parent


PROJECT_ROOT = _find_project_root(Path(SPECPATH))  # noqa: F821 - PyInstaller 注入
ENTRY = str(PROJECT_ROOT / "scripts" / "exe_entry.py")
ICON = str(PROJECT_ROOT / "assets" / "icon.ico")

#: 动态导入的模块要显式列出来
hiddenimports = (
    collect_submodules("mds")
    + collect_submodules("picard")
    + collect_submodules("mutagen")
    + collect_submodules("keyring.backends")
    # Windows 凭据管理器后端（`keyring.backends.Windows`）动态导入它。
    # 只收 keyring.backends 时，PyInstaller 的静态分析**可能**漏掉 win32ctypes，
    # 后果是设置页保存密钥时悄悄退回「明文文件」而不是系统凭据库 ——
    # 这种"降级"没有报错，最难发现，所以显式收进来。
    + collect_submodules("win32ctypes")
)

#: 随包带上的数据文件（图标给窗口与 exe 用）
#: 包内自带的**非 Python 文件**（PyInstaller 不会自动带上，必须显式列出）。
#: ⚠️ `storage/schema.sql` 漏掉的后果：打包后一启动就报「数据库不可用」，
#:    因为 `db.migrate()` 要读它建表（实测在 macOS 上冻一次就抓到了）。
datas = collect_data_files("picard") + [
    (str(PROJECT_ROOT / "assets" / "icon.ico"), "assets"),
    (str(PROJECT_ROOT / "assets" / "icon.png"), "assets"),
    (str(PROJECT_ROOT / "src" / "mds" / "storage" / "schema.sql"), "mds/storage"),
]

#: 只排除"确定用不到、而且很大"的。
#: ⚠️ 刻意**不**排除 unittest / QtQuick / QtQml 这类 —— 收益小，
#: 但有可能被别的库在运行时 import，一排除就报"模块找不到"。
excludes = [
    "tkinter",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtWebEngine",
]

a = Analysis(  # noqa: F821 - PyInstaller 注入的名字
    [ENTRY],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="音乐数据管家",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX 压缩容易触发杀毒误报，关掉
    console=False,           # 无控制台窗口（出错由 App 自己弹窗）
    disable_windowed_traceback=False,
    icon=ICON,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="音乐数据管家",
)
