"""日志初始化的边界情况。

重点：**无控制台窗口的 exe**（PyInstaller `console=False`）在 Windows 上
会把 `sys.stderr` 设为 `None`（这是 CPython 的行为，不是 PyInstaller 干的）。
此时 `logging.StreamHandler(None)` 不会当场报错，但**每次记日志都会抛一次
AttributeError**（被 logging 吞掉，只留下一句隐患），既白费力气又掩盖真错。

⚠️ 写这组测试时踩到的坑：pytest 的 `caplog` 机制会往被测 logger 上挂它自己的
`LogCaptureHandler`。所以断言**不能**写"root.handlers 里没有控制台 handler"
（会把 pytest 的算进来，在全量跑时假失败），而要看"本次调用**新增**了哪些 handler"。
fixture 也只清理本次新增的 —— 粗暴清空会把 pytest 的捕获 handler 一起干掉，
连累后面的用例。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from mds import logging_setup


def _own(handler: logging.Handler) -> bool:
    """是不是"我们自己的"文件 handler（RotatingFileHandler 带 baseFilename）。"""
    return hasattr(handler, "baseFilename")


@pytest.fixture
def mds_logger() -> Iterator[logging.Logger]:
    """记录调用前已有的 handler，测试结束后只移除新增的。"""
    logger = logging.getLogger("mds")
    before = set(map(id, logger.handlers))
    logging_setup._configured = False
    yield logger
    for handler in list(logger.handlers):
        if id(handler) not in before:
            logger.removeHandler(handler)
            handler.close()
    logging_setup._configured = False


def _added(logger: logging.Logger, before_ids: set[int]) -> list[logging.Handler]:
    return [h for h in logger.handlers if id(h) not in before_ids]


def test_no_console_handler_when_stderr_is_none(mds_logger, monkeypatch, tmp_path) -> None:
    """`sys.stderr is None` 时不装控制台 handler，但文件 handler 照常。"""
    before = set(map(id, mds_logger.handlers))
    monkeypatch.setattr(logging_setup.sys, "stderr", None)
    monkeypatch.setattr(logging_setup, "log_dir", lambda: tmp_path)

    logging_setup.setup("INFO")

    added = _added(mds_logger, before)
    console = [h for h in added if isinstance(h, logging.StreamHandler) and not _own(h)]
    assert not console, "没有 stderr 就不该装控制台 handler"
    assert any(_own(h) for h in added), "文件 handler 仍要在"


def test_logging_does_not_raise_when_stderr_is_none(mds_logger, monkeypatch, tmp_path) -> None:
    """记一条日志不能抛异常（真跑一遍，而不是只看 handler 列表）。"""
    monkeypatch.setattr(logging_setup.sys, "stderr", None)
    monkeypatch.setattr(logging_setup, "log_dir", lambda: tmp_path)

    logger = logging_setup.setup("DEBUG")
    logger.info("这条日志应当安全落地")  # 抛异常这里就红了

    log_file = tmp_path / "mds.log"
    assert log_file.is_file()
    assert "这条日志应当安全落地" in log_file.read_text(encoding="utf-8")


def test_console_handler_present_when_stderr_exists(mds_logger, monkeypatch, tmp_path) -> None:
    """有正常 stderr 时，控制台 handler 要在。"""
    import io

    before = set(map(id, mds_logger.handlers))
    monkeypatch.setattr(logging_setup.sys, "stderr", io.StringIO())
    monkeypatch.setattr(logging_setup, "log_dir", lambda: tmp_path)

    logging_setup.setup("INFO")

    added = _added(mds_logger, before)
    assert any(isinstance(h, logging.StreamHandler) and not _own(h) for h in added)


def test_secrets_are_redacted(mds_logger, monkeypatch, tmp_path) -> None:
    """密钥绝不能进日志（A 层底线）。"""
    secret = "sk-abcdef0123456789"
    monkeypatch.setattr(logging_setup, "log_dir", lambda: tmp_path)
    logger = logging_setup.setup("DEBUG", secrets=[secret])
    logger.info("拿到的 key 是 %s", secret)

    text = (tmp_path / "mds.log").read_text(encoding="utf-8")
    assert secret not in text
    assert "***" in text
