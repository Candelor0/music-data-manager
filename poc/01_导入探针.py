"""导入探针：Picard 核心模块能否作为库被复用（不启动 GUI）。

用法：QT_QPA_PLATFORM=offscreen /tmp/mds-poc/.venv/bin/python p1_probe.py
"""

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/tmp/mds-poc/picard-src")

MODULES = [
    "picard",
    "picard.const",
    "picard.metadata",
    "picard.file",
    "picard.matching",
    "picard.similarity",
    "picard.cluster",
    "picard.track",
    "picard.item",
    "picard.formats",
    "picard.acoustid",
    "picard.webservice",
    "picard.coverart",
    "picard.script",
    "picard.album",
    "picard.config",
    "picard.util",
    "picard.cli",
]

print("=" * 70)
print("第 1 步：模块级 import 探测（不创建 QApplication）")
print("=" * 70)
ok, fail = [], []
for name in MODULES:
    try:
        __import__(name)
        ok.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        fail.append((name, exc))
        print(f"  FAIL  {name}  ->  {type(exc).__name__}: {exc}")

print()
print("=" * 70)
print("第 2 步：不创建 QApplication 时的可用能力")
print("=" * 70)
try:
    from picard import formats

    print(f"  picard.formats 暴露的入口: {[n for n in dir(formats) if not n.startswith('_')][:20]}")
except Exception as exc:  # noqa: BLE001
    print(f"  formats 不可用: {exc}")

try:
    from picard.formats import registry

    exts = getattr(registry, "EXTENSIONS", None)
    print(f"  注册的扩展名数量: {len(exts) if exts else 'n/a'}")
    if exts:
        print(f"  支持的扩展名: {sorted(exts)[:40]}")
    print(f"  按扩展名查格式的入口: {[n for n in dir(registry) if 'open' in n.lower() or 'format' in n.lower()]}")
except Exception as exc:  # noqa: BLE001
    print(f"  registry 不可用: {exc}")
    traceback.print_exc()

print()
print("=" * 70)
print("第 3 步：创建 QCoreApplication 后再试（Qt 事件循环依赖）")
print("=" * 70)
try:
    from PyQt6.QtCore import QCoreApplication

    app = QCoreApplication(sys.argv)
    print("  QCoreApplication 创建成功（offscreen）")
    for name in [m for m, _ in fail]:
        try:
            __import__(name)
            print(f"  PASS（补测）  {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL（补测）  {name}  ->  {type(exc).__name__}: {exc}")
except Exception as exc:  # noqa: BLE001
    print(f"  QCoreApplication 创建失败: {exc}")

print()
print("=" * 70)
print(f"小结：import 成功 {len(ok)} / {len(MODULES)}；失败 {len(fail)}")
print("=" * 70)
