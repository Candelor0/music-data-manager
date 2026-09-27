"""用声学指纹向 AcoustID 查询，评估"能不能认出这是哪首歌"。

约束：
- **AcoustID 限流每秒最多 2 次**（比官方 3 次/秒更保守）。
  限流写死在 RateLimiter 里，所有请求都必须经过它。
- 指纹计算结果本地缓存，重复运行不重算。
- 不打印任何密钥，不打印含密钥的 URL。

用法：
    python 05_acoustid查询.py [--limit 5] [--rate 2] [--rebuild-cache]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

POC_DIR = Path(__file__).resolve().parent
PROJECT_DIR = POC_DIR.parent
CACHE_PATH = POC_DIR / ".cache" / "fingerprints.json"
API_URL = "https://api.acoustid.org/v2/lookup"
EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aiff", ".aif", ".wav", ".wma", ".ape", ".wv"}


# ────────────────────────── 配置与限流 ──────────────────────────

def load_env() -> dict[str, str]:
    """读取项目根目录的 .env。只读，不回显。"""
    env: dict[str, str] = {}
    path = PROJECT_DIR / ".env"
    if not path.exists():
        print("❌ 找不到 .env")
        sys.exit(1)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


class RateLimiter:
    """最小间隔限流器。rate_per_sec=2 表示任意两次请求之间至少间隔 0.5 秒。"""

    def __init__(self, rate_per_sec: float) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec 必须大于 0")
        self.rate = rate_per_sec
        self.min_interval = 1.0 / rate_per_sec
        self._last = 0.0
        self._lock = threading.Lock()
        self.total_wait = 0.0

    def wait(self) -> float:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                sleep_for = self.min_interval - delta
                time.sleep(sleep_for)
                self.total_wait += sleep_for
            self._last = time.monotonic()
            return self._last


# ────────────────────────── 指纹计算 ──────────────────────────

def find_fpcalc(explicit: str = "") -> str:
    candidates = [
        explicit,
        os.environ.get("FPCALC", ""),
        str(PROJECT_DIR / "tools" / "fpcalc"),
        str(PROJECT_DIR / "tools" / "fpcalc.exe"),
        "/opt/homebrew/bin/fpcalc",
        "/usr/local/bin/fpcalc",
    ]
    for c in candidates:
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return c
    from shutil import which

    found = which("fpcalc")
    if found:
        return found
    print("❌ 找不到 fpcalc。请把可执行文件放到 tools/fpcalc")
    sys.exit(1)


def compute_fingerprint(fpcalc: str, path: str, timeout: int = 120) -> dict:
    """返回 {'duration': int, 'fingerprint': str} 或 {'error': str}。"""
    try:
        proc = subprocess.run(
            [fpcalc, "-json", "-length", "120", path],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "fingerprint timeout"}
    except OSError as exc:
        return {"error": f"fpcalc 启动失败: {exc}"}
    if proc.returncode != 0:
        return {"error": f"fpcalc 退出码 {proc.returncode}"}
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return {"error": f"fpcalc 输出无法解析: {exc}"}
    if not data.get("fingerprint"):
        return {"error": "fpcalc 未产出指纹"}
    return {"duration": int(data.get("duration", 0)), "fingerprint": data["fingerprint"]}


# ────────────────────────── AcoustID 查询 ──────────────────────────

def acoustid_lookup(
    api_key: str,
    fingerprint: str,
    duration: int,
    limiter: RateLimiter,
    timeout: int = 20,
    max_retries: int = 3,
) -> dict:
    """向 AcoustID 查询。所有请求都经过 limiter，绝不绕过。"""
    payload = urllib.parse.urlencode(
        {
            "client": api_key,
            "duration": str(duration),
            "fingerprint": fingerprint,
            "meta": "recordings releasegroups compress",
            "format": "json",
        }
    ).encode()

    for attempt in range(1, max_retries + 1):
        limiter.wait()
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "MusicDataManager/0.1.0 (poc)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                backoff = 2.0 * attempt
                print(f"    限流(429)，等待 {backoff:.0f}s 后重试 {attempt}/{max_retries}")
                time.sleep(backoff)
                continue
            # 读出服务端的错误说明（不含密钥，不会泄露）
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
                err = body.get("error", {})
                msg = f"{err.get('code')}: {err.get('message')}"
            except Exception:  # noqa: BLE001
                msg = f"HTTP {exc.code}"
            return {"status": "error", "error": {"code": exc.code, "message": msg[:160]}}
        except urllib.error.URLError as exc:
            if attempt >= max_retries:
                return {"status": "error", "error": {"code": "network", "message": str(exc.reason)[:120]}}
            time.sleep(1.5 * attempt)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": {"code": "unknown", "message": f"{type(exc).__name__}: {exc}"[:120]}}
    return {"status": "error", "error": {"code": "rate_limited", "message": "重试后仍被限流"}}


def pick_best(response: dict) -> dict:
    """从响应里取最佳候选。AcoustID 的 results 已按 score 降序。"""
    results = response.get("results") or []
    if not results:
        return {}
    best = results[0]
    recs = best.get("recordings") or []
    rec = recs[0] if recs else {}
    artists = rec.get("artists") or []
    rgs = rec.get("releasegroups") or []
    return {
        "score": round(float(best.get("score", 0)), 3),
        "n_results": len(results),
        "recording_id": rec.get("id", ""),
        "title": rec.get("title", ""),
        "artists": [a.get("name", "") for a in artists],
        "releasegroup": rgs[0].get("title", "") if rgs else "",
        "releasegroup_type": rgs[0].get("type", "") if rgs else "",
    }


# ────────────────────────── 文本归一化与比对 ──────────────────────────

_PUNCT = re.compile(r"[\s\-_/\\.,:;!?'\"“”‘’(){}[\]<>~～・、。，：；！？【】《》|&+＊*]+")


def normalize(text: str) -> str:
    """比对用归一化：NFKC 全角转半角 + 小写 + 只保留字母/数字/CJK。

    为什么这么激进地剔标点：
    - 同一首歌在不同数据源里会用不同的连字符（ASCII `-`、U+2010 `‐`）、
      波浪号（U+301C `〜`、U+FF5E `～`）、全角星号 `＊` 与半角 `*`……
    - 这些差异对"是不是同一首歌"毫无意义，却会让字符串直接比较失败。
    """
    t = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in t if ch.isalnum())


def compare(top: dict, tag_title: str, tag_artist: str) -> str:
    """把 AcoustID 的首选与文件已有标签对比，作为"准不准"的自动代理指标。

    注意：这不等于"产品准确率"——它只能说明"能不能认出这首"。
    难以区分的是"多候选里选对发行版"，那需要 P4。
    """
    if not top:
        return "无结果"
    a_title, a_artist = normalize(top.get("title", "")), normalize(" ".join(top.get("artists", [])))
    t_title, t_artist = normalize(tag_title), normalize(tag_artist)
    if not t_title or not t_artist:
        return "无法比对"

    title_eq = a_title == t_title
    title_contained = bool(a_title) and (a_title in t_title or t_title in a_title)
    artist_eq = a_artist == t_artist
    artist_contained = bool(a_artist) and (a_artist in t_artist or t_artist in a_artist)

    # 文件标题明显含杂质（如尾部拼了"アニメ…主题曲"），而 AI 给的是干净标题
    if not title_eq and a_title and a_title in t_title and len(a_title) < len(t_title) * 0.75:
        return "AI 更干净"
    if title_eq and artist_eq:
        return "一致"
    if (title_eq or title_contained) and (artist_eq or artist_contained):
        return "部分一致"
    return "不一致"


# ────────────────────────── 主流程 ──────────────────────────

@dataclass
class Entry:
    path: str
    name: str
    tag_title: str = ""
    tag_artist: str = ""
    tag_album: str = ""
    duration: int = 0
    fingerprint_ok: bool = False
    score: float = 0.0
    n_results: int = 0
    ai_title: str = ""
    ai_artists: list[str] = field(default_factory=list)
    ai_releasegroup: str = ""
    verdict: str = ""
    error: str = ""
    elapsed: float = 0.0


def read_tags(path: str) -> dict[str, str]:
    try:
        import mutagen
    except ImportError:
        return {}
    try:
        mf = mutagen.File(path, easy=True)
        if mf is None:
            return {}
        out = {}
        for key in ("title", "artist", "album"):
            try:
                v = mf.get(key)
            except Exception:  # noqa: BLE001
                v = None
            if v:
                out[key] = str(v[0]).strip() if isinstance(v, (list, tuple)) else str(v).strip()
        return out
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="P2：AcoustID 指纹查询验证")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 首（0 = 全部）")
    ap.add_argument("--rate", type=float, default=2.0, help="每秒最多请求次数（默认 2）")
    ap.add_argument("--out", default="/tmp/p2_acoustid.json")
    ap.add_argument("--rebuild-cache", action="store_true", help="忽略指纹缓存，全部重算")
    args = ap.parse_args()

    env = load_env()
    api_key = env.get("ACOUSTID_API_KEY", "")
    if not api_key:
        print("❌ .env 里没有 ACOUSTID_API_KEY")
        return 1
    music_dir = env.get("MUSIC_LIBRARY_PATH", "")
    if not music_dir or not Path(music_dir).is_dir():
        print(f"❌ 音乐库目录无效：{music_dir!r}")
        return 1

    fpcalc = find_fpcalc()
    limiter = RateLimiter(args.rate)

    files = sorted(
        str(p) for p in Path(music_dir).rglob("*") if p.is_file() and p.suffix.lower() in EXTS
    )
    if args.limit:
        files = files[: args.limit]

    print("=" * 84)
    print(f"AcoustID 声学指纹查询   共 {len(files)} 首")
    print(f"限流：每秒最多 {args.rate:g} 次（任意两次请求间隔 ≥ {1/args.rate:.2f} 秒）")
    print(f"指纹工具：{fpcalc}")
    print("=" * 84)

    # 指纹缓存
    cache: dict[str, dict] = {}
    if CACHE_PATH.exists() and not args.rebuild_cache:
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            print(f"已载入指纹缓存：{len(cache)} 条")
        except json.JSONDecodeError:
            cache = {}

    entries: list[Entry] = []
    t_start = time.monotonic()
    for i, path in enumerate(files, 1):
        name = Path(path).name
        tags = read_tags(path)
        e = Entry(
            path=path,
            name=name,
            tag_title=tags.get("title", ""),
            tag_artist=tags.get("artist", ""),
            tag_album=tags.get("album", ""),
        )
        t0 = time.monotonic()

        fp = cache.get(path)
        if not fp or args.rebuild_cache:
            fp = compute_fingerprint(fpcalc, path)
            cache[path] = fp
        if "error" in fp:
            e.error = fp["error"]
        else:
            e.fingerprint_ok = True
            e.duration = fp["duration"]
            resp = acoustid_lookup(api_key, fp["fingerprint"], fp["duration"], limiter)
            if resp.get("status") != "ok":
                err = resp.get("error", {})
                e.error = f"{err.get('code')}: {err.get('message')}"[:100]
            else:
                top = pick_best(resp)
                e.score = top.get("score", 0.0)
                e.n_results = top.get("n_results", 0)
                e.ai_title = top.get("title", "")
                e.ai_artists = top.get("artists", [])
                e.ai_releasegroup = top.get("releasegroup", "")
                e.verdict = compare(top, e.tag_title, e.tag_artist)

        e.elapsed = time.monotonic() - t0
        entries.append(e)

        flag = "✅" if e.verdict in ("一致",) else ("🧹" if e.verdict == "AI 更干净" else ("⚠️" if e.verdict == "部分一致" else "❌"))
        if e.error:
            flag = "💥"
        print(f"  {i:>3}/{len(files)} {flag} score={e.score:<5} {e.verdict:<6} {name[:52]}")
        if e.verdict in ("不一致", "部分一致", "AI 更干净") and not e.error:
            print(f"        文件标签: {e.tag_artist} / {e.tag_title}")
            print(f"        AI 结果  : {' '.join(e.ai_artists)} / {e.ai_title}  [{e.ai_releasegroup}]")

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    elapsed_total = time.monotonic() - t_start
    ok = [e for e in entries if not e.error]
    errs = [e for e in entries if e.error]
    same = [e for e in entries if e.verdict == "一致"]
    part = [e for e in entries if e.verdict == "部分一致"]
    diff = [e for e in entries if e.verdict == "不一致"]
    none_ = [e for e in entries if e.verdict == "无结果"]
    cleaner = [e for e in entries if e.verdict == "AI 更干净"]

    print()
    print("=" * 84)
    print("结果汇总")
    print("=" * 84)
    print(f"  总数                {len(entries)}")
    print(f"  请求失败            {len(errs)}")
    print(f"  有结果（score>0）    {len([e for e in ok if e.n_results])}")
    print(f"  ── 与文件已有标签比对（自动代理指标）──")
    print(f"  一致                {len(same)}  ({len(same)/max(len(entries),1)*100:.0f}%)")
    print(f"  AI 更干净            {len(cleaner)}   （文件标题含杂质，AI 给的是干净标题）")
    print(f"  部分一致            {len(part)}")
    print(f"  不一致              {len(diff)}")
    print(f"  无结果              {len(none_)}")
    print(f"  → 可视为正确合计      {len(same)+len(cleaner)+len(part)} / {len(entries)}")
    scores = sorted(e.score for e in ok if e.score)
    if scores:
        print(f"  score 分布：最低 {scores[0]:.2f}｜中位 {scores[len(scores)//2]:.2f}｜最高 {scores[-1]:.2f}")
        print(f"  score ≥ 0.9 的      {len([s for s in scores if s >= 0.9])}")
    print(f"  耗时：总 {elapsed_total:.1f}s｜平均每首 {elapsed_total/max(len(entries),1):.2f}s")
    print(f"  限流等待合计：{limiter.total_wait:.1f}s")

    Path(args.out).write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "rate_per_sec": args.rate,
                "music_dir": music_dir,
                "total": len(entries),
                "summary": {
                    "errors": len(errs),
                    "same": len(same),
                    "ai_cleaner": len(cleaner),
                    "partial": len(part),
                    "different": len(diff),
                    "no_result": len(none_),
                },
                "entries": [asdict(e) for e in entries],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n详细结果：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
