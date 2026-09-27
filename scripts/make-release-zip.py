#!/usr/bin/env python3
"""把 Windows 构建出来的 dist zip 收拾成「可以上传 GitHub Release」的版本。

为什么需要它
------------
构建脚本产出的 zip 是**给自己用**的，不是给陌生人下载的：

· 里面可能有**构建者那台机器**的痕迹 —— `诊断工具.bat` 一跑就生成
  `diagnostics.txt`，里面有 `C:\\Users\\<你的用户名>\\...` 和构建目录路径。
  0.5.1 第一次构建时它就跟着 zip 一起发出去了。
· **缺 GPL 要求随二进制分发的许可证文本**（LICENSE 与第三方全文）。
· 中文文件名是 **GBK 字节且没有 UTF-8 标志位**（Windows 自带 tar.exe 打出来的），
  在 macOS / Linux 上解开是乱码。

所以每次发布前都要过一遍这个脚本。它做四件事：

1. 扔掉不该外发的东西（诊断输出 / 日志 / 运行期数据）
2. 补上该有的东西（`LICENSE`、`许可证/`、`使用说明.txt`）
3. 把文件名统一成 UTF-8（现代 Windows 与 macOS/Linux 都能正确显示）
4. 自查：本机路径、密钥、必需文件 —— 任何一项不过就**退出码 2**，不给"差不多就行"的机会

用法
----
    uv run python scripts/make-release-zip.py ~/Downloads/音乐数据管家-0.5.1.zip
    uv run python scripts/make-release-zip.py "D:\\mds-build\\dist\\音乐数据管家-0.5.1"
    uv run python scripts/make-release-zip.py <输入> --forbid 某个名字 --forbid "D:\\某些目录"
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LICENSES_DIR = PROJECT_ROOT / "licenses"
USAGE_SOURCE = PROJECT_ROOT / "packaging" / "使用说明.txt"

#: 一律扔掉（构建/运行期产物，含本机信息）
DROP_NAMES = frozenset(
    {
        "diagnostics.txt",
        "诊断信息.txt",
        "自检输出.txt",
        "构建日志.txt",
        "构建日志.log",
    }
)
DROP_SUFFIXES = (".log", ".db", ".db-wal", ".db-shm")

#: 允许出现在包里的根级文件（白名单：多出来的一律要人看一眼）
EXPECTED_ROOT_FILES = frozenset(
    {
        ".env.example",
        "README.md",
        "LICENSE",
        "使用说明.txt",
        "许可证说明.txt",
        "音乐数据管家.exe",
        "诊断工具.bat",
    }
)

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),  # DeepSeek / OpenAI 风格
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?key|secret)\s*[=:]\s*[\"']?[A-Za-z0-9/+_-]{20,}"),
)
#: 真实用户的 Windows 配置目录里出现本程序的数据目录 = 构建者跑过程序
_APP_DATA_PATH = re.compile(r"[A-Za-z]:\\Users\\([^\\\r\n\"]{1,40})\\AppData\\Local\\MusicDataManager")
_HOME_PATH = re.compile(r"/(?:Users|home)/([^/\s\"']{2,40})/")

_AUDIO_SUFFIXES = (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".ape", ".wma")
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

#: 发布附件的文件名模板。**必须纯 ASCII**：GitHub Release 会把附件名里的
#: 非 ASCII 字符直接抹掉（实测 `音乐数据管家-0.5.1-发布版.zip` 变成了 `-0.5.1-.zip`），
#: 而且 ASCII 名的下载链接在浏览器、脚本、镜像站里都不会变形。
#: 包**里面**的目录名仍然是中文（那不影响任何东西）。
ASSET_NAME_TEMPLATE = "mds-{version}-windows-portable.zip"

TEXT_SCAN_LIMIT = 4 * 1024 * 1024


@dataclass
class Entry:
    """一个待写入的文件条目。"""

    name: str  # 相对包根的路径，用 / 分隔
    data: bytes
    date_time: tuple[int, int, int, int, int, int] = (2020, 1, 1, 0, 0, 0)
    external_attr: int = 0


# ── 读 ────────────────────────────────────────────────────
def read_zip(path: Path) -> tuple[str, list[Entry]]:
    """读 dist zip，返回（包根目录名，条目列表）。

    `metadata_encoding="gbk"` 是关键：Windows 的 tar.exe 用 GBK 字节写中文名且不设
    UTF-8 标志位，不这样读就会得到一堆乱码（`╥⌠└╓…`），发布出去别人解开也是乱码。
    """
    entries: list[Entry] = []
    with zipfile.ZipFile(path, metadata_encoding="gbk") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entries.append(Entry(info.filename, zf.read(info), info.date_time, info.external_attr))
    root = _common_root([e.name for e in entries])
    return root, entries


def read_dir(path: Path) -> tuple[str, list[Entry]]:
    """读一个已解压的 dist 目录（构建脚本的产物目录）。

    条目名一律**带上根目录名前缀**，与 `read_zip` 保持同一种形状 ——
    两边不一致过一次：补进去的许可证带了前缀，原有文件没带，包里就一塌糊涂。
    """
    root = path.name
    entries = [
        Entry(f"{root}/{p.relative_to(path).as_posix()}", p.read_bytes(), external_attr=0x20)
        for p in sorted(path.rglob("*"))
        if p.is_file()
    ]
    return root, entries


def _common_root(names: list[str]) -> str:
    roots = {PurePosixPath(n).parts[0] for n in names if "/" in n}
    if len(roots) == 1:
        return roots.pop()
    return ""  # 包内没有统一顶层目录（不常见）——那就原样处理


# ── 收拾 ──────────────────────────────────────────────────
def should_drop(name: str) -> str | None:
    """该不该扔掉？返回理由（None = 留着）。

    ⚠️ `_internal/` 里的东西只按「明确的用户数据标志」剔（日志/数据库/诊断输出）：
    那里是**程序自己的身体**（含 `_internal/assets/icon.png` 这种必需资源）。
    曾经把图标当成「图片=用户素材」扔出去过，包里的图标就没了。
    """
    rel = _rel(name)
    base = PurePosixPath(rel).name
    if base in DROP_NAMES:
        return "含本机信息的诊断/日志文件"
    if base.lower().endswith(DROP_SUFFIXES):
        return "日志或运行期数据库"
    if rel.startswith("_internal/") or "/_internal/" in f"/{rel}":
        return None  # 程序自己的身体，不碰
    if rel.startswith("data/") or "/data/" in f"/{rel}":
        return "运行期数据目录"
    if base.lower().endswith(_AUDIO_SUFFIXES):
        return "音频文件（用户的音乐不该进发布包）"
    if base.lower().endswith(_IMAGE_SUFFIXES):
        return "图片文件（用户素材不该进发布包）"
    return None


def _rel(name: str, root: str = "") -> str:
    """去掉包根目录前缀（如果传了 root）。"""
    if root and name.startswith(root + "/"):
        return name[len(root) + 1 :]
    return name


def _with_root(root: str, rel: str) -> str:
    return f"{root}/{rel}" if root else rel


def release_entries(
    entries: list[Entry], root: str
) -> tuple[list[Entry], list[tuple[str, str]], list[tuple[str, str]]]:
    """产出「可发布」的条目列表，并分别报告丢掉与新增了什么。"""
    kept: list[Entry] = []
    dropped: list[tuple[str, str]] = []
    for entry in entries:
        reason = should_drop(entry.name)
        if reason:
            dropped.append((entry.name, reason))
            continue
        kept.append(entry)

    present = {_rel(e.name, root) for e in kept}
    added: list[tuple[str, str]] = []

    def put(rel: str, data: bytes, why: str, *, refresh: bool = False) -> None:
        """补文件；`refresh=True` 时连已存在的一起替换。"""
        if rel in present and not refresh:
            return
        kept[:] = [e for e in kept if _rel(e.name, root) != rel]
        kept.append(Entry(_with_root(root, rel), data))
        present.add(rel)
        added.append((rel, why))

    def add(rel: str, data: bytes, why: str) -> None:
        put(rel, data, why)

    # 说明与配置模板一律用仓库里的最新版：构建产物里那份是构建当天拷的，很容易过期
    put(
        "README.md",
        (PROJECT_ROOT / "README.md").read_bytes(),
        "仓库里的最新 README",
        refresh=True,
    )
    put(
        ".env.example",
        (PROJECT_ROOT / ".env.example").read_bytes(),
        "仓库里的最新配置模板",
        refresh=True,
    )

    license_text = PROJECT_ROOT / "LICENSE"
    add("LICENSE", license_text.read_bytes(), "GPL 要求随二进制提供许可证全文")
    if LICENSES_DIR.is_dir():
        for path in sorted(LICENSES_DIR.iterdir()):
            if not path.is_file():
                continue
            # README.md 在这里讲的是许可证，改名免得和程序自己的 README 混淆
            rel = "许可证说明.md" if path.name == "README.md" else path.name
            add(f"许可证/{rel}", path.read_bytes(), "第三方许可证文本")
    if USAGE_SOURCE.is_file():
        add("使用说明.txt", USAGE_SOURCE.read_bytes(), "给用户看的说明")

    kept.sort(key=lambda e: e.name)
    return kept, dropped, added


# ── 自查 ──────────────────────────────────────────────────
def _is_texty(data: bytes) -> bool:
    if b"\x00" in data[:1024]:
        return False
    return len(data) <= TEXT_SCAN_LIMIT


def _contains_path_like(data: bytes, needle: bytes) -> bool:
    """二进制里只在**紧贴路径分隔符**时才算命中。

    否则用户名这种短字符串（比如 `lll`）会在几万行随机字节里天天空报。
    """
    start = 0
    while True:
        index = data.find(needle, start)
        if index < 0:
            return False
        before = data[index - 1 : index]
        after = data[index + len(needle) : index + len(needle) + 1]
        if before in (b"\\", b"/") or after in (b"\\", b"/"):
            return True
        start = index + 1


def scan(entries: list[Entry], root: str, forbid: tuple[str, ...]) -> list[str]:
    """回去找本机信息与密钥。返回问题列表（空 = 干净）。"""
    problems: list[str] = []
    forbid_bytes = [(f, f.encode("utf-8")) for f in forbid if f]

    for entry in entries:
        rel = _rel(entry.name, root)
        texty = _is_texty(entry.data)
        for label, needle in forbid_bytes:
            hit = needle in entry.data if texty else _contains_path_like(entry.data, needle)
            if hit:
                problems.append(f"{rel}：含被禁字符串 {label!r}（本机信息）")
        if not texty:
            continue
        text = entry.data.decode("utf-8", "replace")
        for match in _APP_DATA_PATH.finditer(text):
            problems.append(f"{rel}：含真实用户的数据目录路径（用户 {match.group(1)!r}）")
        for pattern in _SECRET_PATTERNS:
            found = pattern.search(text)
            if found:
                problems.append(f"{rel}：疑似密钥（{found.group(0)[:12]}…）")
    return problems


def check_required(entries: list[Entry], root: str) -> list[str]:
    """必需文件在不在；根级有没有多余的东西。"""
    present = {_rel(e.name, root) for e in entries}
    problems: list[str] = []
    for rel in ("LICENSE", ".env.example", "使用说明.txt", "许可证/GPL-2.0.txt", "许可证/LGPL-2.1.txt"):
        if rel not in present:
            problems.append(f"缺必需文件：{rel}")
    if not any(PurePosixPath(n).suffix.lower() == ".exe" for n in present):
        problems.append("包里没有 .exe 主程序")
    extra = {
        n
        for n in present
        if "/" not in n and n not in EXPECTED_ROOT_FILES and not n.startswith("许可证")
    }
    for name in sorted(extra):
        problems.append(f"根目录多出未预期文件：{name}（发布前确认一下）")
    return problems


# ── 写 ────────────────────────────────────────────────────
def write_zip(entries: list[Entry], out_path: Path) -> None:
    """写发布包。文件名一律 UTF-8（带标志位），中文在两个系统上都正确。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name, date_time=entry.date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = entry.external_attr or 0x20
            zf.writestr(info, entry.data)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="把构建产物收拾成可上传 Release 的 zip")
    parser.add_argument("source", help="构建脚本产出的 zip，或已解压的 dist 目录")
    parser.add_argument("--out", default=str(Path.home() / "Downloads"), help="输出目录")
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        help="不许出现的字符串（可重复）。默认自动加上当前登录用户名",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    if not source.exists():
        print(f"❌ 找不到输入：{source}")
        return 1

    forbid = tuple(dict.fromkeys([*args.forbid, getpass.getuser()]))

    root, raw = read_dir(source) if source.is_dir() else read_zip(source)
    print(f"读入：{source.name}（{len(raw)} 个文件，顶层目录 {root!r}）")

    entries, dropped, added = release_entries(raw, root)

    print(f"\n丢掉 {len(dropped)} 个：")
    for name, why in dropped:
        print(f"  - {_rel(name, root)}（{why}）")
    print(f"补上 {len(added)} 个：")
    for rel, why in added:
        print(f"  + {rel}（{why}）")

    problems = scan(entries, root, forbid) + check_required(entries, root)
    if problems:
        print("\n❌ 自查没过，**不要发布**：")
        for problem in problems:
            print(f"  · {problem}")
        return 2

    version = root.rsplit("-", 1)[-1] if "-" in root else "0.0.0"
    name = ASSET_NAME_TEMPLATE.format(version=version)
    out_path = Path(args.out).expanduser() / name
    write_zip(entries, out_path)

    size_mb = out_path.stat().st_size / 1024 / 1024
    digest = sha256(out_path)
    # 旁边留一份校验值：别人下载后可以自己核一下有没有下坏（Release 上一起给）
    (out_path.parent / (out_path.name + ".sha256")).write_text(
        f"{digest}  {out_path.name}\n", encoding="utf-8"
    )
    print(f"\n✅ 自查通过（禁词 {len(forbid)} 个、密钥扫描 {len(entries)} 个文件）")
    print(f"发布包：{out_path}（{len(entries)} 个文件，{size_mb:.1f} MB）")
    print(f"sha256：{digest}")
    print(f"校验值也写到了：{out_path.name}.sha256")
    print("\n上传 Release 时把这个 zip 拖进去即可（附件名是 ASCII，避免被 GitHub 抹掉中文）；")
    print("源码由 git tag 提供，满足 GPL 的「对应源码」义务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
