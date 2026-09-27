"""许可证与发布合规守卫。

为什么要有这些测试：
    发布（让别人下载二进制）时，GPL / LGPL 要求**必须随包提供完整许可证文本**，
    且要说明派生的来源与"非官方"身份。这些是**法律义务**，不该靠人记得。
    一公开就生效，漏了不会有任何报错 —— 所以用测试盯着。

第三方的许可证文本本身（licenses/*.txt）是上游原文，不在这里逐字校验，
只校验"文件在、内容是那个许可证、且依赖清单没漏项"。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LICENSE = PROJECT_ROOT / "LICENSE"
LICENSES_DIR = PROJECT_ROOT / "licenses"
README = PROJECT_ROOT / "README.md"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
KIT_SCRIPT = PROJECT_ROOT / "scripts" / "build-exe-kit.py"
PORTABLE_SCRIPT = PROJECT_ROOT / "scripts" / "build-windows-portable.py"
PS1 = PROJECT_ROOT / "scripts" / "windows-build-exe.ps1"

#: licenses/ 里必须有的许可证全文（各自都是随包分发的一环）
REQUIRED_TEXTS = (
    "GPL-2.0.txt",  # MusicBrainz Picard、mutagen
    "LGPL-3.0.txt",  # Qt 5.15、discid
    "LGPL-2.1.txt",  # Chromaprint / fpcalc
    "PSF-Python-3.12.txt",  # 便携版内置的 Python 运行时
)


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# ── 1. 本项目自己的许可证 ────────────────────────────────
def test_license_file_is_gpl3() -> None:
    """LICENSE 必须是 GPLv3 全文（PyQt5 是 v3，v2-only 与它不能混用）。"""
    assert LICENSE.is_file(), "根目录缺 LICENSE —— GPL 要求随源码提供全文"
    text = LICENSE.read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 29 June 2007" in text, "LICENSE 不是 v3 的话与 PyQt5(GPLv3) 不兼容"
    assert len(text) > 30_000, "GPLv3 全文约 35 KB，太短说明被截断或写成了摘要"


def test_pyproject_declares_the_same_license() -> None:
    data = _pyproject()
    assert data["project"]["license"] == "GPL-3.0-or-later"


def test_declared_version_matches_code() -> None:
    """版本号单一出处：pyproject 与 src/mds/__init__.py 必须一致。"""
    import mds

    assert _pyproject()["project"]["version"] == mds.__version__


# ── 2. 第三方许可证文本齐不齐 ────────────────────────────
@pytest.mark.parametrize("name", REQUIRED_TEXTS)
def test_third_party_license_texts_present(name: str) -> None:
    path = LICENSES_DIR / name
    assert path.is_file(), f"缺 {name} —— 发布时无法满足 GPL/LGPL 的随包提供义务"
    assert path.stat().st_size > 1_000, f"{name} 太小，像是占位文件"


def test_third_party_notice_lists_every_runtime_dependency() -> None:
    """`licenses/README.md` 必须覆盖 pyproject 里的每个运行时依赖。

    添加依赖时最容易忘的就是这里 —— 忘了就是许可证披露不全。
    """
    notice = (LICENSES_DIR / "README.md").read_text(encoding="utf-8").lower()
    missing = []
    for raw in _pyproject()["project"]["dependencies"]:
        name = re.split(r"[<>=!\[; ]", raw, maxsplit=1)[0].strip().lower()
        # `picard` 在清单里写作 "MusicBrainz Picard"，`PyQt5` 直接出现，统一按子串查
        if name not in notice:
            missing.append(raw)
    assert not missing, f"licenses/README.md 没写这些依赖：{missing}"


# ── 3. 派生来源与"非官方"声明 ────────────────────────────
def test_readme_states_derivation_and_no_affiliation() -> None:
    text = README.read_text(encoding="utf-8")
    assert "GPL-3.0-or-later" in text
    assert "licenses/README.md" in text
    assert "无隶属" in text or "非官方" in text, "缺『非官方、与 Picard/MusicBrainz 无关联』的声明"
    assert "Picard" in text


def test_notice_mentions_the_services_and_their_rules() -> None:
    """用的是别人的服务，就得写清限速/需要自备 Key（既是合规也是给用户的说明）。"""
    notice = (LICENSES_DIR / "README.md").read_text(encoding="utf-8")
    for word in ("AcoustID", "MusicBrainz", "DeepSeek", "2 次/秒", "1 次/秒"):
        assert word in notice, f"licenses/README.md 缺 {word} 的说明"


# ── 4. 打包脚本真的会把许可证带上 ────────────────────────
def test_exe_kit_includes_license_and_texts() -> None:
    src = KIT_SCRIPT.read_text(encoding="utf-8")
    assert '"LICENSE"' in src, "exe 构建包里没带 LICENSE"
    assert '"licenses"' in src, "exe 构建包里没带 licenses/ 目录"


def test_ps1_copies_license_texts_next_to_the_exe() -> None:
    src = PS1.read_text(encoding="utf-8")
    assert '"LICENSE"' in src
    assert "licenses" in src
    assert "许可证" in src, "产物里没有放许可证文本的目录"


def test_ps1_never_ships_diagnostics_into_the_package() -> None:
    """自检输出里有**构建者这台机器**的用户名与路径，绝不能进要发布的目录。

    0.5.1 首次交给使用者构建时就踩过：`diagnostics.txt` 被打进 zip 发出来了。
    """
    src = PS1.read_text(encoding="utf-8")
    assert '(Join-Path $outDir "diagnostics.txt")' not in src, (
        "不许把自检输出写进产物目录（会被打进 zip 发出去）"
    )
    assert '(Join-Path $root "自检输出.txt")' in src, "自检输出应写到构建目录（不随包分发）"
    assert "diagnostics.txt" in src, "仍应保留『删掉用户自己那台机器的 diagnostics.txt』的兜底"


def test_portable_stage_ships_license_texts() -> None:
    src = PORTABLE_SCRIPT.read_text(encoding="utf-8")
    assert "licenses" in src
    assert "LICENSE" in src
    assert "GPL-3.0-or-later" in src, "便携版的许可证说明必须写明 v3"
