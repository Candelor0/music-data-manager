"""读写完整验证：补上异步回调派发（ProxyToMainEvent）后，完整跑通 7 种真实格式。

结论用途：判定"把 Picard 当库用"的可行性与胶水成本。
"""

import os
import shutil
import sys
import time
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/tmp/mds-poc/picard-src")

SRC = "/tmp/mds-poc/picard-src/test/data"
workdir = "/tmp/mds-poc/work8"
shutil.rmtree(workdir, ignore_errors=True)
os.makedirs(workdir, exist_ok=True)

from PyQt6 import QtCore  # noqa: E402

from picard import (  # noqa: E402
    PICARD_APP_NAME,
    PICARD_ORG_NAME,
)
from picard.config import (  # noqa: E402
    get_config,
    setup_config,
)
from picard.config_upgrade import run_config_upgrades  # noqa: E402
from picard.options import init_options  # noqa: E402
from picard.util import thread  # noqa: E402


class HeadlessPicardApp(QtCore.QCoreApplication):
    """关键胶水：Picard 的异步回调只被 Tagger.event() 派发，这里补上等价实现。"""

    def event(self, event):
        if isinstance(event, thread.ProxyToMainEvent):
            try:
                event.run()
            except Exception as exc:  # noqa: BLE001
                print(f"    [桩] 回调异常（已忽略）: {type(exc).__name__}: {exc}")
            return True
        return super().event(event)


QtCore.QCoreApplication.setApplicationName(PICARD_APP_NAME)
QtCore.QCoreApplication.setOrganizationName(PICARD_ORG_NAME)
app = HeadlessPicardApp(sys.argv)
init_options()
setup_config(app=app, filename=os.path.join(workdir, "picard-poc.ini"))
run_config_upgrades(get_config(), interactive=False)

import picard.formats as F  # noqa: E402
from picard.formats import registry as registry_mod  # noqa: E402
from picard.tagger import Tagger  # noqa: E402

reg = registry_mod.FormatRegistry()
for fmt in F.DEFAULT_FORMATS:
    reg.register(fmt)


class FilesDict(dict):
    """容错的 files 注册表：Picard 会 del 尚未登记的文件。"""

    def __delitem__(self, key):
        self.pop(key, None)


class StubTagger(QtCore.QObject):
    def __init__(self, fmt_registry):
        super().__init__()
        self.stopping = False
        self.format_registry = fmt_registry
        self.files = FilesDict()
        self.thread_pool = QtCore.QThreadPool()
        self.save_thread_pool = QtCore.QThreadPool()
        self.window = MagicMock()
        self.window.player = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__dict__.setdefault(name, MagicMock())


stub = StubTagger(reg)
Tagger._Tagger__instance = stub


def pump(seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(pred, timeout=20.0, label=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    raise TimeoutError(f"等待超时：{label}")


def open_and_load(path):
    f = reg.open(path)
    flag = {"done": False}
    f.load(lambda *a, **k: flag.__setitem__("done", True))
    wait_for(lambda: flag["done"], label=f"load {os.path.basename(path)}")
    pump(0.05)
    return f


TITLE = "中文标题测试 Track 01"
ARTIST = "测试艺术家"
FILES = ["test.mp3", "test.flac", "test.m4a", "test.ogg", "test.opus", "test.aiff", "test.wav"]

print("=" * 100)
print(f"{'文件':<12} {'Picard 格式类':<16} {'写入':<5} {'读回':<5} {'回滚':<5} 备注")
print("=" * 100)

rows = []
for name in FILES:
    src = os.path.join(SRC, name)
    if not os.path.exists(src):
        continue
    dst = os.path.join(workdir, name)
    shutil.copy2(src, dst)
    with open(dst, "rb") as fh:
        snapshot = fh.read()

    note = ""
    fmt_cls = write = read = roll = "—"
    try:
        f = open_and_load(dst)
        fmt_cls = type(f).__name__
        orig = f.metadata["title"]

        f.metadata["title"] = TITLE
        f.metadata["artist"] = ARTIST
        f.save()
        QtCore.QThreadPool.globalInstance().waitForDone(10000)
        stub.thread_pool.waitForDone(10000)
        stub.save_thread_pool.waitForDone(10000)
        pump(0.2)
        write = "✅"

        f2 = open_and_load(dst)
        got = f2.metadata["title"]
        read = "✅" if got == TITLE else "❌"
        if got != TITLE:
            note = f"读回={got!r}"

        with open(dst, "wb") as fh:
            fh.write(snapshot)
        f3 = open_and_load(dst)
        roll = "✅" if f3.metadata["title"] == orig else "❌"
    except Exception as exc:  # noqa: BLE001
        note = f"{type(exc).__name__}: {exc}"[:70]

    rows.append((name, write, read, roll))
    print(f"{name:<12} {fmt_cls:<16} {write:<5} {read:<5} {roll:<5} {note}")

print("=" * 100)
full = sum(1 for _, w, r, b in rows if w == "✅" and r == "✅" and b == "✅")
print(f"完整通过（写入 + 读回 + 回滚）：{full} / {len(rows)}")
