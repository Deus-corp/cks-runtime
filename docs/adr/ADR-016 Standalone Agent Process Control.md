# ADR-016: Standalone Agent Process Control — `desired_state` Signal on `cks_agent_liveness`

**Status:** Proposed
**Related:** ADR-014 (Standalone Agent Liveness Heartbeat — owns the
`cks_agent_liveness` schema this ADR extends), ADR-015 (Sweeper
Control — the in-process counterpart of this ADR, same "blocked on a
design doc" origin), cks-mcp ADR-010 (Process Control Tools — thin tool
layer consuming this ADR)

## Context

The four standalone agents (Critic, Enrichment, Fork Resolution,
Pipeline) each run as their own OS process (`run_critic_agent` and its
three siblings). Each already has, independent of anything in ADR-014
or this ADR:

```python
stop = asyncio.Event()

def _handle_signal(*_: Any) -> None:
    stop.set()

for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, _handle_signal)
...
liveness = LivenessReporter(runtime, "critic", settings.liveness_interval)
await liveness.start()
try:
    while not stop.is_set():
        processed = await run_once(runtime, settings, liveness)
        if processed == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_interval)
            except TimeoutError:
                pass
finally:
    await liveness.stop()
    await runtime.aclose()
```

So a graceful-shutdown path already exists and is already exercised —
today it's only reachable via `SIGTERM`/`SIGINT` from whatever started
the process (a supervisor, `systemd`, or a person running it in a
terminal). The original plan's framing was "a `stop` for a standalone
agent can't be a bare RPC call into another OS process without some
signal mechanism (SIGTERM via supervisor, or a flag in the heartbeat
table the process itself polls between iterations)." Given `cks-mcp`
has no supervisor integration and no assumption about what process
manager (if any) launched these four processes, the signal-via-
supervisor option isn't something this codebase can implement — it
would require knowing and depending on a specific supervisor's API.
The polled-flag option, by contrast, composes directly with the `stop`
event that already exists and is already wired into the exact
shutdown path this needs.

## Decision

### 1. Extend `cks_agent_liveness` with one nullable column

```sql
ALTER TABLE cks_agent_liveness ADD COLUMN desired_state TEXT;
-- NULL or 'running' = no stop requested (default); 'stop_requested' = pending
```

Same `PRAGMA table_info`-driven add-column migration convention as
every other column added to an existing table in
`sqlite_storage.py`/`postgres_storage.py` post-creation (e.g.
`cks_outbox_tasks.claimed_at`) — no new migration mechanism. `NULL`
rather than a `'running'` default so existing rows (written by
processes running the pre-ADR-016 `LivenessReporter`) read back as "no
stop requested" without a backfill.

`AgentLivenessRecord` gains `desired_state: str | None = None`.
`upsert_agent_liveness` (ADR-014, already process-owned, one writer per
`instance_id`) is not the right place to *set* `desired_state` — that
column is written by a different actor (an MCP tool call, from a
different process) than the rest of the row (the heartbeat writer). A
second, narrower storage method:

```python
async def request_agent_stop(self, instance_id: str) -> bool:
    """Set desired_state='stop_requested' for this instance_id.
    Returns False if no row with this instance_id exists (already
    gone, or never existed) -- same not-an-error convention as
    touch_outbox_task's lease-renewal return value."""
```

This is a single-column `UPDATE ... WHERE instance_id = ?`, not an
upsert — it must never create a row (only `upsert_agent_liveness`,
owned by the process itself, does that), and it does not touch
`last_heartbeat_at` — a stop request is not itself evidence the process
is alive.

### 2. `LivenessReporter`'s existing tick reads its own row back

`LivenessReporter._tick_forever` (ADR-014) currently only writes. It
gains a read-back of its own `instance_id` row on the same cadence
(`liveness_interval`, already the loop's existing period — no new
timer):

```python
async def _tick_forever(self, stop_event: asyncio.Event) -> None:
    while True:
        await asyncio.sleep(self._interval)
        with contextlib.suppress(Exception):
            await self._write()
            record = await self._runtime.storage.get_agent_liveness(self._instance_id)
            if record is not None and record.desired_state == "stop_requested":
                stop_event.set()
                return
```

`LivenessReporter` is constructed with a reference to the same `stop`
`asyncio.Event()` each `run_*_agent` function already creates for
signal handling (`LivenessReporter(runtime, "critic",
settings.liveness_interval, stop_event=stop)`), rather than inventing
a second, parallel shutdown channel. Setting `stop_event` here makes a
remote stop request and `SIGTERM` converge on the exact same code path
in `run_critic_agent`'s main loop (`while not stop.is_set(): ...`) and
the exact same `finally: await liveness.stop(); await
runtime.aclose()` cleanup — no new shutdown logic to write or test per
agent, only the two lines above, once, in the shared
`LivenessReporter`.

`get_agent_liveness(instance_id)` (new, targeted single-row read) is
added alongside `list_agent_liveness` rather than reusing the list call
here — a live process shouldn't scan the entire shared table every
`liveness_interval` just to check its own one row, especially as
history accumulates across restarts (ADR-014 §3 explicitly keeps old
rows around).

### 3. Immediate `stopped` status on graceful exit, not a TTL wait

ADR-014 §3's `alive = (now - last_heartbeat_at) <= 3 * liveness_interval_s`
would otherwise leave a cleanly-exited process reading as `alive` for
up to `3 * liveness_interval_s` (90s at the 30s default) after it's
already gone — needlessly slow feedback for something that, unlike a
crash, we know happened. `LivenessReporter.stop()` (already called from
every `run_*_agent`'s `finally` block, both on `SIGTERM` and on a
`desired_state`-triggered exit) gets one addition: a final write that
backdates `last_heartbeat_at` far enough into the past to fail the TTL
check immediately —

```python
async def stop(self) -> None:
    if self._tick_task is not None:
        self._tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._tick_task
        self._tick_task = None
    with contextlib.suppress(Exception):
        self._current_task_id = None
        self._current_task_type = None
        await self._write(last_heartbeat_at_override=_EPOCH_ISO)
```

This reuses the existing §3 formula in `list_processes`/`process_status`
completely unchanged — no new `status` value, no schema addition beyond
§1's single column — a graceful exit is simply indistinguishable, by
design, from "hasn't reported in since the beginning of time," which
the TTL rule already classifies as `stopped`. A crash (no chance to run
`finally`) still degrades the old way: `stopped` appears once the TTL
window elapses, exactly as ADR-014 already specified — this ADR only
makes the *voluntary* exit case faster, it doesn't change crash
detection at all.

### 4. No `start_process`, no `pause`/`resume`

Unlike sweepers (ADR-015 §5, same reasoning applies here too), a
standalone agent process cannot be started by an MCP tool call at
all — `cks-mcp` runs in a different OS process with no privilege or
mechanism to spawn a new one, and inventing one would mean owning
process-supervision responsibility (restart policy, backoff, log
capture) that belongs to whatever already launches these processes
(`systemd`, a container orchestrator, a plain shell script) — a much
larger and different problem than agent visibility. `desired_state`
therefore only ever takes the value `'stop_requested'`; there is no
`'start_requested'`. Restarting a stopped agent remains an operational
action outside `cks-mcp`'s scope, same as starting it was in the first
place.

Pause/resume: a standalone agent's unit of work is one claimed outbox
task (`run_resolver_with_heartbeat`) — there is no meaningful "pause
mid-task" (the in-flight lease-renewal heartbeat would need to keep
running regardless, or the lease expires and a different worker claims
it, defeating the purpose of pausing this one). "Pause" is scoped down
to exactly what `desired_state='stop_requested'` already gives: finish
the current task (if any), then exit before claiming another — this
*is* a graceful pause/drain, it just doesn't have a `resume` half,
because resuming means starting the process again, which §4's first
paragraph already rules out as this ADR's concern.

## Consequences

- **One nullable column on an existing table, one new narrow storage
  method (`request_agent_stop`), one new single-row read
  (`get_agent_liveness`)** — both backends, same migration convention
  as everywhere else.
- **`LivenessReporter` gains a `stop_event` parameter and a read-back
  in its existing tick** — one shared class, so this is a single change
  that covers all four agents, not four separate ones (unlike ADR-014's
  §2, which genuinely did need four per-process settings changes for
  the *interval*, since that's config; the stop-check logic itself has
  no per-agent variation).
- **Worst-case latency from `request_agent_stop` to the process
  actually exiting:** up to one `liveness_interval` (default 30s,
  waiting for the next tick) **plus** however long the current
  `resolver()` call takes to finish, if one is in flight (the same
  bound `run_resolver_with_heartbeat`'s lease-renewal loop already
  tolerates) **plus** up to one `poll_interval` if the process is
  mid-`wait_for(stop.wait(), timeout=poll_interval)` — bounded and
  small relative to how these agents are used, not real-time.
- **A hung process (event loop itself blocked, e.g. a misbehaving sync
  call) won't observe `desired_state` either** — same failure mode as
  it not sending heartbeats at all; ADR-014's TTL already reports such
  a process `stopped` after `3 * liveness_interval_s` regardless of
  whether a stop was ever requested, so this isn't a new gap this ADR
  introduces.
- **cks-mcp follow-up (out of scope here):** `request_process_stop(process_kind)`
  — see companion ADR-010 for the tool contract (looks up the current
  alive instance the same way `process_status` does, then calls
  `request_agent_stop` on that `instance_id`; returns `{"found": false}`
  for a `process_kind` with no alive instance, same convention as
  `process_status`).
- **cks-studio follow-up (out of scope here):** a "Stop" button per
  process card in the Agent Panel's v2 section — separate, later UI
  work.
