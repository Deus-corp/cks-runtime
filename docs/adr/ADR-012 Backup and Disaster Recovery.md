# ADR-012: Backup and Disaster Recovery

**Status:** Proposed
**Related:** ADR-004 (Storage Abstraction), ADR-005 (Version History), ADR-008 (Gossip Replication)

## Context

`cks-runtime` currently ships three storage backends: `InMemoryStorage`
(for tests), `SQLiteStorage` (local/single-process use), and
`PostgresStorage` (production). All three implement the same
`RuntimeStorage` / `AsyncRuntimeStorage` contract — they persist
sessions, version history, the graph registry, embedding vectors, and
outbox tasks — but neither the abstract interface nor any concrete
backend exposes a mechanism to:

1. **Back up** a running store to a portable snapshot a human operator
   can keep off-site, inspect, or restore from.
2. **Migrate** from one backend to another (the common "I started with
   SQLite and now need Postgres" path).
3. **Restore** a previously taken snapshot into a fresh store, either
   wholesale (disaster recovery) or additively (merging two stores after
   a split-brain incident).

Because there is no export/import primitive today, a user who wants any
of these things must reach directly into the SQLite file with an external
tool, write their own SQL dump script, or accept that their data is
effectively trapped in whichever backend they chose at startup. This is
a significant operability gap that grows with every new production
deployment.

## Decision

### 1. Two new methods on the storage interfaces

Add `export_storage` and `import_storage` to both `RuntimeStorage` and
`AsyncRuntimeStorage` as concrete no-op / `NotImplementedError` defaults
(not abstract — backends that don't support backup simply don't override
them, preserving backward compatibility):

```python
# RuntimeStorage (storage.py)
def export_storage(self) -> dict:
    raise NotImplementedError

def import_storage(self, data: dict, mode: str = "merge") -> None:
    raise NotImplementedError
```

```python
# AsyncRuntimeStorage (async_storage.py)
async def export_storage(self) -> dict:
    raise NotImplementedError

async def import_storage(self, data: dict, mode: str = "merge") -> None:
    raise NotImplementedError
```

**`export_storage() -> dict`** returns a self-describing JSON-serialisable
dictionary that is a complete snapshot of every table the backend owns:

```jsonc
{
  "version": 1,                  // schema version for forward compat
  "exported_at": "<ISO-8601>",
  "sessions": [ <raw session JSON strings> ... ],
  "versions": [ <raw version JSON strings> ... ],
  "graphs": [
    {
      "name": "...", "session_id": "...", "description": "...",
      "tags": "...", "public": false,
      "created_at": "...", "updated_at": "..."
    }
    ...
  ],
  "embeddings": [
    { "object_id": "...", "session_id": "...", "embedding_b64": "...", "updated_at": "..." }
    ...
  ],
  "outbox_tasks": [
    {
      "task_type": "...", "session_id": "...", "payload": "...",
      "status": "PENDING", "retry_count": 0,
      "next_retry_at": "...", "created_at": "..."
    }
    ...
  ]
}
```

Sessions and versions are stored as their already-serialised JSON strings
(the same payload written to the `data` column in SQLite/Postgres). This
means the dump is backend-agnostic: any backend can import what any other
backend exported without knowing the other's internal schema. Embeddings
are base64-encoded binary blobs. Only `PENDING` and `FAILED` outbox
tasks are exported — `IN_PROGRESS`, `DEAD`, and `COMPLETED` tasks are
omitted because they are either claimed (and should not be re-claimed on
restore) or terminal.

**`import_storage(data: dict, mode: str = "merge") -> None`** accepts such
a dictionary and restores its contents into the current backend.

`mode` controls collision handling:

- `"clear"` — truncate every table first, then insert the snapshot.
  Use for disaster recovery restores where the store is known to be
  empty or corrupted.
- `"merge"` — insert only rows whose primary key doesn't yet exist in
  the target; skip duplicates silently. Use for copying a subset of
  sessions to a second store or for migrating an SQLite store into a
  fresh Postgres instance that already has some sessions.

### 2. Implementations

**`InMemoryStorage`** — pure in-memory deep-copy: `export_storage` copies
`_sessions`, `_versions`, `_graphs`, and (if present) `_embeddings` into
the dict structure above; `import_storage` deserialises them back.

**`SQLiteStorage`** — reads/writes raw `data` TEXT columns and the other
tables directly; uses `_retry_on_locked` for all writes; `clear` mode
wraps everything in a single transaction for atomicity.

**`PostgresStorage`** — async counterpart to `SQLiteStorage`; uses
`asyncpg` and a single `TRUNCATE … RESTART IDENTITY CASCADE` for the
`clear` path.

**`SyncStorageAdapter`** — delegates to the wrapped sync backend via
`asyncio.to_thread`, exactly as every other method does.

### 3. Three MCP tools in cks-mcp

**`export_storage`** — calls `runtime.storage.export_storage()`, writes
the result to a timestamped JSON file in the runtime's data directory
(the same directory `CKS_DB_PATH` lives in, or `$TMPDIR` as fallback),
and returns a summary (session count, version count, graph count) plus
the file path. The full dump is never echoed into the tool response: it
can be hundreds of megabytes.

**`import_storage`** — accepts `file_path` (path to a dump file produced
by `export_storage`) and `mode` (`"merge"` | `"clear"`, default
`"merge"`). Reads the file, calls `runtime.storage.import_storage(data,
mode)`, and returns a summary of what was imported.

**`migrate_storage`** — accepts `target_backend` (`"sqlite"` | `"postgres"`)
and `target_path` (filesystem path for SQLite, DSN for Postgres). Calls
`export_storage` on the current backend, instantiates a fresh target
backend, and calls `import_storage` on it with `mode="merge"`. Does
**not** hot-swap `runtime.storage` — switching to the new backend
requires a manual server restart with an updated `CKS_STORAGE_BACKEND`
environment variable pointing at the new file/DSN. Returns a summary and
the path of the new store.

### 4. What is intentionally out of scope

- **Incremental / WAL-tail backups.** A full JSON dump is simpler,
  backend-agnostic, and sufficient for the use case (infrequent manual
  snapshots of knowledge graphs, not continuous replication). Continuous
  replication is already handled by ADR-008 (Gossip).
- **Compression or encryption.** The dump is plain JSON. Operators who
  need either can pipe through `gzip` / `age` at the shell level.
- **Automatic scheduling.** Backup cadence is left to the operator
  (cron, CI, whatever). The MCP tools provide the primitive; policy is
  out of scope for the runtime itself.
- **Hot-swap of `runtime.storage`.** Changing the active backend at
  runtime introduces race conditions across every subsystem that holds a
  reference to the old storage. A clean restart is safer and simpler.

## Consequences

- Every storage backend that implements `export_storage` /
  `import_storage` becomes a first-class data source and target for the
  backup/migration pipeline without any further changes to the runtime,
  session manager, or execution pipeline.
- The dump format is versioned (`"version": 1`) so a future ADR can
  extend it (e.g. adding the operation log or archive sessions) without
  breaking existing restore scripts.
- `SyncStorageAdapter` gains two new `asyncio.to_thread` delegations,
  consistent with its existing pattern; no other runtime code changes.
- Tests: round-trip export → import must be idempotent (same data on
  both sides); `clear` mode must leave no orphan rows; `merge` mode must
  not duplicate existing rows; graph registry, embeddings, and outbox
  tasks must all survive the round-trip.