"""应用图标测试。

图标是 `scripts/make-icon.py` **用代码画**出来的（仓库里不放二进制美术资源），
再手工拼成 ICO 容器 —— 所以这里把容器结构也验一遍。
"""

from __future__ import annotations

import struct
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "assets"
ICO = ASSETS / "icon.ico"
PNG = ASSETS / "icon.png"

#: Windows 会用最接近当前 DPI 的那一档，所以必须有多档尺寸
EXPECTED_SIZES = (16, 24, 32, 48, 64, 128, 256)


def test_icon_files_exist() -> None:
    assert ICO.is_file(), "缺少 assets/icon.ico（跑 scripts/make-icon.py 生成）"
    assert PNG.is_file(), "缺少 assets/icon.png"


def test_png_is_valid_and_square() -> None:
    raw = PNG.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "png 文件头不对"
    # IHDR 里的宽高（大端）
    width, height = struct.unpack(">II", raw[16:24])
    assert width == height == 256, f"png 应当是 256×256，实际 {width}×{height}"


def test_ico_container_structure() -> None:
    """ICO 头 + 每条目录项 + 各档 PNG 数据，偏移与长度都要自洽。"""
    raw = ICO.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert reserved == 0 and kind == 1, "ICO 头不对（应为 保留位0 / 类型1）"
    assert count == len(EXPECTED_SIZES), f"应当有 {len(EXPECTED_SIZES)} 档尺寸，实际 {count}"

    for index in range(count):
        start = 6 + 16 * index
        width, height, _colors, _res, planes, bits, size, offset = struct.unpack(
            "<BBBBHHII", raw[start : start + 16]
        )
        side = width or 256          # 256 在 ICO 里写作 0
        assert side == EXPECTED_SIZES[index], f"第 {index} 档尺寸不对：{side}"
        assert height or 256 == side, "宽高应一致"
        assert planes == 1 and bits == 32, "应为 32 位真彩"
        assert raw[offset : offset + 8] == b"\x89PNG\r\n\x1a\n", "数据应当是内嵌 PNG"
        assert size > 0 and offset + size <= len(raw), "数据偏移/长度越界"


def test_generated_icon_matches_script() -> None:
    """仓库里的图标应当能由脚本重新生成（保证它可复现，而不是手塞进去的）。"""
    script = Path(__file__).resolve().parents[2] / "scripts" / "make-icon.py"
    text = script.read_text(encoding="utf-8")
    for marker in ("drawRoundedRect", "ICO_SIZES", "_write_ico"):
        assert marker in text, f"make-icon.py 里应当有 {marker}"
