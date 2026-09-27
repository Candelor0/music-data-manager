"""无界面 Picard 适配层测试。

**这里守着一个曾经静默失败的坑**：

    Picard 2.13.3 要求 `QCoreApplication.instance()` 上挂着
    `tagger_stats_changed` 信号，并由 `event()` 派发 `ProxyToMainEvent`
    （后台线程 → 主线程的回调）。

    界面里 QApplication 是**先**存在的。若界面用的是普通 QApplication，
    `HeadlessPicard.setup()` 只能复用它 —— 于是 Picard 的 load/save 回调
    **永远不触发**，写入静默失败：界面显示"写完了"，文件一个字节没变。

所以这里断言：① 我们生成的 app 类带那两样；② 拿到不合格的 app 时**必须报错**，
不许蒙混过关；③ worker 线程不许去 pump 主线程的事件队列。
"""

from __future__ import annotations

import pytest

from mds.adapters.picard_headless import HeadlessPicard, PicardUnavailable, make_app_class

pytest.importorskip("PyQt5")

from PyQt5 import QtCore  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


def test_app_class_has_picard_信号_and_event_dispatch() -> None:
    cls = make_app_class(QtCore.QCoreApplication)
    assert issubclass(cls, QtCore.QCoreApplication)
    assert hasattr(cls, "tagger_stats_changed")
    assert "event" in cls.__dict__, "必须自己实现 event() 来派发 Picard 的回调"


def test_app_class_can_be_built_on_qapplication() -> None:
    """界面用的那一类：既要能开窗，也要能让 Picard 工作。"""
    cls = make_app_class(QApplication)
    assert issubclass(cls, QApplication)
    assert hasattr(cls, "tagger_stats_changed")


def test_app_class_is_cached_per_base() -> None:
    """同一个基类只生成一个类 —— Qt 的元类重复注册会出问题。"""
    assert make_app_class(QApplication) is make_app_class(QApplication)
    assert make_app_class(QtCore.QCoreApplication) is not make_app_class(QApplication)


def _fresh_context() -> HeadlessPicard:
    """绕开单例，造一个干净的上下文（单例会让测试互相污染）。"""
    ctx = HeadlessPicard.__new__(HeadlessPicard)
    ctx._inited = True
    ctx._ready = False
    ctx._app = None
    ctx._QtCore = None
    ctx._formats = None
    ctx._config_file = None
    ctx._preserve_mtime = True
    ctx._config_setup_done = False
    return ctx


def test_setup_rejects_incompatible_application(monkeypatch) -> None:
    """拿到不合格的 QApplication 时必须**当场报错**，而不是静默写不进去。"""

    class StubApp:  # 既没有 tagger_stats_changed，也没有 event 派发
        pass

    from mds.adapters import picard_headless

    monkeypatch.setattr(picard_headless, "existing_app", lambda _qt: StubApp())
    ctx = _fresh_context()
    ctx._inited = True
    ctx._ready = False
    ctx._app = None
    ctx._QtCore = None
    ctx._formats = None
    ctx._config_file = None
    ctx._preserve_mtime = True
    ctx._config_setup_done = False

    with pytest.raises(PicardUnavailable, match="缺少 Picard 需要的接口"):
        ctx.setup()


def test_current_test_app_is_picard_friendly(qapp) -> None:
    """测试环境里的 QApplication 必须与线上一致（见 conftest 的 qapp_cls）。

    不请求 `qapp` 夹具的话，pytest-qt 不会真的创建应用对象（它是懒创建的），
    这条断言就会测了个寂寞。
    """
    assert qapp is not None
    assert hasattr(qapp, "tagger_stats_changed"), (
        f"测试用的 QApplication（{type(qapp).__name__}）不是 Picard 友好的那一类 —— "
        "测试环境与线上不一致，写入相关的问题测不出来"
    )


def test_can_pump_true_on_own_thread(qapp) -> None:
    """主线程（app 所属线程）可以泵事件。"""
    ctx = _fresh_context()
    ctx._app = qapp
    ctx._QtCore = QtCore
    assert ctx._can_pump() is True


def test_can_pump_false_without_app() -> None:
    """连 app 都没有时不能瞎泵。"""
    ctx = _fresh_context()
    assert ctx._can_pump() is False


def test_can_pump_false_from_worker_thread() -> None:
    """**worker 线程不许 pump 主线程的事件队列**。

    泵错了对象，Picard 投递给主线程的回调就永远轮不到 ——
    表现为"加载标签超时 / 写不进去"。
    """

    class FakeApp:
        def thread(self):
            return "主线程"

        def processEvents(self):  # noqa: N802
            raise AssertionError("worker 线程不该 pump 主线程的事件队列")

    ctx = _fresh_context()
    ctx._app = FakeApp()
    ctx._QtCore = QtCore
    assert ctx._can_pump() is False
    ctx.pump(0.02)  # 不该抛异常
