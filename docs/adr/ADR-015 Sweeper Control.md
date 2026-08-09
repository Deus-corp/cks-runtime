# ADR-015: Sweeper Control — Persistent Enable/Disable State and Concurrent-Caller Safety

**Status:** Proposed
**Related:** ADR-014 (Standalone Agent Liveness Heartbeat — same "Control
Panel blocked on a design doc" origin, see that ADR's Consequences),
cks-mcp ADR-009 (Sweeper Control Tools — thin tool layer consuming this
ADR), `AGENT_VISIBILITY.md` (v1 read-only panel, already shipped)

## Context

The seven in-process reasoning sweepers (`ContradictionSweeper`,
`InferenceStalenessSweeper`, `GraphHealthSweeper`, etc., all mixing in
`SweeperStatusMixin`) each expose `async def start(self)` / `async def
stop(self)`. Today exactly one caller ever invokes them: `Runtime.create()`,
once, at server startup, gated only by whether the sweeper is
config-enabled (`self._xxx_sweeper` is `None` vs. a live instance — see
`runtime.py`'s constructor). There is no tool, no code path, and no
stored state today that stops or restarts a sweeper after that.

Two problems block adding `start_agent`/`stop_agent` tools on top of the
existing `start()`/`stop()` methods as-is:

1. **No persistence of "stopped".** `start()`/`stop()` only affect the
   in-memory `Runtime` instance's `asyncio.Task`. If someone stops
   `contradiction` via a hypothetical tool call and the MCP server
   process restarts five minutes later, `Runtime.create()` has no way
   to know that decision was ever made — the sweeper comes back
   running, because config enablement (static, from env/settings) is
   the only signal it currently checks.
2. **`start()`/`stop()` were not written for concurrent external
   callers.** Each has a classic check-then-act shape:
   ```python
   async def start(self) -> None:
       if self._running:
           return
       ...
       self._running = True
       self._task = asyncio.create_task(self._run(), ...)
   ```
   Nothing stops two concurrent `await sweeper.start()` calls (e.g. two
   MCP clients both calling a `start_agent` tool for the same
   `agent_id` at nearly the same time) from both observing
   `self._running is False` before either sets it — Python's single
   event loop doesn't prevent this, because the race is across
   `await` points, not across OS threads. The result would be two
   `asyncio.Task`s both running `_run()` against the same sweeper
   instance, double-processing every sweep pass. `stop()` has the
   mirror problem: two concurrent stops would both try to cancel
   `self._task`, and the second one operates on whatever `self._task`
   already got set to `None` by the first — currently harmless only
   because nothing calls `stop()` concurrently today.

## Decision

### 1. New table: `cks_sweeper_control`

One row **only for sweepers that have ever had their default overridden**
— absence of a row means "config default applies," matching the
minimal-write style already used elsewhere (e.g. `cks_agent_liveness`
only has rows for processes that have actually started; `list_agents`
doesn't pre-populate rows for config-disabled sweepers either).

```sql
CREATE TABLE IF NOT EXISTS cks_sweeper_control (
    agent_id       TEXT PRIMARY KEY,   -- e.g. 'contradiction', 'graph_health'
    desired_running BOOLEAN NOT NULL,  -- explicit manual override
    updated_at     TEXT NOT NULL       -- ISO 8601, last time this row changed
);
```

Same migration convention as every other table (`CREATE TABLE IF NOT
EXISTS`, no separate migration runner), one new `Storage`/
`StorageAdapter` method pair (ADR-004), no-op default on the base class
the same way `upsert_agent_liveness` was added in ADR-014:

```python
async def set_sweeper_desired_running(self, agent_id: str, desired_running: bool) -> None: ...
async def get_sweeper_desired_running(self, agent_id: str) -> bool | None: ...  # None = no override row
```

### 2. `Runtime.create()` consults the override, once, at startup

For each sweeper that is config-enabled (the existing `if
config.xxx_sweeper_enabled:` gate stays exactly as-is — this table
never *enables* a config-disabled sweeper, it only lets an
already-enabled one stay stopped), `Runtime.create()` additionally
checks `get_sweeper_desired_running(agent_id)` before calling
`.start()`:

```python
if runtime._contradiction_sweeper is not None:
    override = await runtime.storage.get_sweeper_desired_running("contradiction")
    if override is not False:
        await runtime._contradiction_sweeper.start()
```

`override is not False` (rather than `override is True`) so the common
case — no row, `None` — starts the sweeper, same as today's behavior
with this ADR entirely absent.

### 3. Live propagation without a push mechanism: check on every tick

A manual stop needs to affect a sweeper that's *already running* in
this same process (the tool call's whole point), not just future
startups. `stop_agent` therefore also calls `sweeper.stop()` directly
against `runtime._sweepers[agent_id]` in addition to writing the
override row — same pattern as any other MCP tool reaching into
`Runtime` state.

The harder case is a gossip-replicated multi-node deployment (ADR-008,
cks-runtime): each node runs its own `Runtime`, its own in-process
sweeper instances, against shared storage. A `stop_agent` call landing
on node A's MCP server has no RPC path to node B's in-process
`asyncio.Task` — there is no cross-node control channel for sweepers
today, and this ADR does not add one. Instead, each sweeper's existing
`_run()` loop gets one extra check reusing its own tick cadence, the
same "poll, don't push" philosophy as ADR-014 §3's TTL-on-read:

```python
async def _run(self) -> None:
    while self._running:
        try:
            await self.sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(...)
        await asyncio.sleep(self._interval_seconds)
        if await self._storage.get_sweeper_desired_running(self._agent_id) is False:
            self._running = False
            break
```

This bounds cross-node stop propagation to at most one
`_interval_seconds` (already minutes, per sweeper) — acceptable given
sweepers are background maintenance passes, not latency-sensitive
control paths. A node that starts *after* a stop was recorded picks it
up immediately via §2; a node already running picks it up within one
sweep interval via this check. No new signal/pub-sub infrastructure
needed. `start_agent` is **not** given the same cross-node reach — a
node whose sweeper was never running (config-disabled, or crashed) has
no running `_run()` loop to observe the override in; restarting a
sweeper on a specific node remains that node's own `start_agent` call
or its next process restart (§2). This asymmetry is deliberate and
documented in the tool description (cks-mcp ADR-009), not silently
assumed.

### 4. Concurrency: one `asyncio.Lock` per sweeper, held across the check-then-act

```python
self._control_lock = asyncio.Lock()  # added to SweeperStatusMixin's _init_sweeper_status

async def start(self) -> None:
    async with self._control_lock:
        if self._running:
            return
        ...
        self._running = True
        self._task = asyncio.create_task(self._run(), ...)

async def stop(self) -> None:
    async with self._control_lock:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
```

Two concurrent `start_agent` tool calls for the same `agent_id` now
serialize: the second acquires the lock after the first has already
set `self._running = True` and returns immediately as a no-op, same
observable result as today's single-caller behavior. Same for two
concurrent stops. A `start` racing a `stop` resolves to whichever
acquired the lock first — last-write-wins at the granularity of a
whole start/stop call, not a partial one; no invariant of
`SweeperStatusMixin` or the sweep loop can be violated mid-way. This
lock is the entire concurrency fix — no distributed lock, no storage-
level compare-and-swap needed, because it only has to protect
in-process state (`self._running`, `self._task`); the storage row
written by `stop_agent` is a separate, idempotent upsert that doesn't
need the same lock (two concurrent writes of the same
`desired_running` value are commutative).

### 5. No separate "pause"/"resume"

Each sweep pass (`sweep_once()`) is one bounded, already-atomic-enough
unit of work (typically a handful of `enqueue_task` calls) — there is
no in-flight, resumable state a "pause" would preserve that "stop" +
later "start" doesn't already give you for free (the next `start()`
just runs `sweep_once()` again from scratch on its normal schedule).
Introducing pause/resume as concepts distinct from stop/start would add
a state (`paused`) with no different runtime behavior than `stopped`,
just to satisfy the plan's original naming — not worth the extra
surface. cks-mcp ADR-009's tools are named `start_agent`/`stop_agent`
only; "pause" and "resume" from the original plan are implemented as
the same two tools.

## Consequences

- **New table + two storage methods, both backends** — same shape as
  every prior storage addition (ADR-004, ADR-014).
- **One `asyncio.Lock` added to `SweeperStatusMixin`**, `start()`/
  `stop()` in all seven sweepers wrapped with it — mechanical,
  identical diff shape in each file.
- **One extra `await` per sweep-loop iteration** (the desired-running
  check) — negligible against `_interval_seconds` measured in minutes.
- **Cross-node stop propagates within one sweep interval; cross-node
  start does not propagate at all** — documented asymmetry, not a gap
  discovered later. Revisit only if multi-node sweeper control becomes
  an actual operational need (no evidence of that yet).
- **cks-mcp follow-up (out of scope here):** `start_agent(agent_id)` /
  `stop_agent(agent_id)` tools — see companion ADR-009 for the tool
  contract, error shape for an unknown `agent_id`, and how these
  compose with the existing read-only `list_agents`/`agent_status`.
- **cks-studio follow-up (out of scope here):** Agent Panel (v1, still
  read-only per its own design note) gains actual buttons for the
  first time — a separate, later UI change, not decided by this ADR.
