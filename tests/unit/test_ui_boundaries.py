"""架构断言。

**界面从"只读"变成"可写"之后，守卫换了写法**（必须写清楚，免得后人以为守卫被削弱了）：

    早先界面是只读的，所以当时禁止 `ui/` 导入任何写入链路。
    后来界面接通了写入，那条禁令自然失效。

换成三条更准确的守卫：

    1. 功能层（core/pipeline/adapters/storage）**不得 import `mds.ui`** —— 界面可换，功能不动
    2. `ui/` **不得直接**接触写入链路（`adapters.tagwriter` / `adapters.snapshot`），
       只能经由 `pipeline.apply` / `pipeline.rollback` 这两个受控入口 ——
       快照、完整性校验、原子替换都在里面，界面绕不过去
    3. `ui/` **不得调用任何能改文件的函数**（`open` / `remove` / `replace` /
       `write_text` / `mutagen.save` …）—— 界面没有自己动手改文件的能力
    4. `ui/viewmodel.py` 与 `ui/render.py` **不得 import Qt** —— 它们是界面契约，
       换外观时保持不变
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "mds"
UI_DIR = SRC / "ui"
FUNCTION_LAYERS = ("core", "pipeline", "adapters", "storage")

#: ui/ 不许直接导入的模块（只能通过 pipeline 的受控入口间接写入）
FORBIDDEN_IN_UI = (
    "adapters.tagwriter",
    "adapters.snapshot",
)

#: 能改文件的调用名（属性调用的最后一段）
#:
#: ⚠️ `replace` 不在这个集合里 —— `str.replace` 是字符串操作，不是文件操作，
#: 一刀切会误伤（实测 glossary.replace_jargon 就被误报了）。
#: 真正要拦的是 `os.replace`（原子替换），由下面的 FORBIDDEN_RECEIVERS 处理。
FORBIDDEN_CALL_NAMES = frozenset(
    {
        "open",
        "unlink",
        "rename",
        "rmtree",
        "move",
        "copy",
        "copy2",
        "copyfile",
        "write_text",
        "write_bytes",
        "writefile",
        "truncate",
        "chmod",
        "utime",
        "save",  # mutagen / picard 的 File.save()
    }
)

#: 这些"接收者"上的任何调用都不许出现在界面层（它们就是文件系统操作）
FORBIDDEN_RECEIVERS = frozenset({"os", "shutil", "Path", "pathlib"})

#: 接收者是上面那些时，额外要拦的方法名
FORBIDDEN_RECEIVER_METHODS = frozenset({"replace", "remove", "rename", "mkdir", "rmdir"})

#: 明确放行的调用（长得像"写文件"其实不是）
#:
#: `painter.save()` 是 Qt 保存**画笔状态**（配合 restore 用），跟保存文件无关。
#: 一刀切会把自定义表格绘制误报（实测踩到）。
ALLOWED_CALLS = frozenset(
    {
        "painter.save",
        "painter.restore",
        "QPainter.save",
        "QPainter.restore",
    }
)


def _ui_files() -> list[Path]:
    files = sorted(UI_DIR.rglob("*.py"))
    assert files, f"未找到界面源文件：{UI_DIR}"
    return files


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def _called_names(tree: ast.Module) -> set[str]:
    """收集"可能改文件"的调用名，格式 `接收者.方法` 或 `函数名`。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id not in ALLOWED_CALLS:
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            receiver = func.value
            if isinstance(receiver, ast.Name):
                dotted = f"{receiver.id}.{func.attr}"
                if dotted in ALLOWED_CALLS:
                    continue
                if receiver.id in FORBIDDEN_RECEIVERS and func.attr in FORBIDDEN_RECEIVER_METHODS:
                    names.add(dotted)
                    continue
            names.add(func.attr)
    return names


# ── 1. 功能层不得依赖界面 ────────────────────────────────
def test_function_layer_does_not_import_ui() -> None:
    """界面可以换、可以重写，功能层一行都不该动。"""
    violations: list[str] = []
    for layer in FUNCTION_LAYERS:
        for path in sorted((SRC / layer).rglob("*.py")):
            for mod in _imported_modules(_tree(path)):
                normalized = mod.lstrip(".")
                if normalized == "ui" or normalized.endswith("mds.ui") or "ui." in normalized:
                    violations.append(f"{layer}/{path.name}: import {mod}")
    assert not violations, (
        "功能层不得依赖界面层（否则换外观就得动功能）：\n" + "\n".join(violations)
    )


# ── 2. 界面不得直接碰写入链路 ────────────────────────────
def test_ui_does_not_import_low_level_write_chain() -> None:
    """只能经由 pipeline.apply / pipeline.rollback —— 快照与完整性校验在里面。"""
    violations: list[str] = []
    for path in _ui_files():
        for mod in _imported_modules(_tree(path)):
            normalized = mod.lstrip(".")
            if normalized.endswith(FORBIDDEN_IN_UI):
                violations.append(f"{path.name}: import {mod}")
    assert not violations, (
        "界面不得直接接触底层写入组件（会绕过快照与校验）：\n" + "\n".join(violations)
    )


# ── 3. 界面不得自己改文件 ────────────────────────────────
def test_ui_does_not_call_file_mutating_functions() -> None:
    violations: list[str] = []
    for path in _ui_files():
        for name in _called_names(_tree(path)):
            if name in FORBIDDEN_CALL_NAMES or name in {
                f"{r}.{m}" for r in FORBIDDEN_RECEIVERS for m in FORBIDDEN_RECEIVER_METHODS
            }:
                violations.append(f"{path.name}: 调用了 {name}()")
    assert not violations, "界面不得出现任何可能改写文件的调用：\n" + "\n".join(violations)


def test_string_replace_is_not_flagged() -> None:
    """`str.replace` 是字符串操作，不该被误判成文件操作（实测误报过）。"""
    tree = ast.parse('x = "a b".replace(" ", "")')
    assert "os.replace" not in _called_names(tree)
    assert "replace" in _called_names(tree)  # 名字在，但不在拦截集合里


def test_os_replace_is_flagged() -> None:
    tree = ast.parse("import os\nos.replace(1, 2)")
    assert "os.replace" in _called_names(tree)


def test_painter_save_is_not_flagged() -> None:
    """`painter.save()` 保存的是画笔状态，不是文件（实测误报过）。"""
    tree = ast.parse("painter.save()")
    assert _called_names(tree) == set()


# ── 4. 界面契约不含 Qt ───────────────────────────────────
def test_viewmodel_and_render_do_not_import_qt() -> None:
    for name in ("viewmodel.py", "render.py"):
        mods = _imported_modules(_tree(UI_DIR / name))
        bad = [m for m in mods if m.startswith(("PyQt5", "PyQt6", "PySide"))]
        assert not bad, f"{name} 不应依赖 Qt（它是界面契约）：{bad}"


# ── 5. 界面文件齐全 ──────────────────────────────────────
def test_ui_directory_exists() -> None:
    for name in ("main_window.py", "viewmodel.py", "render.py", "prefs.py", "workers.py"):
        assert (UI_DIR / name).is_file(), f"缺少界面文件 {name}"
    for name in ("todo.py", "list.py", "album.py", "settings.py"):
        assert (UI_DIR / "pages" / name).is_file(), f"缺少页面 {name}"
