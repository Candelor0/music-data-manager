#!/usr/bin/env python3
"""生成应用图标（`assets/icon.png` 与 `assets/icon.ico`）。

为什么要用代码画、而不是找个现成的图：
    一是仓库里不放二进制美术资源便于审查，二是这个图标要能随时按主题色重画
    （主题色在 `ui/theme.py` 里，改一处这里就跟着变）。

为什么不用现成工具转 ico：
    Qt **只能写** bmp / png / ppm 等，**不能写 ico**（实测 supportedImageFormats
    里没有 ico）。所以这里手工拼 ICO 容器 —— 现代 Windows 支持"ICO 里直接内嵌 PNG"，
    拼起来很简单（见 `_write_ico`）。

用法：
    uv run python scripts/make-icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PROJECT_ROOT / "assets"

#: ICO 里放这几个尺寸（Windows 会用最接近当前 DPI 的那个）
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: 底色用主题主色，保持和界面一致
BG_TOP = "#3b7ae4"
BG_BOTTOM = "#1f4fb0"
NOTE = "#ffffff"


def draw_icon(size: int):
    """画一枚图标：圆角方块 + 一个八分音符。"""
    from PyQt5.QtCore import QPointF, QRectF, Qt
    from PyQt5.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # ── 圆角方块底（左上到右下的渐变）
    margin = size * 0.04
    radius = size * 0.22
    gradient = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    gradient.setColorAt(0.0, QColor(BG_TOP))
    gradient.setColorAt(1.0, QColor(BG_BOTTOM))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(
        QRectF(margin, margin, size - 2 * margin, size - 2 * margin), radius, radius
    )

    # ── 音符：符头 + 符干 + 符尾
    painter.setBrush(QColor(NOTE))
    painter.setPen(Qt.NoPen)

    # 符头（椭圆，略微倾斜，像手写音符）
    head_w, head_h = size * 0.30, size * 0.22
    head_cx, head_cy = size * 0.40, size * 0.66
    painter.save()
    painter.translate(head_cx, head_cy)
    painter.rotate(-20)
    painter.drawEllipse(QRectF(-head_w / 2, -head_h / 2, head_w, head_h))
    painter.restore()

    # 符干
    stem_w = size * 0.055
    stem_x = head_cx + head_w / 2 - stem_w * 0.9
    stem_top = size * 0.24
    stem_bottom = head_cy + head_h * 0.15
    painter.drawRect(QRectF(stem_x, stem_top, stem_w, stem_bottom - stem_top))

    # 符尾（一段向右下弯的弧）
    flag = QPainterPath()
    flag.moveTo(stem_x + stem_w, stem_top)
    flag.cubicTo(
        QPointF(size * 0.72, size * 0.26),
        QPointF(size * 0.74, size * 0.40),
        QPointF(size * 0.60, size * 0.50),
    )
    flag.cubicTo(
        QPointF(size * 0.66, size * 0.40),
        QPointF(size * 0.60, size * 0.32),
        QPointF(stem_x + stem_w, size * 0.36),
    )
    flag.closeSubpath()
    painter.drawPath(flag)

    painter.end()
    return image


def _png_bytes(image) -> bytes:
    """把 QImage 编成 PNG 字节（内存里做，不落临时文件）。

    ⚠️ `QBuffer` 必须挂在一个**存活的** `QByteArray` 上。
    写成 `QBuffer(QByteArray())` 会段错误 —— 那个临时 QByteArray 立刻被回收，
    Qt 侧留下悬空指针（实测踩到，排查了一轮）。
    """
    from PyQt5.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _write_ico(path: Path, images: list) -> None:
    """手工拼 ICO 容器：头部 + 每条目录项 + 各尺寸的 PNG 数据。

    现代 Windows（Vista 以后）支持 ICO 里直接内嵌 PNG，所以不用转 BMP。
    """
    blobs = [_png_bytes(image) for image in images]
    count = len(blobs)
    header = struct.pack("<HHH", 0, 1, count)          # 保留位, 类型=图标, 数量
    entries = b""
    offset = 6 + 16 * count                            # 数据区起始偏移
    for image, blob in zip(images, blobs, strict=True):
        side = image.width()
        # 256 在 ICO 里用 0 表示
        dimension = 0 if side >= 256 else side
        entries += struct.pack(
            "<BBBBHHII",
            dimension,          # 宽
            dimension,          # 高
            0,                  # 调色板数（真彩为 0）
            0,                  # 保留
            1,                  # 颜色平面
            32,                 # 位深
            len(blob),          # 该图数据长度
            offset,             # 该图数据偏移
        )
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> int:
    # 注意：**不需要** QApplication —— QImage / QPainter 在无图形环境下也能用，
    # 少建一个应用对象就少一类坑。
    ASSETS.mkdir(parents=True, exist_ok=True)
    png = ASSETS / "icon.png"
    ico = ASSETS / "icon.ico"

    draw_icon(256).save(str(png), "PNG")
    _write_ico(ico, [draw_icon(size) for size in ICO_SIZES])

    print(f"已生成 {png.relative_to(PROJECT_ROOT)}（256×256）")
    print(f"已生成 {ico.relative_to(PROJECT_ROOT)}（{len(ICO_SIZES)} 种尺寸：{ICO_SIZES}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
