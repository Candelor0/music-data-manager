"""核心数据模型（Pydantic v2）。纯数据，无副作用。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 字段长度上限，防止模型输出异常长字符串污染数据库
MAX_TITLE = 300
MAX_ARTIST = 300
MAX_ALBUM = 300
MAX_DATE = 10
MAX_GENRE = 100
MAX_REASON = 300


class Tags(BaseModel):
    """一个文件的标签现状（我们关心的字段）。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    date: str = ""
    genre: str = ""
    composer: str = ""
    tracknumber: str = ""
    discnumber: str = ""

    def value_of(self, field: str) -> str:
        """取字段值（字符串，缺失为空串）。

        不能叫 `get`：Pydantic v2 的 BaseModel 已占用 `get(item, default)` 签名，
        覆盖它会在库内部调用时炸掉（实测 TypeError: takes 2 positional arguments but 3 were given）。
        """
        return str(getattr(self, field, "") or "")

    def is_empty(self) -> bool:
        return not any(self.value_of(f) for f in self.model_fields)

    def to_dict(self) -> dict[str, str]:
        return {f: self.value_of(f) for f in self.model_fields}


class Candidate(BaseModel):
    """一个发行版候选（来自 MusicBrainz）。"""

    release_mbid: str = ""
    album: str = ""
    date: str = ""
    country: str = ""
    status: str = ""
    format: str = ""
    track_count: int = 0
    track_number: str = ""
    track_length_sec: int | None = None
    release_group: str = ""
    rg_type: str = ""
    rg_secondary: str = ""
    label: str = ""

    def length_delta(self, file_duration_sec: int) -> int | None:
        """与当前文件的时长差（秒）。候选会被跨文件复用缓存，所以必须实时算。"""
        if not self.track_length_sec or not file_duration_sec:
            return None
        return abs(int(self.track_length_sec) - int(file_duration_sec))


class ItemView(BaseModel):
    """给决策层看的文件视图。"""

    path: str
    name: str
    duration_sec: int = 0
    tags: Tags = Field(default_factory=Tags)
    group_key: str = ""  # 预留：同目录/同专辑分组，后续使用


class CleanedTags(BaseModel):
    """模型清洗后的标签。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    date: str = ""
    genre: str = ""

    @field_validator("title", "artist", "album")
    @classmethod
    def _limit_300(cls, v: str) -> str:
        return (v or "").strip()[:MAX_TITLE]

    @field_validator("date")
    @classmethod
    def _limit_date(cls, v: str) -> str:
        return (v or "").strip()[:MAX_DATE]

    @field_validator("genre")
    @classmethod
    def _limit_genre(cls, v: str) -> str:
        return (v or "").strip()[:MAX_GENRE]


class Suggestion(BaseModel):
    """模型的结构化输出（已通过校验）。"""

    chosen_index: int = -1
    confidence: float | None = None
    reason: str = ""
    cleaned: CleanedTags = Field(default_factory=CleanedTags)

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float | None) -> float | None:
        if v is None:
            return None
        return min(1.0, max(0.0, float(v)))

    @field_validator("reason")
    @classmethod
    def _limit_reason(cls, v: str) -> str:
        return (v or "").strip()[:MAX_REASON]


class TagChange(BaseModel):
    """一个字段的改动。"""

    field: str
    before: str = ""
    after: str = ""
    kind: Literal["fill", "cleanup", "normalize", "consensus"] = "fill"


# 决策动作
DecisionAction = Literal[
    "no_op",  # 无需改动
    "fill_missing",  # 仅补空字段（不动任何已有值）
    "cleanup",  # 建议清洗/规范化已有字段（同值不同写法，或去除标题尾部杂质）
    "keep_existing",  # 有冲突：默认保留现有专辑标签，但列出 AI 的不同判断供用户选（R2）
    "ask_user",  # 证据不足 → 呈现候选交用户选择（R3）
    "no_evidence",  # 连候选都没有 → 无法处理
]

# 证据强度
EvidenceLevel = Literal["album_tag_match", "length_unique", "insufficient", "none"]


class Decision(BaseModel):
    """决策层输出。所有写入路径都必须消费它。"""

    action: DecisionAction
    evidence: EvidenceLevel = "insufficient"
    changes: list[TagChange] = Field(default_factory=list)
    chosen: Candidate | None = None
    show_candidates: list[Candidate] = Field(default_factory=list)
    reason: str = ""
    # 有冲突时（keep_existing）保留 AI 的判断供用户选；None 表示无冲突
    alternative: Candidate | None = None
    # 该文件「已有值 与 同目录共识不一致」的字段（仅提示，不产生改动）
    conflicts: list[str] = Field(default_factory=list)
    # 本次用到了哪个目录的共识（供界面显示来源）
    group_path: str = ""


# ─────────────────────


class FieldConsensus(BaseModel):
    """某个专辑级字段在同目录内的共识。"""

    value: str = ""
    votes: int = 0      # 有多少个文件是这个值
    total: int = 0      # 这个目录一共多少个文件
    ratio: float = 0.0  # 有值文件里，众数的占比

    @property
    def is_strong(self) -> bool:
        return bool(self.value) and self.votes >= 2


class FolderGroup(BaseModel):
    """一个「专辑目录」：父目录相同的所有文件。"""

    folder_path: str = ""
    n_files: int = 0
    folder_hint: str = ""           # 清洗后的目录名（仅作线索，不作写入值）
    artist_hint: str = ""           # 组内艺术家共识（用于清洗目录名前缀）
    consensus: dict[str, FieldConsensus] = Field(default_factory=dict)

    def consensus_value(self, field: str) -> str:
        item = self.consensus.get(field)
        return item.value if item else ""


# ─────────────────────

WriteStatus = Literal[
    "pending",      # 还没生成计划
    "planned",      # 有计划但未执行
    "snapshotted",  # 快照已建，文件未动
    "written",      # 已写入，待校验
    "verified",     # 已写入并校验通过
    "failed",       # 失败（已尝试回滚）
    "skipped",      # 无需改动 / 用户跳过
]


class WritePlan(BaseModel):
    """一条目将要写什么。纯数据；由 core.writeplan 生成，**不碰任何文件**。"""

    allowed: bool = False
    reason: str = ""
    changes: list[TagChange] = Field(default_factory=list)
    target: Tags | None = None
    touches_album: bool = False
    source: str = ""  # fill_missing | cleanup | adopted_ai | ask_user_choice

    def summary(self) -> str:
        if not self.allowed:
            return f"不写入（{self.reason}）"
        return "; ".join(f"{c.field}: {c.before or '（空）'} → {c.after}" for c in self.changes)


class SnapshotRef(BaseModel):
    """快照引用（回滚依据）。"""

    item_id: int = 0
    mode: str = "tags"  # tags | full
    path: str = ""
    blob: str = ""  # 快照文件路径
    before_sha: str = ""  # 写前 sha256（S9 回滚完整性判据）
    after_sha: str = ""   # 写后 sha256（用于检测回滚前是否又被外部改过）
    size: int = 0
    mtime: float = 0.0
    created_at: str = ""
    # 写前的"音频+图片"指纹（回滚后可比对，证明只动了标签）
    audio_sha: str = ""
    audio_len: int = 0
    pictures_sha: str = ""
    n_pictures: int = 0


class WriteResult(BaseModel):
    """写入结果。"""

    status: WriteStatus = "pending"
    error: str = ""
    before_sha: str = ""
    after_sha: str = ""
    changes: list[TagChange] = Field(default_factory=list)
    rolled_back: bool = False
