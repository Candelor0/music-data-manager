#!/usr/bin/env python3
"""给界面截图量明暗分布 —— 一个**报警器**，不是门限。

⚠️ 定位说明：一开始想用这条线当"层次感"的门限，
   但试了几轮发现**它替代不了眼睛** —— 比如"设置页的白色输入框"和"一整块空白"
   在数字上很像，实际观感完全不同。

   所以它现在只干一件事：**报出"一张 A4 纸"的极端特征**
   （整屏刷白 + 几乎没有结构灰）。真正的判断靠看图。

为什么要有这个东西
------------------
界面改版后整屏缺少分区、观感廉价。量化后发现，根因**不是"白"**：

    改版前：结构性中灰占 **23.8%**  → 看着有骨有肉
    改版后：结构性中灰占 **11.2%**  → 像一张 A4 纸
    （平均亮度反而从 249 降到 247 —— 更"不白"了，但更难看）

**层次与分区感只能靠中灰来体现**：行与行的分隔、面板与面板的边界、
表头和内容的区别，全是中灰在干活。

所以定下这条可测量的线（不达标不算做完）：

    结构性中灰（亮度 200~245）占比 18%~55%   ← 分区带、边框、斑马纹
    纯白（亮度 > 250）占比 ≤ 70%             ← 别整屏刷白

用法：
    uv run python scripts/ui-snapshot-metrics.py 图1.png 图2.png ...
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 参考线（对照：改版前 中灰 23.8% / 纯白 74.1%；失败版 中灰 11.2% / 纯白 83.5%）
#: 只报**极端情况**：整屏刷白 + 几乎没有结构灰 —— 那正是"一张 A4 纸"的特征
#: （实测失败版：纯白 83%、结构灰 2%）。其余交给眼睛判断。
FLAT_WHITE = 70.0
FLAT_STRUCTURE = 8.0

SAMPLE_STEP = 3  # 每 3 像素取一个点（够准，又快）


def luminance(r: int, g: int, b: int) -> float:
    return (r * 299 + g * 587 + b * 114) / 1000


def is_neutral(r: int, g: int, b: int, *, tolerance: int = 26) -> bool:
    """是不是「中性灰」（而不是带颜色的淡蓝/淡绿底）。

    为什么要分：第一版把「勾选行的淡蓝底」也算进结构性中灰，
    全选之后数字直接冲到 59% —— 但那是**颜色**不是**结构**。
    结构指的是边框、行分隔、表头带这类中性灰。
    """
    return max(r, g, b) - min(r, g, b) <= tolerance


def analyse(path: Path) -> dict:
    from PyQt5.QtGui import QImage
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841

    image = QImage(str(path))
    if image.isNull():
        raise SystemExit(f"读不到图片：{path}")

    bands = {"纯白": 0, "浅灰": 0, "结构": 0, "彩色": 0, "暗": 0}
    total = 0
    for y in range(0, image.height(), SAMPLE_STEP):
        for x in range(0, image.width(), SAMPLE_STEP):
            pixel = image.pixel(x, y)
            r, g, b = (pixel >> 16) & 255, (pixel >> 8) & 255, pixel & 255
            lum = luminance(r, g, b)
            total += 1
            if lum > 245:
                bands["纯白"] += 1
            elif lum > 238:
                # 接近白但还不是白：桌面底、分区块的内容底
                bands["浅灰"] += 1
            elif lum >= 200:
                # 真正撑边界的"结构灰"：分区带、表头、边框、分隔线
                bands["结构" if is_neutral(r, g, b) else "彩色"] += 1
            else:
                bands["暗"] += 1

    pct = {name: count / total * 100 for name, count in bands.items()}
    flat = pct["纯白"] > FLAT_WHITE and pct["结构"] < FLAT_STRUCTURE
    ok = not flat
    return {"path": path, "size": f"{image.width()}×{image.height()}", "pct": pct, "ok": ok}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    print(
        f"报警线：整屏刷白（>{FLAT_WHITE}%）且几乎没有结构灰（<{FLAT_STRUCTURE}%）"
        "　← 这就是「一张 A4 纸」的特征"
    )
    print()
    print(f"{'图片':<40}{'纯白':>7}{'浅灰':>7}{'结构':>7}{'彩色':>7}{'暗':>7}   结果")
    print("-" * 92)
    all_ok = True
    for raw in argv:
        stats = analyse(Path(raw))
        pct = stats["pct"]
        verdict = "✅ 看不出问题" if stats["ok"] else "❌ 像一张 A4 纸（整屏刷白、没有结构）"
        all_ok &= stats["ok"]
        name = stats["path"].name
        print(
            f"{name[:38]:<40}{pct['纯白']:>6.1f}%{pct['浅灰']:>6.1f}%"
            f"{pct['结构']:>6.1f}%{pct['彩色']:>6.1f}%{pct['暗']:>6.1f}%   {verdict}"
        )
    print()
    print("参考：改版前 中灰 23.8%／纯白 74.1%　｜　失败版 中灰 11.2%／纯白 83.5%")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
