"""密钥存取层测试。

**铁律：这些测试绝不触碰真实凭据库**（否则 CI 会读开发者的钥匙串，还会弹授权框）。
需要凭据库的地方一律用 monkeypatch 或内存替身。
"""

from __future__ import annotations

import json

import pytest

from mds.adapters.secrets import (
    SECRET_KEYS,
    FileStore,
    KeyringStore,
    MemoryStore,
    default_store,
    masked,
)


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    """兜底保险：任何测试若真去读钥匙串，立刻失败并说清原因。"""

    class Boom:
        @staticmethod
        def get_password(*_a, **_k):
            raise AssertionError("测试不得触碰真实凭据库！请注入 MemoryStore")

        @staticmethod
        def set_password(*_a, **_k):
            raise AssertionError("测试不得触碰真实凭据库！请注入 MemoryStore")

        @staticmethod
        def delete_password(*_a, **_k):
            raise AssertionError("测试不得触碰真实凭据库！请注入 MemoryStore")

        @staticmethod
        def get_keyring():
            raise AssertionError("测试不得触碰真实凭据库！请注入 MemoryStore")

    monkeypatch.setattr("keyring.get_password", Boom.get_password, raising=False)
    monkeypatch.setattr("keyring.set_password", Boom.set_password, raising=False)
    monkeypatch.setattr("keyring.delete_password", Boom.delete_password, raising=False)


# ── 掩码 ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("short", "•••••"),
        ("1234567890", "••••••7890"),
        ("sk-abcdef123456", "••••••3456"),
    ],
)
def test_masked_only_reveals_last_four(value: str, expected: str) -> None:
    """只露末 4 位 —— 这是对外承诺过的行为（防截图泄露），不能改成露首尾。"""
    assert masked(value) == expected


def test_masked_never_contains_the_secret() -> None:
    secret = "sk-super-secret-value-9999"
    shown = masked(secret)
    assert secret not in shown
    assert shown.endswith("9999")


# ── 内存替身 ──────────────────────────────────────────────
def test_memory_store_round_trip() -> None:
    store = MemoryStore()
    assert store.get("ACOUSTID_API_KEY") is None
    store.set("ACOUSTID_API_KEY", "abc")
    assert store.get("ACOUSTID_API_KEY") == "abc"
    store.set("ACOUSTID_API_KEY", "")  # 空串 = 删除
    assert store.get("ACOUSTID_API_KEY") is None
    store.set("ACOUSTID_API_KEY", "abc")
    store.delete("ACOUSTID_API_KEY")
    assert store.get("ACOUSTID_API_KEY") is None


def test_memory_store_is_marked_secure() -> None:
    assert MemoryStore().is_secure is True


# ── 文件兜底 ──────────────────────────────────────────────
def test_file_store_round_trip(tmp_path) -> None:
    store = FileStore(tmp_path / "secrets.json")
    store.set("ACOUSTID_API_KEY", "abc")
    store.set("DEEPSEEK_API_KEY", "def")
    assert store.get("ACOUSTID_API_KEY") == "abc"
    assert store.get("DEEPSEEK_API_KEY") == "def"
    assert json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8")) == {
        "ACOUSTID_API_KEY": "abc",
        "DEEPSEEK_API_KEY": "def",
    }
    store.delete("DEEPSEEK_API_KEY")
    assert store.get("DEEPSEEK_API_KEY") is None
    assert list(json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))) == [
        "ACOUSTID_API_KEY"
    ]


def test_file_store_is_marked_plaintext(tmp_path) -> None:
    """兜底文件是明文 —— 界面要如实显示，不能假装加密。"""
    store = FileStore(tmp_path / "secrets.json")
    assert store.is_secure is False
    assert str(tmp_path) in store.location()


def test_file_store_survives_broken_json(tmp_path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    store = FileStore(path)
    assert store.get("ACOUSTID_API_KEY") is None  # 不抛异常
    store.set("ACOUSTID_API_KEY", "abc")  # 还能正常写回
    assert store.get("ACOUSTID_API_KEY") == "abc"


# ── 凭据库可用性判定 ──────────────────────────────────────
def test_keyring_available_detects_fail_backend(monkeypatch) -> None:
    """环境里没有可用凭据库时，keyring 会退到 fail 后端 —— 必须能识别出来，
    否则我们会把密钥写进一个"假成功"的地方。"""
    from keyring.backends import fail

    monkeypatch.setattr("keyring.get_keyring", lambda: fail.Keyring())
    assert KeyringStore.available() is False


def test_default_store_falls_back_to_file(monkeypatch, tmp_path) -> None:
    """凭据库不可用 → 退回本地文件（而不是静默失败）。"""
    monkeypatch.setattr(KeyringStore, "available", staticmethod(lambda: False))
    store = default_store(service="音乐数据管家", data_dir=tmp_path)
    assert isinstance(store, FileStore)
    assert store.is_secure is False
    assert store.path.parent == tmp_path


def test_default_store_prefers_keyring(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(KeyringStore, "available", staticmethod(lambda: True))
    store = default_store(service="音乐数据管家", data_dir=tmp_path)
    assert isinstance(store, KeyringStore)
    assert store.is_secure is True


def test_only_two_secret_keys_are_managed() -> None:
    """密钥清单必须只有这两把 —— 邮箱之类不该混进来（它走 preferences 表）。"""
    assert SECRET_KEYS == ("ACOUSTID_API_KEY", "DEEPSEEK_API_KEY")
