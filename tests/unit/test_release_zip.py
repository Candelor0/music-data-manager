"""发布包整理脚本（scripts/make-release-zip.py）的测试。

这个脚本是**发布前的最后一道闸**：扔掉带本机信息的文件、补上 GPL 要求的许可证、
统一文件名编码、并自查"有没有把不该外发的东西发出去"。所以它自己被测试盯着。

重点覆盖两件事：
1. **GBK 文件名**：Windows 的 tar.exe 打出来的中文名是 GBK 字节且不设 UTF-8 标志位。
   读的时候不用 `metadata_encoding="gbk"` 就会得到乱码。这里手写一个这样的 zip 来验。
2. **自查真的会拦人**：禁词、真实用户路径、疑似密钥，一个都不能放过。
"""

from __future__ import annotations

import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("make_release_zip", SCRIPTS / "make-release-zip.py")
assert _spec and _spec.loader
mr = importlib.util.module_from_spec(_spec)
sys.modules["make_release_zip"] = mr  # dataclass 需要能在 sys.modules 里找到本模块
_spec.loader.exec_module(mr)


# ── 手写一个 GBK 文件名的 zip（模仿 Windows tar.exe 的产物） ──
def _deflate_raw(data: bytes) -> bytes:
    """zip 里的 deflate 是**裸流**（没有 zlib 包头），不能用 zlib.compress。"""
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _gbk_zip(path: Path, files: dict[str, bytes]) -> None:
    local_parts: list[bytes] = []
    central_parts: list[bytes] = []
    offset = 0
    for name, data in files.items():
        raw_name = name.encode("gbk")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        comp = _deflate_raw(data)
        local_parts.append(
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,
                0,  # flags：**不设** UTF-8 标志位（0x800）—— 关键
                8,
                0,
                0,
                crc,
                len(comp),
                len(data),
                len(raw_name),
                0,
            )
            + raw_name
            + comp
        )
        central_parts.append(
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                20,
                20,
                0,
                8,
                0,
                0,
                crc,
                len(comp),
                len(data),
                len(raw_name),
                0,
                0,
                0,
                0,
                0,
                offset,
            )
            + raw_name
        )
        offset += len(local_parts[-1])
    central = b"".join(central_parts)
    body = b"".join(local_parts)
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        len(files),
        len(files),
        len(central),
        len(body),
        0,
    )
    path.write_bytes(body + central + end)


# ── 1. 该扔什么 ──────────────────────────────────────────
@pytest.mark.parametrize(
    "name",
    [
        "pkg/diagnostics.txt",
        "pkg/自检输出.txt",
        "pkg/构建日志.txt",
        "pkg/run.log",
        "pkg/data/mds.db",
        "pkg/音乐.flac",
        "pkg/cover.jpg",
    ],
)
def test_should_drop_removes_leaky_files(name: str) -> None:
    assert mr.should_drop(name) is not None, f"{name} 不该被发布出去"


@pytest.mark.parametrize(
    "name",
    [
        "pkg/音乐数据管家.exe",
        "pkg/tools/fpcalc.exe",
        "pkg/.env.example",
        "pkg/README.md",
        "pkg/_internal/assets/icon.png",
        "pkg/_internal/PyQt5/Qt5/plugins/styles/qwindowsvistastyle.dll",
    ],
)
def test_should_drop_keeps_what_belongs(name: str) -> None:
    assert mr.should_drop(name) is None


def test_should_drop_never_touches_internal() -> None:
    """`_internal/` 是程序自己的身体 —— 曾经把里面的图标当“用户图片”扔出去过。"""
    for rel in ("icon.png", "cover.jpg", "some.wav"):
        assert mr.should_drop(f"pkg/_internal/assets/{rel}") is None


# ── 2. GBK 文件名要读对 ──────────────────────────────────
def test_reads_gbk_filenames_without_garbling(tmp_path: Path) -> None:
    """Windows 打出来的包，中文名是 GBK 字节 —— 读的时候必须解对，否则发布出去是乱码。"""
    src = tmp_path / "dist.zip"
    _gbk_zip(
        src,
        {
            "音乐数据管家-0.5.1/使用说明.txt": "你好".encode(),
            "音乐数据管家-0.5.1/诊断工具.bat": b"@echo off\r\n",
        },
    )
    root, entries = mr.read_zip(src)
    assert root == "音乐数据管家-0.5.1"
    assert {e.name.split("/", 1)[1] for e in entries} == {"使用说明.txt", "诊断工具.bat"}


def test_write_zip_marks_names_as_utf8(tmp_path: Path) -> None:
    """写出去的名字必须带 UTF-8 标志位，这样 Windows 与 macOS/Linux 都不会乱码。"""
    out = tmp_path / "out.zip"
    mr.write_zip([mr.Entry("包/文件.txt", "内容".encode())], out)
    with zipfile.ZipFile(out) as zf:
        info = zf.infolist()[0]
        assert info.filename == "包/文件.txt"
        assert info.flag_bits & 0x800, "没设 UTF-8 标志位"


# ── 3. 该补什么 ──────────────────────────────────────────
def test_release_entries_adds_license_usage_and_drops_diagnostics(tmp_path: Path) -> None:
    raw = [
        mr.Entry("pkg/音乐数据管家.exe", b"MZ"),
        mr.Entry("pkg/.env.example", b"#"),
        mr.Entry("pkg/diagnostics.txt", b"C:\\Users\\someone\\music"),
    ]
    entries, dropped, added = mr.release_entries(raw, "pkg")
    names = {e.name for e in entries}
    assert "pkg/音乐数据管家.exe" in names
    assert "pkg/LICENSE" in names
    assert "pkg/使用说明.txt" in names
    assert "pkg/许可证/GPL-2.0.txt" in names
    assert "pkg/许可证/许可证说明.md" in names, "licenses/README.md 应改名为许可证说明.md"
    assert all("diagnostics" not in n for n in names)
    assert any("diagnostics" in n for n, _ in dropped)
    assert any(n.endswith("LICENSE") for n, _ in added)


def test_release_entries_refreshes_readme_and_env_template(tmp_path: Path) -> None:
    """包里的 README / .env.example 必须换成仓库里的最新版（构建产物里那份容易过期）。"""
    raw = [
        mr.Entry("pkg/音乐数据管家.exe", b"MZ"),
        mr.Entry("pkg/README.md", "很久以前的说明".encode()),
        mr.Entry("pkg/.env.example", "# 旧模板\n".encode()),
    ]
    entries, _, added = mr.release_entries(raw, "pkg")
    by_name = {e.name: e.data for e in entries}
    assert by_name["pkg/README.md"] == mr.PROJECT_ROOT.joinpath("README.md").read_bytes()
    assert by_name["pkg/.env.example"] == mr.PROJECT_ROOT.joinpath(".env.example").read_bytes()
    assert {n for n, _ in added} >= {"README.md", ".env.example"}


# ── 4. 自查必须真的拦人 ──────────────────────────────────
def test_scan_catches_build_machine_username() -> None:
    entries = [mr.Entry("pkg/README.md", "构建于 alice 的电脑".encode())]
    problems = mr.scan(entries, "pkg", forbid=("alice",))
    assert problems and "alice" in problems[0]


def test_scan_catches_build_machine_username_in_binary_path() -> None:
    """二进制（exe/dll）里如果嵌了带用户名的路径，也要抓出来。"""
    blob = b"\x00\x01" * 50 + b"C:\\Users\\alice\\Music\x00\x02"
    problems = mr.scan([mr.Entry("pkg/app.exe", blob)], "pkg", forbid=("alice",))
    assert problems and "alice" in problems[0]


def test_scan_catches_app_data_path_with_real_user() -> None:
    text = "数据目录：C:\\Users\\somebody\\AppData\\Local\\MusicDataManager\\音乐数据管家"
    problems = mr.scan([mr.Entry("pkg/diagnostics.txt", text.encode())], "pkg", forbid=())
    assert problems and "somebody" in problems[0]


def test_scan_catches_api_keys_in_text() -> None:
    entries = [
        mr.Entry("pkg/.env.example", b"ACOUSTID_KEY=abc\n"),
        mr.Entry("pkg/leak.txt", b"api_key = \"sk-abcdefghijklmnopqrstuvwxyz0123\"\n"),
    ]
    problems = mr.scan(entries, "pkg", forbid=())
    assert any("疑似密钥" in p for p in problems)


def test_scan_ignores_binary_noise() -> None:
    """二进制里随便出现同样几个字母不算问题。

    为什么要在意：用户名可能是 `lll` 这种短字符串，DLL 里碰巧出现就会天天空报。
    所以二进制里只在**紧贴路径分隔符**时才算数。
    """
    blob = b"\x00\x01" * 100 + b"alice"
    problems = mr.scan([mr.Entry("pkg/_internal/x.dll", blob)], "pkg", forbid=("alice",))
    assert problems == []


def test_check_required_flags_missing_license_and_stray_files() -> None:
    entries = [mr.Entry("pkg/音乐数据管家.exe", b"MZ"), mr.Entry("pkg/notes.txt", b"hi")]
    problems = mr.check_required(entries, "pkg")
    assert any("LICENSE" in p for p in problems)
    assert any("notes.txt" in p for p in problems)


# ── 5. 端到端（走真 main()） ─────────────────────────────
def _fake_dist(tmp_path: Path, *, leak: str = "", leak_file: str = "音乐数据管家.exe") -> Path:
    dist = tmp_path / "dist" / "音乐数据管家-0.5.1"
    (dist / "tools").mkdir(parents=True)
    (dist / "音乐数据管家.exe").write_bytes(
        b"MZ fake " + (leak.encode() if leak_file == "音乐数据管家.exe" else b"")
    )
    (dist / "tools" / "fpcalc.exe").write_bytes(b"MZ fake")
    (dist / ".env.example").write_bytes(b"#\n")
    (dist / "README.md").write_text("说明", encoding="utf-8")
    (dist / "诊断工具.bat").write_bytes(b"@echo off\r\n")
    (dist / "diagnostics.txt").write_text(
        leak if leak_file == "diagnostics.txt" else "", encoding="utf-8"
    )
    return dist


def test_main_produces_release_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = _fake_dist(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(
        sys, "argv", ["make-release-zip.py", str(dist), "--out", str(out), "--forbid", "测试禁词"]
    )
    assert mr.main() == 0

    produced = list(out.glob("*.zip"))
    assert len(produced) == 1
    assert produced[0].name == "mds-0.5.1-windows-portable.zip"
    assert produced[0].name.isascii(), "Release 附件名必须纯 ASCII（GitHub 会抹掉中文）"
    with zipfile.ZipFile(produced[0]) as zf:
        names = zf.namelist()
        assert "音乐数据管家-0.5.1/音乐数据管家.exe" in names
        assert "音乐数据管家-0.5.1/LICENSE" in names
        assert "音乐数据管家-0.5.1/使用说明.txt" in names
        assert "音乐数据管家-0.5.1/许可证/LGPL-2.1.txt" in names
        assert not any(n.endswith("diagnostics.txt") for n in names)
        assert zf.read("音乐数据管家-0.5.1/README.md") == mr.PROJECT_ROOT.joinpath(
            "README.md"
        ).read_bytes(), "包里的 README 应是仓库里的最新版"

    sidecar = out / "mds-0.5.1-windows-portable.zip.sha256"
    assert sidecar.is_file(), "应同时写出 .sha256 校验值（Release 上一起给）"
    assert mr.sha256(produced[0]) in sidecar.read_text(encoding="utf-8")


def test_main_refuses_when_leak_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把构建机路径嵌进**会留下来的文件**里时必须拒绝发布（退出码 2），
    而不是给个警告就放行。这里用主程序：二进制里嵌路径是很常见的情况。"""
    dist = _fake_dist(tmp_path, leak="C:\\Users\\alice\\Desktop\\mds")
    monkeypatch.setattr(
        sys,
        "argv",
        ["make-release-zip.py", str(dist), "--out", str(tmp_path / "out"), "--forbid", "alice"],
    )
    assert mr.main() == 2


def test_main_drops_leaky_diagnostics_and_still_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """泄露只出现在要被扔掉的文件里时，扔掉即可，不必拦发布。"""
    dist = _fake_dist(
        tmp_path, leak="C:\\Users\\alice\\AppData\\Local\\MusicDataManager", leak_file="diagnostics.txt"
    )
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["make-release-zip.py", str(dist), "--out", str(out)])
    assert mr.main() == 0


def test_main_reports_missing_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["make-release-zip.py", str(tmp_path / "nope.zip")])
    assert mr.main() == 1
