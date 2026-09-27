"""日志。核心要求（A 层底线）：**日志里绝不能出现密钥**。

实现方式是给所有 handler 装一个脱敏 Filter：把进程内已知的密钥真值替换成 ***。
这样即使某处不小心把 key 拼进了消息，落盘前也会被抹掉。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import log_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_LOGGER_NAME = "mds"


class RedactFilter(logging.Filter):
    """把已知密钥真值从日志消息里抹掉。"""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets: list[str] = [s for s in (secrets or []) if len(s) >= 6]

    def set_secrets(self, secrets: list[str]) -> None:
        self._secrets = [s for s in secrets if len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = message
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "***")
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_redactor = RedactFilter()
_configured = False


def setup(level: str = "INFO", secrets: list[str] | None = None) -> logging.Logger:
    global _configured
    root = logging.getLogger(_LOGGER_NAME)
    if secrets:
        _redactor.set_secrets(secrets)
    if _configured:
        return root

    root.setLevel(logging.DEBUG)
    root.propagate = False

    # ⚠️ 无控制台窗口的 exe（PyInstaller console=False）在 Windows 上会把
    #    `sys.stderr` 设为 None。此时建 `StreamHandler(None)` 不会当场报错，
    #    但**每次记日志都会抛一次 AttributeError**（被 logging 吞掉，
    #    只留下一句 "Logging error" 的隐患），既白费力气又掩盖真错。
    #    所以：没有流就不装控制台 handler，文件 handler 照常。
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(getattr(logging, level.upper(), logging.INFO))
        console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        console.addFilter(_redactor)
        root.addHandler(console)

    try:
        path: Path = log_dir()
        path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path / "mds.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        file_handler.addFilter(_redactor)
        root.addHandler(file_handler)
    except OSError:
        root.warning("日志目录不可写，仅输出到控制台")

    _configured = True
    return root


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)
