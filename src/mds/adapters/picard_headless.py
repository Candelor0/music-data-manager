"""无界面 Picard 上下文 —— 复用 Picard 的 formats 层做标签读写的「胶水层」。

为什么需要这个模块（实测，见开发记录§2.1）：
    `picard.file.File` 的每个方法都要访问 `self.tagger`，而 2.13.3 里
    **`tagger` 就是那个 QApplication 本身**（`QtCore.QObject.tagger = self`）。
    它还要求这个对象上挂着 `thread_pool` / `save_thread_pool` / `files` / `stopping` 等属性
    （`thread.run_task()` 直接读 `QCoreApplication.instance().thread_pool`）。
    不补这一层，`save()` 会在后台线程里抛异常且被吞掉 —— 表现为「以为写完了、其实没写」。

本模块做五件事：
    1. 建（或复用）一个 HeadlessApp（QCoreApplication 子类），并挂上 Tagger 需要的那套属性
    2. 初始化 Picard 的 config（**全进程只初始化一次**）
    3. **显式关掉 Picard 一切会动文件的开关**（重命名 / 移动 / 删空目录）
    4. **打开文件后把元数据加载完**（Picard 的 load 是异步的，不等待会读到空标签）
    5. 提供「保存是异步的」这件事的等待封装

**安全承诺**：本模块只允许"在原位重写标签"，绝不重命名、移动或删除任何文件。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger("picard")

# Picard 里会动文件的开关 —— 必须全部为 False（绝不删除/移动用户文件）
_DANGEROUS_SETTINGS = ("rename_files", "move_files", "delete_empty_dirs", "clear_existing_tags")

DEFAULT_TIMEOUT_SEC = 60.0


class PicardUnavailable(RuntimeError):
    """Picard 或其 Qt 绑定不可用。"""


def _import_picard():
    try:
        import picard  # noqa: PLC0415
        import picard.config as picard_config  # noqa: PLC0415
        import picard.formats as picard_formats  # noqa: PLC0415
        from PyQt5 import QtCore  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise PicardUnavailable(
            f"无法导入 Picard/PyQt5：{exc}。请执行 `uv sync`（Picard 2.13.3 会一并安装 PyQt5）"
        ) from exc
    return picard, picard_config, picard_formats, QtCore


# ────────────────────────── 兜底对象 ──────────────────────────

class _NullObject:
    """兜底：任何属性访问/调用都返回自己，永不抛异常。

    Picard 内部有些冷分支会访问我们没实现的属性；与其让它崩，
    不如给一个无害的替身（这些分支在我们的使用路径上不会真的生效）。
    """

    def __getattr__(self, name: str) -> _NullObject:
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> _NullObject:
        return self

    def __bool__(self) -> bool:
        return False


class _TolerantDict(dict):
    """Picard 会 `del tagger.files[path]`，但正常流程里未必先登记过。"""

    def __delitem__(self, key: Any) -> None:
        self.pop(key, None)


class _NullWindow:
    player = None


_APP_CLASSES: dict[Any, Any] = {}


def existing_app(QtCore: Any) -> Any:
    """当前进程里已有的 QApplication/QCoreApplication（没有则为 None）。

    单独抽成函数是为了**可测**：守卫"拿到不合格的 app 必须报错"这条时，
    直接替换全局 `QCoreApplication.instance` 会把测试框架自己搞坏（实测踩到）。
    """
    return QtCore.QCoreApplication.instance()


def make_app_class(base: Any) -> Any:
    """按给定基类生成"Picard 友好"的 QApplication 子类。

    ⚠️ **界面必须用它来建 QApplication**：

        Picard 2.13.3 要求 `QCoreApplication.instance()` 上挂着
        `tagger_stats_changed` 信号，并且由 `event()` 派发
        `ProxyToMainEvent`（后台线程 → 主线程的回调）。

        界面里 QApplication 是**先**存在的。若界面用的是普通 `QApplication`，
        `HeadlessPicard.setup()` 只能复用它 —— 于是：
          · 没有 `tagger_stats_changed` → 报 AttributeError
          · 没有那个 `event()` → Picard 的 load/save 回调**永远不触发**
        结果就是本模块开头警告的那件事：**以为写完了、其实一个字节都没写**。

    所以界面和命令行都从这里取 app 类，保证两边的 QApplication 长得一样。
    """
    if base in _APP_CLASSES:
        return _APP_CLASSES[base]

    from PyQt5 import QtCore as _QtCore  # 类体里要用到 pyqtSignal

    class _PicardFriendlyApp(base):  # type: ignore[valid-type,misc]
        #: Picard 在 File.state 的 setter 里会 emit 它
        tagger_stats_changed = _QtCore.pyqtSignal()

        def event(self, event):
            """转发 Picard 的"后台线程 → 主线程"回调。

            ⚠️ 缺这一步，`File.load()` / `File.save()` 的完成回调**永远不会触发**
            （表现为"加载标签超时"）。
            原版由 `Tagger.event` 完成派发（2.13.3 tagger.py:781），
            我们这里是纯 QCoreApplication，必须自己补上。
            """
            try:
                from picard.util.thread import ProxyToMainEvent  # noqa: PLC0415
            except ImportError:
                return super().event(event)
            if isinstance(event, ProxyToMainEvent):
                try:
                    event.run()
                except Exception as exc:  # noqa: BLE001 - 回调异常不该拖垮事件循环
                    log.warning("主线程回调异常：%s: %s", type(exc).__name__, exc)
                return True
            return super().event(event)

        def __getattr__(self, name: str) -> Any:
            if name.startswith("_"):
                raise AttributeError(name)
            # ⚠️ 刻意**不缓存**到 __dict__：
            # 一旦缓存，首次访问就会把 _NullObject 永久装上，
            # 后面的 hasattr/is None 判断全部失效（实测：app.thread_pool 变成 _NullObject，
            # 导致 Picard 的异步任务根本没跑）。
            return _NullObject()

    _APP_CLASSES[base] = _PicardFriendlyApp
    return _PicardFriendlyApp


def _headless_app_class(QtCore: Any) -> Any:
    """命令行（无界面）用的：纯 QCoreApplication。"""
    return make_app_class(QtCore.QCoreApplication)


def _attach_tagger_attrs(app: Any, QtCore: Any) -> None:
    """把 Tagger 需要的那套属性挂到 app 上（幂等）。

    注意用 `vars(app)` 而不是 `getattr`：
    本类的 `__getattr__` 对未知属性会返回 _NullObject，用 getattr 会误以为"已有值"。
    """
    existing = vars(app)
    if "stopping" not in existing:
        app.stopping = False
    if not isinstance(existing.get("files"), _TolerantDict):
        app.files = _TolerantDict()
    for attr in ("thread_pool", "priority_thread_pool", "save_thread_pool"):
        if not isinstance(existing.get(attr), QtCore.QThreadPool):
            setattr(app, attr, QtCore.QThreadPool())
    if not isinstance(existing.get("window"), _NullWindow):
        app.window = _NullWindow()


# 无界面路径用到的设置：(名称, 类型, 默认值)
#
# ⚠️ 为什么必须**注册**而不能只赋值（实测踩过）：
# `SettingConfigSection.__getitem__` 在选项未注册时直接返回 None，
# `__setitem__` 也会直接 return —— 也就是"赋值静默失败"。
# 结果：① 安全开关其实从未生效（只是凑巧 None 是假值）；
#       ② `compare_ignore_tags` 读到 None，在后台线程里抛 TypeError。
# Picard 的选项默认值本来是由 UI 选项页面注册的，我们不加载 UI，所以要自己注册。
_OPTION_DEFS: dict[str, tuple[str, Any]] = {
    # 安全开关：会让 Picard 动文件或清空数据的，一律关掉
    "rename_files": ("bool", False),
    "move_files": ("bool", False),
    "delete_empty_dirs": ("bool", False),
    "move_additional_files": ("bool", False),
    "clear_existing_tags": ("bool", False),   # 为 True 会先清空再写 → 数据丢失
    "dont_write_tags": ("bool", False),       # 为 True 则根本不写标签
    "save_images_to_files": ("bool", False),
    "save_acoustid_fingerprints": ("bool", False),
    # 行为开关（与 Picard 默认一致，显式写出以保证不依赖 UI）
    "preserve_timestamps": ("bool", True),
    "compare_ignore_tags": ("list", []),
    "guess_tracknumber_and_title": ("bool", False),
    "ignore_existing_acoustid_fingerprints": ("bool", False),
    "query_limit": ("int", 0),
    "file_lookup_threshold": ("float", 0.7),
    # 各格式的写入开关
    "aac_save_ape": ("bool", False),
    "remove_ape_from_aac": ("bool", False),
    "fix_missing_seekpoints_flac": ("bool", False),
    "itunes_compatible_grouping": ("bool", False),
    "preserve_images": ("bool", True),
    "rating_steps": ("int", 1),
    "rating_user_email": ("text", ""),
    "remove_wave_riff_info": ("bool", False),
    "write_wave_riff_info": ("bool", True),
    "wave_riff_info_encoding": ("text", "utf-8"),
}

_OPTION_CLASS = {"bool": "BoolOption", "int": "IntOption", "float": "FloatOption",
                 "text": "TextOption", "list": "ListOption"}


class HeadlessPicard:
    """无界面 Picard 上下文（**进程内单例**）。

    用法::

        ctx = HeadlessPicard(config_file=...)
        f = ctx.open(path)          # ← 元数据已加载完
        f.metadata["title"] = "x"
        ctx.save(f)                 # ← 等到真的写完

    ⚠️ 为什么必须是单例：
      - `setup_config()` 在一个进程里**只能调一次**（第二次会重建 logger 并删掉旧对象，
        导致 `RuntimeError: wrapped C/C++ object of type TailLogger has been deleted`）；
      - Qt 不允许一个进程里存在多个 QCoreApplication。
    """

    _instance: HeadlessPicard | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> HeadlessPicard:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_file: str | Path | None = None, *, preserve_mtime: bool = True) -> None:
        if getattr(self, "_inited", False):
            return  # 单例：保留第一次传入的参数
        self._inited = True
        self._config_file = str(config_file) if config_file else None
        self._preserve_mtime = preserve_mtime
        self._app: Any = None
        self._QtCore: Any = None
        self._formats: Any = None
        self._ready = False
        self._config_setup_done = False

    # ── 生命周期 ────────────────────────────────────────────
    def __enter__(self) -> HeadlessPicard:
        self.setup()
        return self

    def __exit__(self, *exc: object) -> None:
        # 刻意不销毁 QCoreApplication：Qt 进程内单例，销毁后无法再次创建。
        return None

    def setup(self) -> None:
        if self._ready:
            return
        picard, picard_config, picard_formats, QtCore = _import_picard()
        self._QtCore = QtCore
        self._formats = picard_formats

        # 1) app（已存在就复用；Qt 不允许进程内多个）
        QtCore.QCoreApplication.setApplicationName(picard.PICARD_APP_NAME)
        QtCore.QCoreApplication.setOrganizationName(picard.PICARD_ORG_NAME)
        existing = existing_app(QtCore)
        if existing is None:
            existing = _headless_app_class(QtCore)([])
        elif not hasattr(existing, "tagger_stats_changed"):
            # 拿到的 app 不是 Picard 友好的那种 → 写标签会**静默失败**。
            # 宁可当场报错，也不要让用户以为写成功了。
            raise PicardUnavailable(
                f"现有的 QApplication（{type(existing).__name__}）缺少 Picard 需要的接口。"
                "界面请用 picard_headless.make_app_class(QtWidgets.QApplication) 创建应用对象。"
            )
        self._app = existing
        _attach_tagger_attrs(self._app, QtCore)

        # 2) config —— 全进程只初始化一次
        if not self._config_setup_done:
            picard_config.setup_config(self._app, filename=self._config_file)
            self._config_setup_done = True
        self._apply_settings(picard_config)

        # 3) tagger 就是 app 自己（2.13.3 的做法）
        QtCore.QObject.tagger = self._app  # type: ignore[attr-defined]
        QtCore.QObject.config = picard_config.config  # type: ignore[attr-defined]
        QtCore.QObject.log = picard.log  # type: ignore[attr-defined]

        self._ready = True
        log.debug("HeadlessPicard 就绪")

    def _apply_settings(self, picard_config: Any) -> None:
        """**注册并写入**无界面路径用到的全部设置。

        必须先注册（见 `_OPTION_DEFS` 的注释）：未注册的选项赋值会静默失败。
        """
        setting = picard_config.get_config().setting
        for name, (kind, default) in _OPTION_DEFS.items():
            try:
                if setting[name] is None:  # 未注册 → 自己注册
                    option_cls = getattr(picard_config, _OPTION_CLASS[kind])
                    option_cls("setting", name, default)
                setting[name] = self._preserve_mtime if name == "preserve_timestamps" else default
            except Exception as exc:  # noqa: BLE001 - 缺某项时不该致命
                log.warning("无法设置 Picard 选项 %s：%s", name, type(exc).__name__)

    def assert_safe_settings(self) -> dict[str, bool]:
        """返回关键开关当前值，供 doctor 与测试断言。"""
        _, picard_config, _, _ = _import_picard()
        setting = picard_config.get_config().setting
        return {key: bool(setting[key]) for key in _DANGEROUS_SETTINGS}

    # ── 事件循环 ────────────────────────────────────────────
    def _can_pump(self) -> bool:
        """能不能由当前线程泵事件（Qt 的线程亲和性）。

        ⚠️ 界面里写入跑在 worker 线程，而 QApplication 属于主线程：
        这时**不能**由 worker 去 `processEvents()` —— 它泵的是自己线程的队列，
        Picard 投递给主线程的回调永远轮不到，于是"加载标签超时 / 写不进去"。
        主线程自己的事件循环在跑，回调自然会被派发，worker 只管等就行。
        """
        if self._app is None or self._QtCore is None:
            return False
        try:
            return self._app.thread() == self._QtCore.QThread.currentThread()
        except (AttributeError, RuntimeError):
            return False

    def _pump_once(self) -> None:
        if self._can_pump():
            self._app.processEvents()

    def pump(self, seconds: float = 0.05) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._pump_once()
            time.sleep(0.005)

    def wait_until(self, predicate: Callable[[], bool], timeout: float = DEFAULT_TIMEOUT_SEC) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self._pump_once()
            time.sleep(0.01)
        return False

    def wait_idle(self, timeout: float = DEFAULT_TIMEOUT_SEC) -> bool:
        """等线程池排空（Picard 的 save/load 都走 QThreadPool）。"""
        ms = int(timeout * 1000)
        ok = self._QtCore.QThreadPool.globalInstance().waitForDone(ms)
        ok = bool(self._app.thread_pool.waitForDone(ms)) and ok
        ok = bool(self._app.save_thread_pool.waitForDone(ms)) and ok
        self.pump(0.2)  # 完成回调在主线程派发，需要再泵一次事件循环
        return ok

    # ── 文件操作 ────────────────────────────────────────────
    def open(self, path: str | Path, *, load: bool = True, timeout: float = DEFAULT_TIMEOUT_SEC) -> Any:
        """打开文件并（默认）**等元数据加载完**。

        ⚠️ 这是本项目最关键的一处陷阱（实测踩过）：
        `picard.formats.open_()` 返回的 File 里 **metadata 是空的** —— 标签要等 `load()`
        的回调才会填进去。若不等就 `save()`，会把自己的改动当成"整个标签"写回去，
        或者在回滚时"什么都没写"（表现为 sha256 一个字节都没变）。
        """
        if not self._ready:
            self.setup()
        if not Path(path).is_file():
            raise PicardUnavailable(f"文件不存在：{path}")
        f = self._formats.open_(str(path))
        if f is None:
            raise PicardUnavailable(f"Picard 无法打开该文件（格式不支持或已损坏）：{Path(path).name}")
        if load:
            done: list[int] = []
            f.load(lambda *a, **k: done.append(1))
            if not self.wait_until(lambda: bool(done), timeout=timeout):
                raise PicardUnavailable(f"加载标签超时：{Path(path).name}")
        return f

    def save(self, file_obj: Any, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> None:
        """触发保存并**确保它真的执行完**。

        `File.save()` 只是把任务丢进线程池后立刻返回；不等待就往下走，
        会得到"以为写完了"的假象。
        """
        if not self._ready:
            self.setup()
        file_obj.save()
        self.wait_idle(timeout)

    # ── 自检 ────────────────────────────────────────────────
    def describe(self) -> dict[str, str]:
        picard, _, _, QtCore = _import_picard()
        return {
            "picard": getattr(picard, "__version__", "?"),
            "qt": f"PyQt5 {QtCore.QT_VERSION_STR}",
            "safety": ", ".join(f"{k}={v}" for k, v in self.assert_safe_settings().items()),
        }


def make_config_file(data_dir: str | Path) -> Path:
    """Picard 的 config 放在我们自己的数据目录，避免污染用户的 Picard 配置。"""
    path = Path(data_dir) / "picard.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_available() -> tuple[bool, str]:
    """doctor 用：只判断能否导入，不做初始化。"""
    try:
        picard, _, _, QtCore = _import_picard()
    except PicardUnavailable as exc:
        return False, str(exc)
    return True, f"Picard {picard.__version__} + PyQt5 {QtCore.QT_VERSION_STR}"


def assert_no_qt6() -> None:
    """防止 PyQt6 与 PyQt5 同时被加载（两者共用 Qt 运行时符号，会崩）。"""
    import importlib.util as ilu

    if ilu.find_spec("PyQt6") is not None and ilu.find_spec("PyQt5") is not None:
        raise PicardUnavailable(
            "环境里同时存在 PyQt5 与 PyQt6 —— 两者会争抢 Qt 运行时符号导致崩溃。"
            "请只保留 PyQt5（Picard 2.13.3 依赖它）。"
        )


def env_summary() -> str:
    ok, detail = is_available()
    if not ok:
        return f"不可用：{detail}"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return detail
