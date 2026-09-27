"""密钥存取：系统凭据库 / 本地文件 / 内存替身。

设计目标（设置里填一次，关掉软件也不用重输）：

    优先用**系统凭据库**（Windows 凭据管理器 / macOS 钥匙串 / Linux SecretService）——
    这是系统专门存密码的地方，加密、不落明文。
    凭据库不可用时**不得静默失败**，退回数据目录下的本地文件，并把
    「存在哪、是明文」如实告诉用户（`location()` / `is_secure`）。

三条纪律：

1. **绝不回显密钥**：本模块不打印、不写日志、不把值放进异常消息。
2. **测试不得触碰真实凭据库**：测试一律注入 `MemoryStore`（见 tests/unit/test_secrets.py）。
3. **不依赖 config**：服务名与数据目录由调用方传入，避免 config ↔ adapters 循环导入。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger("mds.secrets")

#: 需要持久化的密钥名（**只存这两把**，其它配置走 .env 或 preferences 表）
SECRET_KEYS: tuple[str, ...] = ("ACOUSTID_API_KEY", "DEEPSEEK_API_KEY")

#: 文件兜底时的文件名
FALLBACK_FILENAME = "secrets.json"


@runtime_checkable
class SecretStore(Protocol):
    """密钥存储。三种实现可互换。"""

    #: 给人看的位置说明，例如「Windows 凭据管理器」
    label: str
    #: 是否加密存储（界面要如实显示）
    is_secure: bool

    def get(self, key: str) -> str | None:
        """取密钥；没有或不可用都返回 None（**不抛异常**，不要因为它导致启动失败）。"""

    def set(self, key: str, value: str) -> None:
        """存密钥；value 为空串表示删除。"""

    def delete(self, key: str) -> None:
        """删除密钥。"""

    def location(self) -> str:
        """给用户看的具体位置（凭据库里没有路径，给个可读描述即可）。"""


class MemoryStore:
    """内存替身：测试专用，**绝不触碰真实凭据库**。"""

    label = "内存（仅测试）"
    is_secure = True

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(initial or {})

    def get(self, key: str) -> str | None:
        return self._data.get(key) or None

    def set(self, key: str, value: str) -> None:
        if value:
            self._data[key] = value
        else:
            self._data.pop(key, None)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def location(self) -> str:
        return "内存（进程结束即消失）"


class FileStore:
    """兜底：数据目录下的 `secrets.json`（**明文**）。

    仅当系统凭据库不可用时使用，并且必须在界面上明确告知用户「这是明文、存在哪」。
    """

    label = "本地文件"
    is_secure = False

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 尽力收紧权限（Windows 上 chmod 作用有限，所以界面必须如实说"明文"）
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, key: str) -> str | None:
        return self._read().get(key) or None

    def set(self, key: str, value: str) -> None:
        data = self._read()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        self._write(data)

    def delete(self, key: str) -> None:
        self.set(key, "")

    def location(self) -> str:
        return str(self.path)


class KeyringStore:
    """系统凭据库（Windows 凭据管理器 / macOS 钥匙串 / Linux SecretService）。"""

    label = "系统凭据库"
    is_secure = True

    def __init__(self, service: str) -> None:
        self.service = service

    @staticmethod
    def available() -> bool:
        """凭据库是否真的能用。企业电脑有时会禁用，必须能测出来。"""
        try:
            import keyring  # noqa: PLC0415
            from keyring.errors import KeyringError  # noqa: PLC0415
        except ImportError:
            return False
        try:
            backend = keyring.get_keyring()
            # fail backend：环境里没有任何可用凭据库时会退到它，等于不可用
            name = f"{type(backend).__module__}.{type(backend).__name__}"
            if "fail" in name.lower():
                log.debug("凭据库后端不可用：%s", name)
                return False
            return True
        except (KeyringError, RuntimeError, OSError) as exc:  # pragma: no cover - 环境相关
            log.debug("探测凭据库失败：%s", type(exc).__name__)
            return False

    def get(self, key: str) -> str | None:
        try:
            import keyring  # noqa: PLC0415
            from keyring.errors import KeyringError  # noqa: PLC0415
        except ImportError:
            return None
        try:
            return keyring.get_password(self.service, key) or None
        except (KeyringError, RuntimeError, OSError) as exc:
            # 绝不把值或异常原文抛给上层：它可能包含系统路径等细节
            log.debug("读取凭据库失败（%s）：%s", key, type(exc).__name__)
            return None

    def set(self, key: str, value: str) -> None:
        try:
            import keyring  # noqa: PLC0415
            from keyring.errors import KeyringError  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("未安装 keyring，无法写入系统凭据库") from exc
        try:
            if value:
                keyring.set_password(self.service, key, value)
            else:
                self.delete(key)
        except (KeyringError, RuntimeError, OSError) as exc:
            raise RuntimeError(f"写入系统凭据库失败：{type(exc).__name__}") from exc

    def delete(self, key: str) -> None:
        try:
            import keyring  # noqa: PLC0415
            from keyring.errors import KeyringError, PasswordDeleteError  # noqa: PLC0415
        except ImportError:
            return
        try:
            keyring.delete_password(self.service, key)
        except PasswordDeleteError:
            pass  # 本来就没有，视为成功
        except (KeyringError, RuntimeError, OSError) as exc:
            log.debug("删除凭据失败（%s）：%s", key, type(exc).__name__)

    def location(self) -> str:
        return f"系统凭据库（{self.service}）"


def default_store(*, service: str, data_dir: str | Path) -> SecretStore:
    """按可用性挑一个：凭据库优先，不可用则退回本地文件。"""
    if KeyringStore.available():
        return KeyringStore(service)
    log.info("系统凭据库不可用，密钥将存放在本地文件（明文）")
    return FileStore(Path(data_dir) / FALLBACK_FILENAME)


def masked(value: str, *, keep: int = 4) -> str:
    """把密钥变成可显示的样子：**只露末 4 位**，例如 `••••••1234`。

    这是对外承诺过的行为（界面上的密钥默认掩码，防截图/录屏泄露）。
    **永远不要**在界面上直接显示完整值。
    """
    if not value:
        return ""
    # 太短就别露 —— 5 位的值露 4 位等于泄露大半
    if len(value) < keep * 2:
        return "•" * len(value)
    return "•" * 6 + value[-keep:]
