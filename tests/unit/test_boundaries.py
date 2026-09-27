"""边界断言：core/ 必须保持纯净（补偿措施第 3 条）。

一旦有人把 Qt / Picard / 网络库引进 core，这条测试会红。
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[2] / "src" / "mds" / "core"

#: 本项目界面用 PyQt5（因 picard 2.13.3 依赖它）。core/ 一律不得碰任何 Qt 绑定，
#: 所以 PyQt5 也必须禁 —— 之前只禁了 PyQt6/PySide6，是个漏洞。
FORBIDDEN_PREFIXES = ("PyQt5", "PyQt6", "PySide2", "PySide6", "picard", "httpx", "requests", "urllib")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_core_files_exist() -> None:
    files = sorted(CORE_DIR.glob("*.py"))
    assert files, f"未找到 core 源文件：{CORE_DIR}"
    assert any(f.name == "decide.py" for f in files)


def test_core_does_not_import_forbidden_modules() -> None:
    violations: list[str] = []
    for path in sorted(CORE_DIR.glob("*.py")):
        for mod in _imported_modules(path):
            if mod.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.name}: import {mod}")
    assert not violations, "core/ 层不得依赖 Qt / Picard / 网络库：\n" + "\n".join(violations)


# ── Windows 脚本必须带 UTF-8 BOM ───────────────────────────
def test_all_powershell_scripts_have_utf8_bom() -> None:
    """所有 .ps1 必须以 UTF-8 BOM 开头。

    Windows PowerShell 5.1 读脚本时，若无 BOM 会按系统旧编码解析；
    脚本里有中文 → 中文变乱码 → 解析器报「意外的标记 }」之类的假语法错误，
    而且报错行号会串位。实测踩过两次（2026-09-22）。
    """
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    ps1 = sorted(scripts_dir.glob("*.ps1"))
    assert ps1, f"没有找到 PowerShell 脚本：{scripts_dir}"
    for path in ps1:
        raw = path.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf", f"{path.name} 必须以 UTF-8 BOM 开头"
        # 并且能正常解码出中文（防止文件本身编码坏了）
        text = raw[3:].decode("utf-8")
        assert "音乐数据管家" in text, f"{path.name} 中文解码异常"
