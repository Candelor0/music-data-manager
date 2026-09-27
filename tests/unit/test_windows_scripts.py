"""Windows 交付脚本的静态与语法检查。

背景（都是 Windows 实测踩出来的，写在这里防止复发）：
  1. .ps1 无 BOM + 含中文 → PowerShell 5.1 按 GBK 解析 → 假语法错误
  2. 注释块 <# 被误删 → 后续正文被当代码 → 一堆"意外的标记"
  3. TrimStart("\\") 只处理反斜杠 → 遇到正斜杠路径时相对路径多一个前缀
  4. certutil 解析文本输出不牢靠 → 换 Get-FileHash -LiteralPath
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
PS1_FILES = sorted(SCRIPTS_DIR.glob("*.ps1"))
BOM = b"\xef\xbb\xbf"


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig")


def _find_pwsh() -> str | None:
    for candidate in (
        os.environ.get("PWSH", ""),
        shutil.which("pwsh") or "",
        "/tmp/pwsh/app/pwsh",
        "/usr/local/bin/pwsh",
        "/opt/homebrew/bin/pwsh",
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def test_scripts_exist() -> None:
    assert PS1_FILES, f"没有找到 PowerShell 脚本：{SCRIPTS_DIR}"
    names = {p.name for p in PS1_FILES}
    assert {"windows-verify-setup.ps1", "windows-verify-run.ps1"} <= names


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_has_utf8_bom(path: Path) -> None:
    """PowerShell 5.1 读脚本时，若无 BOM 会按系统旧编码解析，中文变乱码报假语法错误。"""
    raw = path.read_bytes()
    assert raw[:3] == BOM, f"{path.name} 必须以 UTF-8 BOM 开头"
    assert "音乐数据管家" in raw[3:].decode("utf-8"), f"{path.name} 中文解码异常"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_block_comment_is_closed(path: Path) -> None:
    """文件必须以 <# 开头、含 #>。少一个就会把注释正文当代码执行。"""
    text = _read(path)
    assert text.lstrip().startswith("<#"), f"{path.name} 必须以 <# 块注释开头"
    assert "#>" in text, f"{path.name} 缺少 #> 结束标记"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_braces_balanced(path: Path) -> None:
    text = _read(path)
    assert text.count("{") == text.count("}"), f"{path.name} 大括号不平衡"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_no_backslash_only_trims(path: Path) -> None:
    """只 TrimStart/TrimEnd 反斜杠会在正斜杠路径上出错（相对路径被当成绝对路径）。"""
    text = _read(path)
    assert 'TrimStart("\\")' not in text, f"{path.name} 应同时处理 / 与 \\ 两种分隔符"
    assert 'TrimEnd("\\")' not in text, f"{path.name} 应同时处理 / 与 \\ 两种分隔符"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_uses_getfilehash_not_certutil(path: Path) -> None:
    text = _read(path)
    if "sha256" in text.lower():
        assert "Get-FileHash" in text
        assert "certutil" not in text, f"{path.name} 解析 certutil 文本输出不可靠"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_powershell_syntax(path: Path) -> None:
    """真用 PowerShell 解析器编译一遍（本机没装 pwsh 就跳过）。"""
    pwsh = _find_pwsh()
    if pwsh is None:
        pytest.skip("本机没有 pwsh，跳过真实语法校验（可用 PWSH=路径 指定）")
    code = (
        "$e=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$e)|Out-Null;"
        "if($e.Count){ $e | ForEach-Object { \"$($_.Extent.StartLineNumber):$($_.Message)\" }; exit 1 }"
        "else { exit 0 }"
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-Command", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"{path.name} 语法错误：\n{proc.stdout.strip()}"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_integrity_verdict_uses_product_marker(path: Path) -> None:
    """完整性判定的依据必须是「产品真的会打印的那句话」。

    踩过的坑：脚本的说明文字里也写了「完整性校验未通过」，
    结果 $log -match 匹配到了自己 → 正常情况也报 ❌（自我指涉误报）。
    产品出错时 stdout 上的标记是「（已中止，原文件未被修改）」。
    """
    text = _read(path)
    if "完整性" not in text:
        pytest.skip("该脚本不涉及完整性判定")
    assert "已中止，原文件未被修改" in text, "判定应基于产品实际打印的标记"
