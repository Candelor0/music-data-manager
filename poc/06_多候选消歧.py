"""多候选消歧 —— 在 MusicBrainz 的发行版候选里让 DeepSeek 挑，并量出质量/成本/时延。

流程（每首）：
    指纹（复用指纹缓存）
      → AcoustID 拿 recording MBID
      → MusicBrainz 拉该 recording 的**全部发行版候选**
      → 组装证据（文件名/时长/现有标签 ± 候选列表）
      → DeepSeek 结构化输出：选了哪个、为什么、置信度、清洗后的标签
      → 与文件现有标签自动比对（代理指标）+ 记录 token 与耗时

限流（都写死在代码里，绕不过去）：
    - AcoustID：每秒最多 2 次
    - MusicBrainz：每秒最多 1 次（官方要求）
    - DeepSeek：无硬限速，但带成本累加器，超预算即停止

两轮对比：
    --round A   正常（模型看得到文件现有标签）
    --round B   假装文件没有标签（只给文件名 + 时长）→ 模拟"空标签库"

缓存：三份缓存都在 poc/.cache/ 下，重复运行不重复花钱。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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
CACHE_DIR = POC_DIR / ".cache"
FP_CACHE = CACHE_DIR / "fingerprints.json"
MB_CACHE = CACHE_DIR / "musicbrainz.json"
LLM_CACHE = CACHE_DIR / "llm.json"

ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
MB_BASE = "https://musicbrainz.org/ws/2"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# DeepSeek 官方价目（美元 / 百万 tokens）
# 来源：https://api-docs.deepseek.com/quick_start/pricing（deepseek-flash 档）
# 峰时：周一至周五 01:00-04:00 与 06:00-10:00 UTC；其余时段（含周末与中国法定假日）为谷时，价格为峰时的一半。
USD_IN_MISS_PEAK, USD_IN_MISS_OFFPEAK = 0.30, 0.15
USD_IN_HIT_PEAK, USD_IN_HIT_OFFPEAK = 0.006, 0.003
USD_OUT_PEAK, USD_OUT_OFFPEAK = 1.20, 0.60
USD_TO_CNY = 7.1  # 估算用汇率，实际以账单为准

EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aiff", ".aif", ".wav", ".wma", ".ape", ".wv"}


# ───────────────────────── 基础工具 ─────────────────────────

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = PROJECT_DIR / ".env"
    if not path.exists():
        print("❌ 找不到 .env")
        sys.exit(1)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


class RateLimiter:
    """最小间隔限流器：rate_per_sec=2 ⇒ 任意两次请求间隔 ≥ 0.5 秒。"""

    def __init__(self, rate_per_sec: float, name: str = "") -> None:
        self.rate = rate_per_sec
        self.name = name
        self.min_interval = 1.0 / rate_per_sec
        self._last = 0.0
        self.total_wait = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            s = self.min_interval - delta
            time.sleep(s)
            self.total_wait += s
        self._last = time.monotonic()


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in t if ch.isalnum())


# ───────────────────────── P2：AcoustID ─────────────────────────

def acoustid_recording(api_key: str, fp: str, duration: int, limiter: RateLimiter) -> dict:
    """返回最佳 recording 的 mbid/title/artists + 候选 recording 数。"""
    data = urllib.parse.urlencode(
        {"client": api_key, "duration": str(duration), "fingerprint": fp,
         "meta": "recordings releasegroups compress", "format": "json"}
    ).encode()
    for attempt in range(1, 4):
        limiter.wait()
        req = urllib.request.Request(
            ACOUSTID_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(2.0 * attempt)
                continue
            try:
                msg = json.loads(exc.read().decode("utf-8", "replace")).get("error", {})
            except Exception:  # noqa: BLE001
                msg = {"code": exc.code}
            return {"error": f"{msg.get('code')}: {msg.get('message')}"}
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                return {"error": f"{type(exc).__name__}"}
            time.sleep(1.5 * attempt)
            continue

        results = body.get("results") or []
        if not results:
            return {"error": "no_acoustid_result"}
        best = results[0]
        recs = best.get("recordings") or []
        if not recs:
            return {"error": "no_recording"}
        rec = recs[0]
        return {
            "score": round(float(best.get("score", 0)), 3),
            "recording_mbid": rec.get("id", ""),
            "acoustid_title": rec.get("title", ""),
            "acoustid_artists": [a.get("name", "") for a in rec.get("artists") or []],
            "n_recording_candidates": len(recs),
        }
    return {"error": "rate_limited"}


# ───────────────────────── MusicBrainz ─────────────────────────

def mb_get(path: str, query: dict, user_agent: str, limiter: RateLimiter) -> dict:
    url = f"{MB_BASE}/{path}?{urllib.parse.urlencode(query)}"
    for attempt in range(1, 4):
        limiter.wait()
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 503 or exc.code == 429:
                time.sleep(2.0 * attempt)
                continue
            return {"error": f"HTTP {exc.code}"}
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                return {"error": f"{type(exc).__name__}"}
            time.sleep(1.5 * attempt)
    return {"error": "rate_limited"}


def fetch_candidates(recording_mbid: str, user_agent: str, limiter: RateLimiter, track_length: int) -> dict:
    """拉取一个 recording 的全部发行版候选，并整理成紧凑结构。"""
    data = mb_get(
        f"recording/{recording_mbid}",
        # 必须带 media，否则 releases[].media 为 None，拿不到时长与曲目数——
        # 而时长是消歧最强的一条证据（实测：漏掉 media 时模型只能说“无时长可验证”）。
        {"inc": "releases+release-groups+artist-credits+media", "fmt": "json"},
        user_agent, limiter,
    )
    if "error" in data:
        return {"error": data["error"]}

    rec = data
    releases = rec.get("releases") or []
    track_title = rec.get("title", "")

    cands = []
    for rel in releases:
        rg = rel.get("release-group") or {}
        # 该 recording 在这个 release 里的曲目信息（时长是强判别信号）
        track_len = None
        track_no = None
        fmt = ""
        for medium in rel.get("media") or []:
            fmt = fmt or (medium.get("format") or "")
            # MusicBrainz 在 recording 查询里用单数键 'track'（值为数组），
            # 但也可能遇到复数 'tracks'，两者都兼容；值可能是 dict 而非 list。
            raw = medium.get("track")
            if raw is None:
                raw = medium.get("tracks")
            if isinstance(raw, dict):
                raw = [raw]
            for t in raw or []:
                if not isinstance(t, dict):
                    continue
                if track_no is None and t.get("number"):
                    track_no = t.get("number")
                if t.get("length"):
                    track_len = int(t["length"])
                    break
            if track_len:
                break
        labels = [li.get("label", {}).get("name", "") for li in rel.get("label-info") or []]
        cands.append({
            "release_mbid": rel.get("id", ""),
            "album": rel.get("title", ""),
            "date": (rel.get("date") or rg.get("first-release-date") or ""),
            "country": rel.get("country", "") or "",
            "status": rel.get("status", "") or "",
            "format": fmt,
            "track_count": sum((m.get("track-count") or 0) for m in rel.get("media") or []),
            "track_number": track_no,
            "track_length_sec": round(track_len / 1000) if track_len else None,
            "release_group": rg.get("title", "") or "",
            "rg_type": rg.get("primary-type", "") or "",
            "rg_secondary": ", ".join(rg.get("secondary-types") or []),
            "label": labels[0] if labels else "",
        })

    # 只保留有区分度的字段。
    # 注意：不在这里算“时长差”——候选会被缓存跨文件复用，
    # 时长差必须针对**当前文件**实时计算（见 length_delta）。
    cands.sort(
        key=lambda c: (
            abs((c["track_length_sec"] or 0) - track_length) if c["track_length_sec"] else 9999,
            c["date"],
        )
    )
    return {"track_title": track_title, "candidates": cands}


# ───────────────────────── DeepSeek ─────────────────────────

SYSTEM_PROMPT = """你是音乐元数据整理专家，任务是从候选发行版中选出与用户本地文件真正对应的那一个。

判断原则（按可信度从高到低）：
1. **曲目时长吻合度（最强的证据）**：候选里这首歌的时长与文件时长越接近越可信。
   差 5 秒以内很强；差 15 秒以上基本可排除。注意现场的候选都给出了时长差。
2. 专辑名与文件**现有专辑标签**的一致性（若提供了现有标签，这是次强证据）。
3. 文件名线索：文件名里常含曲序、专辑名或单曲名，可作交叉验证。
4. 发行版类型只是**弱**参考：**不要仅仅因为候选是合集/原声带/再版就排除它**——
   用户的文件很可能正是从某张合集里抓的，一旦时长或专辑名支持某个合集，就应该选它。
5. 地区与年份：与文件现有信息吻合的优先。

若证据不足以确定，必须降低 confidence 并在 reason 里说明缺什么证据，不要硬猜。

只输出 JSON，不要任何额外文字。格式：
{
  "chosen_index": <候选数组下标，从 0 开始；无法确定时填 -1>,
  "confidence": <0 到 1 的小数>,
  "reason": "<不超过 80 字的中文理由>",
  "cleaned": {
    "title": "<清洗后的曲名>",
    "artist": "<清洗后的艺术家>",
    "album": "<专辑名>",
    "date": "<发行年份，如 1997>",
    "genre": "<流派，未知则留空>"
  }
}"""


def length_delta(cand: dict, file_duration: int) -> int | None:
    """针对当前文件实时计算时长差（秒）。候选跨文件复用，不能预存。"""
    tl = cand.get("track_length_sec")
    if not tl or not file_duration:
        return None
    return abs(int(tl) - int(file_duration))


def build_user_prompt(name: str, tags: dict, duration: int, cands: list[dict], mode: str) -> str:
    """mode：full=给全部现有标签；no_album=只给曲名/艺术家、隐藏专辑（真实场景）；none=全不给"""
    lines = ["# 待整理的本地文件", f"文件名：{name}", f"时长：{duration} 秒"]
    if mode == "full":
        lines += [
            f"现有标签-曲名：{tags.get('title', '')}",
            f"现有标签-艺术家：{tags.get('artist', '')}",
            f"现有标签-专辑：{tags.get('album', '')}",
            f"现有标签-年份：{tags.get('date', '')}",
        ]
    elif mode == "no_album":
        lines += [
            f"现有标签-曲名：{tags.get('title', '')}",
            f"现有标签-艺术家：{tags.get('artist', '')}",
            "（该文件没有专辑标签，需要你推断它出自哪个发行版）",
        ]
    else:
        lines.append("（该文件没有可用标签，只有文件名与时长）")

    lines += ["", f"# 候选发行版（共 {len(cands)} 个）"]
    for i, c in enumerate(cands):
        delta = length_delta(c, duration)
        delta_s = f"{delta}s" if delta is not None else "未知"
        lines.append(
            f"[{i}] 专辑《{c['album']}》| 日期 {c['date'] or '未知'} | 国家 {c['country'] or '未知'}"
            f" | 状态 {c['status'] or '未知'} | 类型 {c['rg_type'] or '未知'}"
            f"{'/' + c['rg_secondary'] if c['rg_secondary'] else ''}"
            f" | 格式 {c['format'] or '未知'} | 曲目数 {c['track_count']}"
            f" | 本曲时长 {c['track_length_sec'] if c['track_length_sec'] is not None else '未知'}"
            f"（与文件差 {delta_s}） | 厂牌 {c['label'] or '未知'}"
        )
    lines += ["", "请从上述候选中选出最可能对应的那一个，按规定 JSON 格式输出。"]
    return "\n".join(lines)


def deepseek_chat(api_key: str, system: str, user: str, cache_key: str, cache: dict,
                  timeout: int = 90) -> dict:
    if cache_key in cache:
        c = cache[cache_key]
        return {**c, "cached": True}

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 600,
        # 关闭“思考模式”：实测输出 token 从 36 降到 11、耗时 0.9s→0.4s。
        # 本任务是结构化判别，不需要思维链，开（默认）只会变贵变慢。
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        DEEPSEEK_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    t0 = time.monotonic()
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
            usage = body.get("usage") or {}
            result = {
                "latency": round(time.monotonic() - t0, 2),
                "model": body.get("model", DEEPSEEK_MODEL),
                "content": body["choices"][0]["message"]["content"],
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
                "cache_miss_tokens": usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0)),
            }
            cache[cache_key] = result
            return {**result, "cached": False}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2.0 * attempt)
                continue
            return {"error": f"HTTP {exc.code}: {detail}"}
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                return {"error": f"{type(exc).__name__}: {exc}"[:120]}
            time.sleep(1.5 * attempt)
    return {"error": "retry_exhausted"}


def parse_llm_json(content: str) -> dict:
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return {"error": "invalid_json"}
    if not isinstance(obj, dict):
        return {"error": "not_object"}
    ci = obj.get("chosen_index")
    if not isinstance(ci, int):
        return {"error": "bad_chosen_index"}
    conf = obj.get("confidence")
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        conf = None
    cleaned = obj.get("cleaned") if isinstance(obj.get("cleaned"), dict) else {}
    return {
        "chosen_index": ci,
        "confidence": conf,
        "reason": str(obj.get("reason", ""))[:200],
        "cleaned": {
            "title": str(cleaned.get("title", "")),
            "artist": str(cleaned.get("artist", "")),
            "album": str(cleaned.get("album", "")),
            "date": str(cleaned.get("date", "")),
            "genre": str(cleaned.get("genre", "")),
        },
        "schema_ok": True,
    }


# ───────────────────────── 打分（自动代理指标） ─────────────────────────

def auto_score(chosen: dict | None, tags: dict) -> str:
    if not chosen:
        return "未选出"
    album, date = chosen.get("album", ""), chosen.get("date", "")
    na, ta = normalize(album), normalize(tags.get("album", ""))
    album_ok = bool(na and ta) and (na == ta or na in ta or ta in na)
    nd, td = normalize(date)[:4], normalize(tags.get("date", ""))[:4]
    date_ok = bool(nd and td) and nd == td
    if album_ok and (date_ok or not td):
        return "专辑一致"
    if album_ok:
        return "专辑一致·年份不符"
    if date_ok:
        return "年份一致·专辑不同"
    return "不一致"


# ───────────────────────── 主流程 ─────────────────────────

@dataclass
class Row:
    name: str
    path: str
    duration: int = 0
    tag_title: str = ""
    tag_artist: str = ""
    tag_album: str = ""
    tag_date: str = ""
    n_candidates: int = 0
    chosen_index: int = -1
    chosen_album: str = ""
    chosen_date: str = ""
    chosen_confidence: float | None = None
    chosen_reason: str = ""
    cleaned_title: str = ""
    cleaned_artist: str = ""
    cleaned_album: str = ""
    auto_score: str = ""
    latency: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""
    model: str = ""
    cache_hit_tokens: int = 0
    candidates: list[dict] = field(default_factory=list)


def cost_usd(in_miss: int, in_hit: int, out: int, peak: bool) -> float:
    if peak:
        return in_miss / 1e6 * USD_IN_MISS_PEAK + in_hit / 1e6 * USD_IN_HIT_PEAK + out / 1e6 * USD_OUT_PEAK
    return in_miss / 1e6 * USD_IN_MISS_OFFPEAK + in_hit / 1e6 * USD_IN_HIT_OFFPEAK + out / 1e6 * USD_OUT_OFFPEAK


def main() -> int:
    ap = argparse.ArgumentParser(description="P4：多候选消歧验证")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--round", choices=["A", "B", "C", "both"], default="A",
                    help="A=给全部标签；B=完全无标签；C=有曲名/艺术家但无专辑（最接近真实场景）；both=A+B")
    ap.add_argument("--mb-rate", type=float, default=1.0, help="MusicBrainz 每秒最多请求数（官方 1）")
    ap.add_argument("--acoustid-rate", type=float, default=2.0, help="AcoustID 每秒最多请求数（本项目限流：2）")
    ap.add_argument("--max-candidates", type=int, default=15, help="最多送多少个候选给模型")
    ap.add_argument("--budget", type=float, default=3.0, help="本轮云端总预算（元），超了立即停")
    ap.add_argument("--out", default="/tmp/p4_results.json")
    args = ap.parse_args()

    env = load_env()
    for k in ("ACOUSTID_API_KEY", "DEEPSEEK_API_KEY", "MUSICBRAINZ_USER_AGENT", "MUSIC_LIBRARY_PATH"):
        if not env.get(k):
            print(f"❌ .env 缺少 {k}")
            return 1

    fpcalc = str(PROJECT_DIR / "tools" / "fpcalc")
    if not Path(fpcalc).exists():
        print("❌ 找不到 tools/fpcalc")
        return 1

    ac_limiter = RateLimiter(args.acoustid_rate, "acoustid")
    mb_limiter = RateLimiter(args.mb_rate, "musicbrainz")

    fp_cache = load_json(FP_CACHE, {})
    mb_cache = load_json(MB_CACHE, {})
    llm_cache = load_json(LLM_CACHE, {})

    files = sorted(str(p) for p in Path(env["MUSIC_LIBRARY_PATH"]).rglob("*")
                   if p.is_file() and p.suffix.lower() in EXTS)
    if args.limit:
        files = files[: args.limit]

    rounds = ["A", "B"] if args.round == "both" else [args.round]
    MODE = {"A": "full", "B": "none", "C": "no_album"}
    LABEL = {"A": "给全部现有标签", "B": "完全无标签（只有文件名+时长）",
             "C": "有曲名/艺术家、隐藏专辑（真实场景）"}
    print("=" * 92)
    print(f"多候选消歧   文件 {len(files)} 首   轮次 {rounds}")
    print(f"限流：AcoustID {args.acoustid_rate:g}/秒｜MusicBrainz {args.mb_rate:g}/秒（官方 1）")
    print(f"预算上限：¥{args.budget}（超出即停）｜每首最多送 {args.max_candidates} 个候选")
    print("=" * 92)

    import mutagen

    all_rows: dict[str, list[Row]] = {}
    spent = 0.0
    stopped = False

    for rnd in rounds:
        print(f"\n{'─'*92}\n轮次 {rnd}：{LABEL[rnd]}\n{'─'*92}")
        rows: list[Row] = []

        for i, path in enumerate(files, 1):
            if stopped:
                break
            name = Path(path).name
            row = Row(name=name, path=path)
            try:
                mf = mutagen.File(path, easy=True)
                if mf:
                    for key, attr in (("title", "tag_title"), ("artist", "tag_artist"),
                                      ("album", "tag_album"), ("date", "tag_date")):
                        v = mf.get(key)
                        if v:
                            setattr(row, attr, (str(v[0]) if isinstance(v, (list, tuple)) else str(v)).strip())
            except Exception:  # noqa: BLE001
                pass
            tags = {"title": row.tag_title, "artist": row.tag_artist,
                    "album": row.tag_album, "date": row.tag_date}

            # 1) 指纹
            fp = fp_cache.get(path)
            if not fp:
                try:
                    out = subprocess.run([fpcalc, "-json", "-length", "120", path],
                                         capture_output=True, timeout=120, check=False)
                    fp = json.loads(out.stdout.decode("utf-8", "replace"))
                except Exception as exc:  # noqa: BLE001
                    fp = {"error": str(exc)[:80]}
                fp_cache[path] = fp
            if "error" in fp:
                row.error = f"fingerprint: {fp['error']}"
                rows.append(row)
                print(f"  {i:>3}/{len(files)} 💥 {row.error[:60]}")
                continue
            row.duration = int(fp.get("duration", 0))

            # 2) AcoustID → recording
            ac = acoustid_recording(env["ACOUSTID_API_KEY"], fp["fingerprint"], row.duration, ac_limiter)
            if "error" in ac:
                row.error = f"acoustid: {ac['error']}"
                rows.append(row)
                print(f"  {i:>3}/{len(files)} 💥 {row.error[:60]}")
                continue
            mbid = ac["recording_mbid"]

            # 3) MusicBrainz 候选（可缓存）
            if mbid not in mb_cache:
                mb_cache[mbid] = fetch_candidates(mbid, env["MUSICBRAINZ_USER_AGENT"], mb_limiter, row.duration)
            mbd = mb_cache[mbid]
            if "error" in mbd:
                row.error = f"musicbrainz: {mbd['error']}"
                rows.append(row)
                print(f"  {i:>3}/{len(files)} 💥 {row.error[:60]}")
                continue

            cands = mbd["candidates"][: args.max_candidates]
            row.n_candidates = len(mbd["candidates"])
            row.candidates = cands

            # 4) 组 prompt + 调用模型
            prompt = build_user_prompt(name, tags, row.duration, cands, MODE[rnd])
            key = f"v3|{DEEPSEEK_MODEL}|{rnd}|{mbid}|{len(cands)}|{row.tag_album}|{row.duration}"
            res = deepseek_chat(env["DEEPSEEK_API_KEY"], SYSTEM_PROMPT, prompt, key, llm_cache)

            if "error" in res:
                row.error = f"llm: {res['error']}"
            else:
                row.latency = res["latency"]
                row.prompt_tokens = res["prompt_tokens"]
                row.completion_tokens = res["completion_tokens"]
                row.cache_hit_tokens = res.get("cache_hit_tokens", 0)
                if not res.get("cached"):
                    spent += cost_usd(res.get("cache_miss_tokens", row.prompt_tokens),
                                      res.get("cache_hit_tokens", 0), row.completion_tokens, peak=True)
                    row.model = res.get("model", DEEPSEEK_MODEL)
                parsed = parse_llm_json(res["content"])
                if "error" in parsed:
                    row.error = f"schema: {parsed['error']}"
                else:
                    ci = parsed["chosen_index"]
                    row.chosen_index = ci
                    row.chosen_confidence = parsed["confidence"]
                    row.chosen_reason = parsed["reason"]
                    row.cleaned_title = parsed["cleaned"]["title"]
                    row.cleaned_artist = parsed["cleaned"]["artist"]
                    row.cleaned_album = parsed["cleaned"]["album"]
                    chosen = cands[ci] if 0 <= ci < len(cands) else None
                    if chosen:
                        row.chosen_album = chosen["album"]
                        row.chosen_date = chosen["date"]
                    row.auto_score = auto_score(chosen, tags)

            icon = {"专辑一致": "✅", "专辑一致·年份不符": "⚠️", "年份一致·专辑不同": "⚠️",
                    "不一致": "❌", "未选出": "❔"}.get(row.auto_score, "💥")
            print(f"  {i:>3}/{len(files)} {icon} 候选{row.n_candidates:>3} 选[{row.chosen_index:>2}] "
                  f"conf={row.chosen_confidence if row.chosen_confidence is not None else '—'} "
                  f"{row.latency:.1f}s {row.auto_score or row.error[:30]}  {name[:34]}")
            rows.append(row)

        all_rows[rnd] = rows
        save_json(MB_CACHE, mb_cache)
        save_json(FP_CACHE, fp_cache)
        save_json(LLM_CACHE, llm_cache)

        if spent > args.budget:
            print(f"\n⚠️ 已达预算上限 ¥{args.budget}（已花 ¥{spent:.4f}），停止后续轮次")
            stopped = True
            break

    # ─── 汇总 ───
    save_json(MB_CACHE, mb_cache)
    save_json(FP_CACHE, fp_cache)
    save_json(LLM_CACHE, llm_cache)

    print()
    print("=" * 92)
    print("结果汇总")
    print("=" * 92)
    for rnd, rows in all_rows.items():
        if not rows:
            continue
        ok = [r for r in rows if not r.error]
        good = [r for r in ok if r.auto_score in ("专辑一致", "专辑一致·年份不符")]
        lat = [r.latency for r in ok if r.latency]
        pt = sum(r.prompt_tokens for r in ok)
        ct = sum(r.completion_tokens for r in ok)
        print(f"\n轮次 {rnd}（{LABEL[rnd]}）：")
        print(f"  处理成功            {len(ok)} / {len(rows)}")
        print(f"  专辑选对（自动判定）  {len(good)}  ({len(good)/max(len(ok),1)*100:.0f}%)")
        print(f"  专辑一致·年份不符     {len([r for r in ok if r.auto_score == '专辑一致·年份不符'])}")
        print(f"  年份一致·专辑不同     {len([r for r in ok if r.auto_score == '年份一致·专辑不同'])}")
        print(f"  不一致               {len([r for r in ok if r.auto_score == '不一致'])}")
        print(f"  未选出               {len([r for r in ok if r.auto_score == '未选出'])}")
        if lat:
            lat_sorted = sorted(lat)
            print(f"  时延：中位 {lat_sorted[len(lat_sorted)//2]:.2f}s｜最大 {max(lat):.2f}s（不含指纹与网络查询）")
        print(f"  tokens：输入 {pt:,}（其中命中缓存 {sum(r.cache_hit_tokens for r in ok):,}）｜输出 {ct:,}")
        if ok:
            c_off = cost_usd(pt, sum(r.cache_hit_tokens for r in ok), ct, peak=False) * USD_TO_CNY
            c_peak = cost_usd(pt, sum(r.cache_hit_tokens for r in ok), ct, peak=True) * USD_TO_CNY
            print(f"  成本（官方价目 ×{USD_TO_CNY} 汇率估算）：谷时 ¥{c_off:.4f}｜峰时 ¥{c_peak:.4f}")
            print(f"  → 折合每 100 首：谷时 ¥{c_off/len(ok)*100:.3f}｜峰时 ¥{c_peak/len(ok)*100:.3f}")

    total_rows = [r for rows in all_rows.values() for r in rows if not r.error]
    tin = sum(r.prompt_tokens for r in total_rows)
    thit = sum(r.cache_hit_tokens for r in total_rows)
    tout = sum(r.completion_tokens for r in total_rows)
    tot_usd = cost_usd(tin, thit, tout, peak=False)
    print(f"\n总花费（谷时估算，含两轮）：¥{tot_usd*USD_TO_CNY:.4f}｜预算上限 ¥{args.budget}")
    print(f"模型：{DEEPSEEK_MODEL}（已关闭思考模式）｜缓存命中的输入 token：{thit:,} / {tin:,}")

    # ─── 人工核对表 ───
    sheet = ["# 人工核对表", ""]
    sheet.append("> 每条只需回答一个问题：**接受还是不接受**？不能接受的请在备注里写一句话原因。")
    sheet.append("")
    for rnd, rows in all_rows.items():
        sheet.append(f"## 轮次 {rnd}（{LABEL[rnd]}）")
        sheet.append("")
        sheet.append("| # | 文件 | 现有专辑 | AI 选的专辑 | AI 给的年份 | 把握 | AI 的理由 | 接受? | 备注 |")
        sheet.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for i, r in enumerate([r for r in rows if not r.error], 1):
            sheet.append(
                f"| {i} | `{r.name[:46]}` | {r.tag_album or '—'} | **{r.chosen_album or '—'}** | "
                f"{r.cleaned_album and ''}{r.chosen_date or '—'} | "
                f"{'—' if r.chosen_confidence is None else f'{r.chosen_confidence:.2f}'} | "
                f"{r.chosen_reason} | | |"
            )
        sheet.append("")
    sheet_path = "/tmp/人工核对表.md"
    Path(sheet_path).write_text("\n".join(sheet), encoding="utf-8")

    save_json(Path(args.out), {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rounds": {k: [asdict(r) for r in v] for k, v in all_rows.items()},
        "estimated_cost_cny": round(tot_usd * USD_TO_CNY, 4),
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing (deepseek-flash)",
        "price_assumption": {"in_miss_usd": USD_IN_MISS_OFFPEAK, "in_hit_usd": USD_IN_HIT_OFFPEAK,
                             "out_usd": USD_OUT_OFFPEAK, "usd_cny": USD_TO_CNY},
        "model": DEEPSEEK_MODEL,
    })
    print(f"\n详情：{args.out}")
    print(f"人工核对表：{sheet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
