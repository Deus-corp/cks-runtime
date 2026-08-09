# ADR-014: Standalone Agent Liveness Heartbeat

**Status:** Proposed
**Related:** ADR-004 (Storage Abstraction), ADR-007 (Concurrent Multi-Agent
Writes), cks-mcp ADR-007 (CKSAgentOrchestrator), cks-studio
`AGENT_VISIBILITY.md` (v1 read-only sweeper panel, already shipped)

## Context

`list_agents`/`agent_status` (cks-mcp) report only the seven in-process
reasoning sweepers (`ContradictionSweeper`, `InferenceStalenessSweeper`,
etc.) that live inside the MCP server's own `Runtime` instance. The four
standalone agents — Critic Agent (`critic_agent.py`), Enrichment Agent
(`enrichment_agent.py`), Fork Resolution Agent
(`fork_resolution_agent.py`), Pipeline Agent (`pipeline_agent.py`) — each
run as their own OS process with their own `Runtime` and storage
connection. Nothing today answers "is the Critic Agent process actually
running, and when did it last do anything?" short of shelling into the
host and checking `ps`.

Each of these four processes already has an unrelated per-process
`heartbeat_interval` setting (`CriticAgentSettings.heartbeat_interval`,
`CKS_CRITIC_HEARTBEAT_INTERVAL`, default 60s; same shape in the other
three). That heartbeat exists to keep an **outbox task lease** alive
while `_run_resolver_with_heartbeat` (`cks_mcp.agent_loop`) is still
working a single claimed task — its cadence is chosen to stay
comfortably under `SQLiteStorage`/`PostgresStorage`'s 5-minute
stale-lease reclaim window. It says nothing about whether the process
itself is alive when it isn't currently holding a lease (e.g. idle
between poll cycles, or between tasks). Reusing that name or that timer
for process liveness would conflate two different failure modes: "this
agent died mid-task, reclaim its lease" vs. "this agent's process is
gone, stop expecting it to pick anything up." This ADR introduces a
second, independent concept for the latter and deliberately does not
touch `heartbeat_interval`/`CKS_*_HEARTBEAT_INTERVAL` or the lease
mechanism at all.

## Decision

### 1. New table: `cks_agent_liveness`

One row per standalone-agent **process instance** (not per agent kind —
a restarted process gets a fresh row, it does not overwrite the old
one; see TTL read-path below for why this doesn't need cleanup-on-write).

```sql
CREATE TABLE IF NOT EXISTS cks_agent_liveness (
    instance_id         TEXT PRIMARY KEY,   -- uuid4, generated once at process start
    process_kind        TEXT NOT NULL,      -- 'critic' | 'enrichment' | 'fork_resolution' | 'pipeline'
    hostname             TEXT NOT NULL,
    pid                  INTEGER NOT NULL,
    liveness_interval_s  REAL NOT NULL,      -- this instance's configured interval (per-process, see §2)
    started_at           TEXT NOT NULL,      -- ISO 8601, set once
    last_heartbeat_at    TEXT NOT NULL,      -- ISO 8601, updated every tick
    current_task_id      INTEGER,            -- cks_outbox_tasks.task_id if currently leased, else NULL
    current_task_type    TEXT                -- denormalized for cheap display, avoids a join for the panel
);

CREATE INDEX IF NOT EXISTS idx_agent_liveness_kind
ON cks_agent_liveness(process_kind, last_heartbeat_at);
```

Follows the same `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info`
add-column migration convention already used for every other table in
`sqlite_storage.py`/`postgres_storage.py` (see `graph_registry.public`,
`cks_outbox_tasks.claimed_at`) — no separate migration runner, no schema
version table. Ships in both backends behind the existing
`Storage`/`StorageAdapter` interface (ADR-004), as one new abstract
method pair:

```python
async def upsert_agent_liveness(self, record: AgentLivenessRecord) -> None: ...
async def list_agent_liveness(self) -> list[AgentLivenessRecord]: ...
```

`upsert_agent_liveness` is a plain `INSERT ... ON CONFLICT(instance_id)
DO UPDATE` (SQLite) / `INSERT ... ON CONFLICT DO UPDATE` (Postgres) —
one row is only ever written by the one process that owns
`instance_id`, so there is no cross-process write contention to design
around (unlike the outbox lease, which ADR-007 already solved for the
genuinely-contended case).

### 2. Per-process configurable interval (not a single global constant)

Each of the four `*Settings.from_env()` dataclasses gains one new field,
independent from the existing `heartbeat_interval` (task-lease) field:

```python
liveness_interval: float = _DEFAULT_LIVENESS_INTERVAL_SECONDS  # default 30.0
```

read from a new, distinctly-named env var per process — following the
exact naming convention already used for the other three (poll/max
retries/heartbeat all get a `CKS_<AGENT>_` prefix):

| Process          | Env var                              |
|------------------|---------------------------------------|
| Critic Agent     | `CKS_CRITIC_LIVENESS_INTERVAL`        |
| Enrichment Agent | `CKS_ENRICHMENT_LIVENESS_INTERVAL`    |
| Fork Resolution  | `CKS_FORK_AGENT_LIVENESS_INTERVAL`    |
| Pipeline Agent   | `CKS_PIPELINE_LIVENESS_INTERVAL`      |

Rationale for per-process over one global constant: the four loops
already run at different natural cadences (`_DEFAULT_POLL_INTERVAL_SECONDS`
is 5s for Critic/Enrichment, 30s for Fork Resolution) and are deployed
independently — an operator running Fork Resolution on a slower/cheaper
box shouldn't be forced onto the same liveness cadence as a
tightly-polled Critic Agent. Default value (30s) is picked independently
of each process's `poll_interval` — liveness is "is the OS process up",
not "did it just do work" — so a single shared default across all four
is fine even though the field is per-process-overridable.

Each process's main loop (`run_critic_agent`/`run_enrichment_agent`/
etc. in `cks_mcp.agent_loop`) calls `upsert_agent_liveness` once at
startup (`started_at` = `last_heartbeat_at` = now) and again every
`liveness_interval` seconds via a plain `asyncio` background tick —
structurally the same "second concurrent loop alongside the main poll
loop" shape `_run_resolver_with_heartbeat` already establishes for the
lease heartbeat, just decoupled from task-holding. `current_task_id`/
`current_task_type` are updated opportunistically (set when a task is
claimed, cleared to `NULL` on completion/failure/dead-letter) — best
effort, not authoritative; a crash mid-task can leave a stale
`current_task_id` until the next full heartbeat, which is acceptable
since the outbox table (not this one) is the source of truth for task
state.

### 3. TTL-on-read, not TTL-on-write

No cleanup sweeper, no `DELETE` on a schedule. `process_status` (new
tool, see §4) computes liveness at **read time**:

```
alive = (now - last_heartbeat_at) <= 3 * liveness_interval_s
```

3× rather than 2× — enough slack to absorb one missed tick from GC
pause, a slow storage round-trip, or a single delayed `asyncio` task
without flapping the status, while still catching a genuinely dead
process within a bounded, short window (at the 30s default: dead
process shows `stopped` within 90s, not indefinitely). Rows for
processes that have been stopped for a long time are **not** deleted —
they're valid history ("was this ever running, and when did it last
report in") and cost is negligible (one row per process instance,
these accumulate slowly). If unbounded accumulation across many
restarts ever becomes a real concern, that's a separate, later decision
(e.g. prune rows older than N days) — not blocking this ADR.

### 4. Separate tools, not merged into `list_agents`

`list_agents`/`agent_status` keep their current contract unchanged —
sweepers only, same response shape, same `kind: "sweeper"` field
already documented. Two new tools instead of extending those:

- `list_processes` — returns every row from `cks_agent_liveness`,
  each annotated with computed `status: "alive" | "stopped"` (per the
  TTL rule above) and `process_kind`.
- `process_status(process_kind)` — same per-row shape, filtered to the
  most recently-started instance of that kind (there can be more than
  one row per `process_kind` across restarts; only the newest is
  "the" current status for that kind).

Rationale for separate tools over folding a `kind: "process"` variant
into `list_agents`: the two record shapes only superficially resemble
each other. Sweeper status is about **work done** (`last_result_count`,
`last_run_duration_ms`, `last_error` from a specific pass) on a
predictable, self-throttled interval measured in sweeps. Process status
is about **whether the process exists at all** (`pid`, `hostname`,
`current_task_id`) on an interval measured in seconds, decoupled from
whatever work it's doing. Padding one response shape with fields that
are always-null for the other `kind` (`last_result_count` is
meaningless for a process row; `pid`/`hostname` are meaningless for an
in-process sweeper) would make both harder to document and consume
than two small, honest tools. `cks-studio`'s Agent Panel (v1) already
renders sweepers via `listAgents()`; a `listProcesses()` wrapper next to
it, in its own card group, is a smaller diff than reshaping the
existing sweeper-only response and its consumer.

## Consequences

- **New table, new storage-adapter methods, both backends.** Same
  migration shape already used everywhere else in
  `sqlite_storage.py`/`postgres_storage.py` — no new infrastructure
  category introduced.
- **Four small, mechanical changes** — one `upsert_agent_liveness` call
  at startup plus one background tick added to each of
  `run_critic_agent`, `run_enrichment_agent`,
  `run_fork_resolution_agent`, `run_pipeline_agent` in
  `cks_mcp.agent_loop` and each of the four `*Settings` dataclasses.
  Not "just another diff" in aggregate — this is the piece of work the
  Agent Visibility plan flagged as needing its own ADR before code, and
  this section is that ADR.
- **No coupling to the task-lease heartbeat.** `heartbeat_interval`/
  `CKS_*_HEARTBEAT_INTERVAL` (lease-keepalive) and
  `liveness_interval`/`CKS_*_LIVENESS_INTERVAL` (process-alive) stay
  fully independent knobs, independently defaulted, independently
  documented — avoids the exact ambiguity flagged in Context.
  Existing `CriticAgentMetrics`/lease-loss counters are untouched.
- **`cks-studio` follow-up (out of scope here):** once `list_processes`
  ships, the Agent Panel gets a second, clearly-separated card group
  ("Processes") next to the existing sweeper cards, reusing
  `useAgentsPolling`'s shape with a second hook (`useProcessesPolling`)
  pointed at the new tool — no changes needed to the v1 sweeper code
  path. Still no start/stop actions here; Control Panel (start/stop/
  pause/resume) remains blocked on this ADR landing first, plus its own
  separate design for the IPC/signal problem the original plan called
  out (a `stop` for a standalone agent process can't be a bare RPC call
  into another OS process without some signal mechanism — e.g. a
  `desired_state` column here that the process itself polls and acts on
  between iterations, or `SIGTERM` via a supervisor). That remains
  future work, deliberately not decided by this ADR.
- **Open item deferred, not blocking:** whether `list_processes` needs
  pagination once instance history grows large. Not addressed here;
  revisit if row count in practice becomes a problem (see §3).
