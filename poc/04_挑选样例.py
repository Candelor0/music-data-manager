"""从本机音乐库自动挑选"边界样例"，供离线验证与样例集定稿使用。

设计原则：
- **只读**：只读取元数据与文件路径；不修改、不移动、不复制、不上传任何文件。
- **不打扰**：输出一份"建议清单 + 每条的挑选理由"，由人过目确认，不自动改动用户文件。
- **只用 mutagen**：不依赖 Picard，保证挑选环节本身简单可靠。

用法：
    python 04_挑选样例.py "/路径/到/音乐库" [--scan-limit 20000] [--out 样例建议.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

try:
    import mutagen
except ImportError:
    print("缺少依赖 mutagen。请先执行：uv pip install mutagen")
    sys.exit(1)

EXTS = {
    ".mp3", ".flac", ".m4a", ".m4b", ".mp4", ".ogg", ".oga", ".opus",
    ".aiff", ".aif", ".wav", ".wma", ".ape", ".wv",
}

# 原始标签键别名（各容器命名不同），覆盖目标标签格式
RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "TIT2", "\xa9nam", "INAM", "Title"),
    "artist": ("artist", "TPE1", "\xa9ART", "IART", "Artist"),
    "album": ("album", "TALB", "\xa9alb", "IPRD", "Album"),
    "albumartist": ("albumartist", "TPE2", "aART", "WM/AlbumArtist"),
    "date": ("date", "year", "TDRC", "TYER", "\xa9day", "ICRD", "Date"),
    "genre": ("genre", "TCON", "\xa9gen", "IGNR", "Genre"),
    "composer": ("composer", "TCOM", "\xa9wrt", "IMUS", "Composer"),
}

MOJIBAKE_MARKERS = ("\ufffd", "Ã", "Â", "ï¿½", "å¤", "ã", "Ð", "Ñ")
QUESTION_RUN = re.compile(r"\?{3,}")

CLASSICAL_HINTS = ("classical", "古典", "baroque", "opera", "symphony", "sonata", "concerto")
VARIOUS_ARTISTS = ("various artists", "va", "群星", "合集", "合辑", "v.a.")

# 挑选优先级：按"诊断价值"排，而非按出现数量（否则"缺流派"这类大面积问题会挤掉稀有的真边界）
REASON_PRIORITY = (
    "读取失败",
    "疑似乱码/错误编码",
    "缺标题或艺术家",
    "合辑(Various Artists)",
    "古典乐特征",
    "缺专辑",
    "缺年份",
    "超长曲目(≥10 分钟)",
    "超短曲目(<1 分钟)",
    "缺流派",
    "有作曲者字段",
)


def reason_rank(reason: str) -> int:
    return REASON_PRIORITY.index(reason) if reason in REASON_PRIORITY else len(REASON_PRIORITY)


@dataclass
class Track:
    path: str
    ext: str
    format_class: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    date: str = ""
    genre: str = ""
    composer: str = ""
    length: float = 0.0
    error: str = ""
    reasons: list[str] = field(default_factory=list)


def _first(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip()


def _read_fields(path: str) -> tuple[str, float, dict[str, str], str]:
    """返回 (格式类名, 时长, 统一字段字典, 错误信息)。两遍策略：easy 模式 → 原始键别名。"""
    fmt_class = ""
    length = 0.0
    fields: dict[str, str] = {}
    error = ""

    mf = None
    try:
        mf = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001
        mf = None

    if mf is None:
        try:
            mf = mutagen.File(path)
        except Exception as exc:  # noqa: BLE001
            return "", 0.0, {}, f"{type(exc).__name__}: {exc}"[:120]

    if mf is None:
        return "", 0.0, {}, "无法识别格式"

    fmt_class = type(mf).__name__
    try:
        length = float(mf.info.length)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    # easy 模式：键已统一为 title/artist/...
    for key in RAW_ALIASES:
        try:
            v = mf.get(key)
        except Exception:  # noqa: BLE001
            v = None
        s = _first(v)
        if s:
            fields[key] = s

    # 回退到原始键别名（补齐 easy 模式没覆盖到的）
    missing = [k for k in RAW_ALIASES if not fields.get(k)]
    if missing:
        for key in missing:
            for alias in RAW_ALIASES[key]:
                try:
                    v = mf.get(alias)
                except Exception:  # noqa: BLE001
                    v = None
                s = _first(v)
                if s:
                    fields[key] = s
                    break

    return fmt_class, length, fields, error


def read_track(path: str) -> Track:
    ext = os.path.splitext(path)[1].lower()
    tr = Track(path=path, ext=ext)
    tr.format_class, tr.length, fields, tr.error = _read_fields(path)
    tr.title = fields.get("title", "")
    tr.artist = fields.get("artist", "")
    tr.album = fields.get("album", "")
    tr.albumartist = fields.get("albumartist", "")
    tr.date = fields.get("date", "")
    tr.genre = fields.get("genre", "")
    tr.composer = fields.get("composer", "")
    return tr


def looks_garbled(*values: str) -> bool:
    for v in values:
        if not v:
            continue
        if any(m in v for m in MOJIBAKE_MARKERS):
            return True
        if QUESTION_RUN.search(v):
            return True
    return False


def classify(tr: Track) -> None:
    r = tr.reasons
    if tr.error:
        r.append("读取失败")
        return
    if not tr.title or not tr.artist:
        r.append("缺标题或艺术家")
    if not tr.album:
        r.append("缺专辑")
    if not tr.date:
        r.append("缺年份")
    if not tr.genre:
        r.append("缺流派")
    if looks_garbled(tr.title, tr.artist, tr.album):
        r.append("疑似乱码/错误编码")
    # 古典乐：只看流派关键词。
    # 不能把"有作曲者字段"当成古典乐——日系资源普遍填 composer，会大面积误判。
    if any(h in tr.genre.lower() for h in CLASSICAL_HINTS):
        r.append("古典乐特征")
    if tr.composer:
        r.append("有作曲者字段")
    if any(v in tr.albumartist.lower() for v in VARIOUS_ARTISTS):
        r.append("合辑(Various Artists)")
    if tr.length >= 600:
        r.append("超长曲目(≥10 分钟)")
    if tr.length and tr.length < 60:
        r.append("超短曲目(<1 分钟)")


def main() -> int:
    ap = argparse.ArgumentParser(description="从音乐库挑选边界样例（只读，不改动任何文件）")
    ap.add_argument("music_dir", help="音乐库根目录")
    ap.add_argument("--scan-limit", type=int, default=20000, help="最多扫描多少个文件（默认 20000）")
    ap.add_argument("--out", default="样例建议.json", help="输出 JSON 路径")
    ap.add_argument("--edge-per-reason", type=int, default=3, help="每类边界最多挑几条")
    ap.add_argument("--batch-size", type=int, default=100, help="批量验证集条数")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.music_dir))
    if not os.path.isdir(root):
        print(f"目录不存在：{root}")
        return 1

    print(f"扫描目录：{root}")
    print("（只读元数据，不会修改/移动/上传任何文件）\n")

    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in EXTS:
                found.append(os.path.join(dirpath, fn))
                if len(found) >= args.scan_limit:
                    break
        if len(found) >= args.scan_limit:
            print(f"已达扫描上限 {args.scan_limit}，停止扫描")
            break

    total = len(found)
    print(f"发现音频文件：{total} 个")
    if total == 0:
        return 1

    tracks: list[Track] = []
    for i, p in enumerate(found, 1):
        tr = read_track(p)
        classify(tr)
        tracks.append(tr)
        if i % 200 == 0 or i == total:
            print(f"  已读取 {i}/{total}", end="\r")
    print()

    by_reason: dict[str, list[Track]] = defaultdict(list)
    for tr in tracks:
        for reason in tr.reasons:
            by_reason[reason].append(tr)

    by_ext: dict[str, int] = defaultdict(int)
    for tr in tracks:
        by_ext[tr.ext] += 1

    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for tr in tracks:
        if tr.title and tr.artist:
            seen[(tr.artist.lower(), tr.title.lower())].add(tr.album.lower())
    multi = {k: v for k, v in seen.items() if len(v) > 1}

    print("=" * 78)
    print("一、格式分布")
    print("=" * 78)
    for ext, n in sorted(by_ext.items(), key=lambda kv: -kv[1]):
        print(f"  {ext:<8} {n:>6} 个")

    print()
    print("=" * 78)
    print("二、边界情况分布（同一条可能命中多类）")
    print("=" * 78)
    for reason, items in sorted(by_reason.items(), key=lambda kv: reason_rank(kv[0])):
        print(f"  {reason:<22} {len(items):>6} 条")
    print(f"  {'同曲多版本(artist+title)':<20} {len(multi):>6} 组")

    chosen: dict[str, list[Track]] = {}
    used: set[str] = set()
    for reason, items in sorted(by_reason.items(), key=lambda kv: reason_rank(kv[0])):
        picked = [t for t in items if t.path not in used][: args.edge_per_reason]
        for t in picked:
            used.add(t.path)
        if picked:
            chosen[reason] = picked

    # 批量集：优先取"标签齐备"的（标题+艺术家+专辑），再补其他
    def is_usable(t: Track) -> bool:
        return bool(t.title and t.artist and t.album)

    clean = [t for t in tracks if is_usable(t) and t.path not in used]
    batch = clean[: args.batch_size]
    if len(batch) < args.batch_size:
        taken = {b.path for b in batch}
        rest = [t for t in tracks if t.path not in used and t.path not in taken]
        batch += rest[: args.batch_size - len(batch)]

    print()
    print("=" * 78)
    print("三、建议的边界样例（10 首量级）")
    print("=" * 78)
    n = 0
    for reason, items in chosen.items():
        for t in items:
            n += 1
            print(f"  {n:>2}. [{reason}] {os.path.basename(t.path)}")
            print(f"      {t.artist or '—'} / {t.title or '—'} / {t.album or '—'} ({t.date or '—'})")
    print(f"\n  共 {n} 条边界样例")

    print()
    print("=" * 78)
    print(f"四、建议的批量验证集：{len(batch)} 首（其中标签完整 {len(clean[: args.batch_size])} 首）")
    print("=" * 78)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "music_root": root,
        "scanned_files": total,
        "format_distribution": dict(by_ext),
        "boundary_counts": {k: len(v) for k, v in by_reason.items()},
        "multi_version_groups": len(multi),
        "proposed_edge_samples": {k: [asdict(t) for t in v] for k, v in chosen.items()},
        "proposed_batch_samples": [asdict(t) for t in batch],
    }
    out_path = args.out if os.path.isabs(args.out) else os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n已写出清单：{out_path}")
    print("提示：这只是建议，不会改动你的任何文件。确认后再据此挑选用例。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
