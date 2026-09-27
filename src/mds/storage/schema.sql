-- 音乐数据管家 · schema
-- 原则：每个 item 的每一步都即时提交，使"跑到一半被杀进程"后能精确续跑（S6）。

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- 一次运行（一个音乐库目录 + 一套参数）
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    music_root  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | done | failed
    params_json TEXT NOT NULL,
    stats_json  TEXT
);

-- 每个文件一条。各 *_status 是续跑的判据。
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    path            TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    size            INTEGER,
    mtime           REAL,
    ext             TEXT,
    duration_sec    INTEGER,

    tags_json       TEXT,               -- 读到的现有标签

    fp_status       TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | error
    fp_error        TEXT,

    ac_status       TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | no_result | error
    ac_score        REAL,
    ac_title        TEXT,
    recording_mbid  TEXT,
    ac_error        TEXT,

    cand_status     TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | no_result | error
    cand_error      TEXT,
    n_candidates    INTEGER DEFAULT 0,
    candidates_json TEXT,

    llm_status      TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | schema_error | skipped | error
    llm_error       TEXT,
    suggestion_json TEXT,
    confidence      REAL,
    chosen_index    INTEGER,
    decision_json   TEXT,
    verdict         TEXT,

    -- （写入链路）
    plan_json       TEXT,                 -- WritePlan
    user_choice     TEXT,                 -- kept_existing | adopted_ai | picked | skipped
    snap_ref        TEXT,                 -- 快照引用（JSON: SnapshotRef）
    write_status    TEXT NOT NULL DEFAULT 'pending',
                       -- pending | planned | snapshotted | written | verified | failed | skipped
    write_error     TEXT,
    before_sha      TEXT,                 -- 写前 sha256（回滚完整性判据）
    after_sha       TEXT,                 -- 写后 sha256
    written_at      TEXT,

    error           TEXT,
    updated_at      TEXT NOT NULL,
    -- 所属的「专辑目录」组
    group_id        INTEGER,
    UNIQUE(run_id, path)
);

CREATE INDEX IF NOT EXISTS idx_items_run ON items(run_id);

-- LLM 调用审计（成本核算与排错）
CREATE TABLE IF NOT EXISTS llm_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT,
    item_id           INTEGER,
    model             TEXT,
    mode              TEXT,
    prompt_tokens     INTEGER,
    cache_hit_tokens  INTEGER,
    completion_tokens INTEGER,
    latency_ms        INTEGER,
    cost_usd          REAL,
    status            TEXT,
    created_at        TEXT NOT NULL
);

-- ── 跨 run 复用的缓存：省时间、也省钱 ──────────────────────────
-- 指纹：以 (路径, 大小, mtime) 为键，文件变了就自动失效
CREATE TABLE IF NOT EXISTS cache_fingerprint (
    path_key    TEXT PRIMARY KEY,
    size        INTEGER,
    mtime       REAL,
    fingerprint TEXT,
    duration_sec INTEGER,
    updated_at  TEXT NOT NULL
);

-- MusicBrainz 候选：以 recording MBID 为键（同一录音跨文件复用）
CREATE TABLE IF NOT EXISTS cache_mb (
    recording_mbid TEXT PRIMARY KEY,
    payload_json   TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- LLM 结果：以 prompt_hash 为键（同提示同答案，完全可复用）
CREATE TABLE IF NOT EXISTS cache_llm (
    prompt_hash TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    model       TEXT,
    updated_at  TEXT NOT NULL
);

-- 写入前后的完整快照（回滚依据）
CREATE TABLE IF NOT EXISTS changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT,
    item_id      INTEGER,
    path         TEXT NOT NULL,
    before_json  TEXT NOT NULL,          -- 写前全部原始标签（Picard rawitems）
    after_json   TEXT,                   -- 写后标签
    snapshot_ref TEXT,                   -- 快照引用 JSON
    before_sha   TEXT,
    after_sha    TEXT,
    status       TEXT NOT NULL DEFAULT 'applied',   -- applied | rolled_back | failed
    created_at   TEXT NOT NULL,
    rolled_back_at TEXT,
    -- 一次 apply 算一个批次，用于「撤销上一批写入」
    batch_id     TEXT
);

-- 注意：这里**不能**建 idx_changes_batch。老库的 changes 表在跑 schema.sql 时
-- 还没有 batch_id 列（那是迁移才加的），建索引会报 no such column。
-- 该索引由 db._ensure_extra_indexes() 在迁移之后补建（新库老库都会走到）。

CREATE INDEX IF NOT EXISTS idx_changes_run ON changes(run_id);
CREATE INDEX IF NOT EXISTS idx_changes_item ON changes(item_id);

-- 专辑目录分组（父目录相同的文件为一组）
CREATE TABLE IF NOT EXISTS groups (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(id),
    folder_path    TEXT NOT NULL,       -- 父目录绝对路径
    n_files        INTEGER NOT NULL,
    folder_hint    TEXT,                -- 清洗后的目录名（仅作线索，不作写入值）
    artist_hint    TEXT,
    consensus_json TEXT,                -- 各专辑级字段的同目录共识
    created_at     TEXT NOT NULL,
    UNIQUE(run_id, folder_path)
);

CREATE INDEX IF NOT EXISTS idx_groups_run ON groups(run_id);

-- 界面偏好（**不含任何密钥**；密钥走系统凭据库，见 adapters/secrets.py）
CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
