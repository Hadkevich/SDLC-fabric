-- Agentic SDLC — DB-backed control plane (SQLite).
--
-- The DB is the source of truth for inter-agent JSON artifacts and the all-tasks
-- table that many agents and several pipelines share. Project *code* files stay
-- on the local filesystem (see the file-on-edge worker). Live status is a
-- projection foldable from `events` (the append-only log), so a crash never
-- silently loses progress.
--
-- All access goes through src/sdlcdb/db.py, which sets WAL + busy_timeout so a
-- later swap to Postgres stays localized to that one module.

-- One row per workflow run. Several may be `running` concurrently (multi-pipeline).
CREATE TABLE IF NOT EXISTS pipelines (
    pipeline_id  TEXT PRIMARY KEY,               -- uuid
    name         TEXT,                           -- project dir name (projects/<name>)
    request      TEXT,                           -- raw user prompt
    status       TEXT NOT NULL DEFAULT 'running' -- running|awaiting_approval|complete|failed
                 CHECK (status IN ('running','awaiting_approval','complete','failed')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- The all-tasks table: every task of every agent, across every pipeline.
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,             -- uuid, unique across all pipelines
    pipeline_id    TEXT NOT NULL REFERENCES pipelines(pipeline_id),
    agent_role     TEXT NOT NULL,                -- product-agent, planner-agent, ...
    stage          TEXT NOT NULL,                -- lifecycle stage this task belongs to
    title          TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','claimed','in_progress','done',
                                     'error','awaiting_approval','blocked','skipped')),
    attempt        INTEGER NOT NULL DEFAULT 0,   -- transient retries (per task)
    max_retries    INTEGER NOT NULL DEFAULT 3,
    heal_round     INTEGER NOT NULL DEFAULT 0,   -- evaluator healing rounds (capped)
    depends_on     TEXT NOT NULL DEFAULT '[]',   -- JSON array of upstream task_ids
    inputs         TEXT NOT NULL DEFAULT '[]',   -- JSON array of artifact names to read
    outputs        TEXT NOT NULL DEFAULT '[]',   -- JSON array of artifact names to write
    request        TEXT,                         -- per-task user request (product agent)
    healing_prompt TEXT,                         -- injected by the evaluator on heal
    fingerprint    TEXT,                         -- hash(spec + input hashes + model)
    payload        TEXT,                         -- JSON: agent's done/error result/summary
    claimed_by     TEXT,                         -- worker id holding the lease
    lease_until    TEXT,                         -- ISO ts; an expired lease => requeue
    created_at     TEXT NOT NULL,
    claimed_at     TEXT,
    started_at     TEXT,
    completed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_claim
    ON tasks (agent_role, status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_pipeline
    ON tasks (pipeline_id, status);

-- Inter-agent JSON artifacts. DB = source of truth (NOT local files).
-- Latest write per (pipeline, name) wins; content_hash powers skip-unchanged.
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id      TEXT PRIMARY KEY,           -- uuid
    pipeline_id      TEXT NOT NULL REFERENCES pipelines(pipeline_id),
    producer_task_id TEXT REFERENCES tasks(task_id),
    name             TEXT NOT NULL,              -- requirements.json, code_spec/<id>.json
    content          TEXT NOT NULL,              -- the JSON text
    schema_name      TEXT,                       -- schema it validated against, if any
    content_hash     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (pipeline_id, name)
);

-- Append-only event log. The status above is a *projection* rebuildable by
-- folding this table (db.fold_state). `processed_at` is the watcher trigger:
-- NULL means the orchestrator has not yet routed this event.
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,            -- uuid (stamped by the engine)
    pipeline_id     TEXT NOT NULL REFERENCES pipelines(pipeline_id),
    task_id         TEXT,
    stage           TEXT NOT NULL,
    agent           TEXT NOT NULL,
    status          TEXT NOT NULL,               -- success|failure|blocked|retry
    summary         TEXT,
    blocking_issues TEXT NOT NULL DEFAULT '[]',  -- JSON array
    input_refs      TEXT NOT NULL DEFAULT '[]',  -- JSON array
    output_refs     TEXT NOT NULL DEFAULT '[]',  -- JSON array
    metrics         TEXT,                         -- JSON: cost/tokens/duration
    retry_count     INTEGER NOT NULL DEFAULT 0,
    timestamp       TEXT NOT NULL,
    processed_at    TEXT                          -- NULL => new, unrouted event
);

CREATE INDEX IF NOT EXISTS idx_events_unprocessed
    ON events (processed_at, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_pipeline
    ON events (pipeline_id, timestamp);

-- Human sign-offs (SPEC §8.6). A gated stage's tasks wait in `awaiting_approval`
-- until an operator records the matching gate here (the watcher won't spawn them).
CREATE TABLE IF NOT EXISTS approvals (
    pipeline_id TEXT NOT NULL REFERENCES pipelines(pipeline_id),
    gate        TEXT NOT NULL,                   -- requirements|architecture|production_deploy
    approved_at TEXT NOT NULL,
    PRIMARY KEY (pipeline_id, gate)
);
