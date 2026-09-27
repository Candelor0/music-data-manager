"""读写功能探针：用 Picard 官方无界面引导，真实读写标签并验证快照回滚。

用法：QT_QPA_PLATFORM=offscreen /tmp/mds-poc/.venv/bin/python p13_functional.py
"""

import os
import shutil
import sys
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/tmp/mds-poc/picard-src")

workdir = "/tmp/mds-poc/work"
shutil.rmtree(workdir, ignore_errors=True)
os.makedirs(workdir, exist_ok=True)

print("=" * 70)
print("A. 用 Picard 官方无界面引导启动（minimal_init）")
print("=" * 70)
from picard.cli._bootstrap import minimal_init  # noqa: E402

app = minimal_init(config_file=os.path.join(workdir, "picard-poc.ini"))
print(f"  ✅ minimal_init() 成功，得到 {type(app).__name__}（无 GUI）")

print()
print("=" * 70)
print("B. 构建格式注册表（复用 Picard 的 formats 层）")
print("=" * 70)
import picard.formats as F  # noqa: E402
from picard.formats import registry as registry_mod  # noqa: E402

reg = registry_mod.FormatRegistry()
for fmt in F.DEFAULT_FORMATS:
    reg.register(fmt)

exts = sorted(reg.supported_extensions())
print(f"  已注册格式数: {len(list(iter(reg)))}，扩展名 {len(exts)} 个")
print(f"  扩展名: {exts}")

print()
print("=" * 70)
print("C. 造真实音频文件（stdlib 生成 WAV，不需要任何编码器）")
print("=" * 70)
wav_path = os.path.join(workdir, "p1_test.wav")
with wave.open(wav_path, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(44100)
    w.writeframes(b"\x00\x00" * 44100)
print(f"  生成: {wav_path}  ({os.path.getsize(wav_path)} bytes)")

print()
print("=" * 70)
print("D. 读取 → 快照 → 写入 → 校验 → 回滚")
print("=" * 70)
f = reg.open(wav_path)
print(f"  registry.open() 返回类型: {type(f).__name__}")
print(f"  初始标签: title={f.metadata['title']!r} artist={f.metadata['artist']!r}")

with open(wav_path, "rb") as fh:
    snapshot = fh.read()
print(f"  1) 快照原文件: {len(snapshot)} bytes")

f.metadata["title"] = "探针测试标题"
f.metadata["artist"] = "POC 验证"
f.metadata["album"] = "测试专辑"
f.metadata["date"] = "1997"
f.metadata["genre"] = "Rock"
print(f"  2) save() 返回: {f.save()}")

f2 = reg.open(wav_path)
got = {k: f2.metadata[k] for k in ("title", "artist", "album", "date", "genre")}
print(f"  3) 重新读取: {got}")
assert got["title"] == "探针测试标题" and got["artist"] == "POC 验证", "写入未生效"
print("     ✅ 标签写入并读回成功")

with open(wav_path, "wb") as fh:
    fh.write(snapshot)
f3 = reg.open(wav_path)
restored = {k: f3.metadata[k] for k in ("title", "artist", "album")}
print(f"  4) 回滚后读取: {restored}")
assert not restored["title"], "回滚失败"
print("     ✅ 快照回滚成功（字节级还原）")

print()
print("=" * 70)
print("E. 能否挂上网络层（AcoustID / MusicBrainz 复用前提）")
print("=" * 70)
try:
    from picard.acoustid import AcoustIDClient
    from picard.webservice import WebService

    ws = WebService()
    print(f"  ✅ WebService 可实例化: {type(ws).__name__}")
    print(f"  ✅ AcoustIDClient 可导入: {AcoustIDClient.__name__}")
    print("     （真实查询需要 AcoustID API Key，属 P2）")
except Exception as exc:  # noqa: BLE001
    print(f"  ⚠️ 网络层挂载失败: {type(exc).__name__}: {exc}")

print()
print("=" * 70)
print("F. 结论")
print("=" * 70)
print("  ✅ Picard 可无界面复用：官方提供 minimal_init()，QCoreApplication 即可")
print("  ✅ formats 层可读写真实标签（WAV 实测通过），支持 24 种容器格式")
print("  ✅ 快照=原文件字节备份，回滚=字节还原，方案实测可行")
print("  ⚠️ 复用必然依赖 PyQt6（FormatRegistry 是 QObject，config 是全局单例）")
