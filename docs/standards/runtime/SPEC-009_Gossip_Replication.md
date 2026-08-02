# SPEC-009

# Gossip Replication

**Status:** Draft

**Standard:** CKS Runtime

**Category:** Runtime Distributed Replication Specification

---

# 1. Purpose

This specification defines the canonical Gossip Replication model for
independent Runtime replicas.

Gossip Replication allows two or more Runtime processes, each with
its own local Storage, to reconcile Session state without a shared
database and without a central coordinator.

Gossip Replication reuses the existing Runtime merge mechanism. It
introduces no new conflict semantics of its own.

Gossip Replication is an optional Runtime capability. A Runtime
implementation may omit it entirely and remain conformant to SPEC-008
Runtime Conformance.

---

# 2. Scope

This specification defines:

* the Replica Identity model;
* the `GossipEnvelope` wire format;
* envelope authenticity (signing and verification);
* replay and order protection;
* the semantics of applying remote Session state;
* the `GossipTransport` contract that any transport implementation
  (HTTP, gRPC, WebSocket, or otherwise) must satisfy;
* the Peer Discovery contract;
* the Gossip orchestration model (`GossipService`).

This specification intentionally omits a specific wire technology.
The reference implementation (HTTP long-poll) is documented in
Section 13 as an example, not a requirement.

---

# 3. Gossip Philosophy

Gossip Replication preserves Runtime operational state across
independent Storage instances.

Gossip Replication does not define new knowledge semantics.

Gossip Replication does not validate knowledge.

Gossip Replication does not resolve conflicts a synchronous merge
could not already resolve.

Gossip transmits Session snapshots between replicas that already
trust each other. Authenticating a malicious peer's edits is out of
scope (see Section 15).

---

# 4. Replica Identity

Two distinct identities exist in the Runtime distributed model:

* **`node_id`** — minted per Session (`session.metadata["node_id"]`),
  scoped to one `RuntimeSession` instance's local commits, for
  `VersionVector` disambiguation between concurrent branches from the
  same replica (SPEC-007 Version History).
* **`replica_id`** — one durable identity per Runtime
  process/deployment, stable across restarts.

A Storage backend that supports Gossip Replication provides
`get_or_create_replica_id()`, generating and persisting the identity
on first call (a single-row `cks_runtime_identity` table in the
reference SQLite/PostgreSQL backends). A backend that does not
support Gossip Replication returns `None`; a `None` replica id means
that Storage instance is not a distinguishable gossip peer, and
callers shall not attempt to gossip through it.

`replica_id` is supplied to `GossipAdapter` by the caller at
construction time. This specification does not require an adapter to
fetch it automatically from Storage — a deployment wiring
`GossipAdapter` together with a durable Storage backend is
responsible for sourcing `replica_id` via
`storage.get_or_create_replica_id()` itself if durability across
restarts is required. Passing an arbitrary or freshly generated
string works but forfeits durability: a peer restarting under a new
`replica_id` looks, to `GossipFilter`, like a distinct sender with no
prior sequence history.

`node_id` shall never be reused as `replica_id`. Overloading it would
break the moment a replica process restarts and mints new Sessions,
silently discarding the durable identity gossip depends on.

---

# 5. The GossipEnvelope Wire Format

A `GossipEnvelope` is a signed snapshot of one `RuntimeSession`, as
sent by one replica to another.

An envelope carries the following fields:

| Field | Meaning |
|---|---|
| `version` | Envelope format version (currently `"1.0"`). |
| `sender_replica_id` | The `replica_id` of the sending replica. |
| `session_id` | The Session this snapshot belongs to. |
| `knowledge_structure_json` | The canonical serialization (`cks.serialize`) of the Session's Knowledge Structure. |
| `metadata` | The Session's metadata, including its `VersionVector` clocks. |
| `parent_session_id` | Branch lineage pointer, or `null`. |
| `parent_version_id` | Fork-point lineage pointer, or `null`. |
| `nonce` | A fresh random value, unique per envelope, for replay protection. |
| `seq_no` | A monotonically increasing integer, unique per sender across every session it gossips. |
| `timestamp_ms` | Epoch milliseconds at signing time. |
| `signature` | HMAC-SHA256 over the fields above, keyed by the shared gossip secret. |

An envelope is immutable once built. A received envelope is verified,
filtered, and consumed; nothing in this model requires mutating one
in place.

Reconstructing a `RuntimeSession` from an envelope (`to_session()`)
populates only `session_id`, `metadata`, `knowledge_structure`, and
the two lineage pointers. `version_history` is left empty — an
envelope carries a snapshot, not a replay log.

A conformant implementation shall reject a malformed envelope
(missing or mistyped required fields) immediately and explicitly,
rather than substituting defaults. A wire-format defect is not
something a caller should ever merge state from.

---

# 6. Envelope Authenticity

Every envelope is signed with HMAC-SHA256 over a canonical,
unambiguous encoding of its fields, keyed by a secret shared between
cooperating replicas.

Signature verification is constant-time (`hmac.compare_digest` or
equivalent). A conformant implementation shall not use a
variable-time byte comparison for verification.

The gossip signing secret is a **separate trust domain** from any
other provenance or authenticity mechanism a deployment may also use
(for example, `cks-mcp`'s own provenance signing). A valid
`GossipEnvelope` signature proves only "this message really came from
replica X, and was not altered in transit" — a different claim from
"this fact was checked against this source." The two secrets shall
never be shared: doing so would let a compromised gossip peer forge
the other trust domain's claims, or vice versa.

Signature verification shall be checked before an envelope is handed
to replay/order filtering (Section 7) or applied to local state
(Section 8). A forged envelope shall never occupy a slot in a replay
cache or advance a sequence counter, forged or not.

---

# 7. Replay and Order Protection

Every implementation of the server side of gossip exchange shall
validate incoming envelope metadata before applying it, checking, in
this order:

1. **Timestamp** — the envelope's `timestamp_ms` shall fall within a
   configured clock-skew tolerance of the receiver's own clock.
   Optionally, a message older than a configured TTL relative to its
   own timestamp shall also be rejected.
2. **Nonce** — the envelope's `nonce` shall not have been seen before
   from this `sender_replica_id`. A bounded cache (oldest-evicted) is
   sufficient; an implementation is not required to remember every
   nonce ever seen.
3. **Sequence number** — the envelope's `seq_no` shall be strictly
   greater than the highest `seq_no` previously accepted from this
   `sender_replica_id`.

Any one of these checks failing rejects the envelope. Rejection is
not an authenticity failure (that is Section 6's concern) — a
correctly signed envelope may still be rejected as a replay or as
out-of-order.

`seq_no` is a single counter a sending replica shares across every
Session it gossips to every peer, not one counter per
(peer, session) pair. A caller running gossip for several Sessions
must therefore draw every `seq_no` it sends from one shared source.

---

# 8. Applying Remote State

`apply_remote_session` reconciles one replica's snapshot of a Session
into local state through the following decision sequence:

1. **Unknown locally (bootstrap).** If the receiving replica tracks
   no local Session under this `session_id` at all, the remote
   snapshot is adopted as a new local Session — registered and
   persisted exactly as a Session restored from local Storage at
   startup would be, and committed as a real local Version. The
   remote's already-committed `VersionVector` is preserved unchanged,
   so a later gossip round involving a third replica that already saw
   the original's commits is recognized as already-known history, not
   a fresh conflict. The bootstrapped Session's `node_id` is always
   freshly minted; it shall never be copied from the remote's
   `node_id` (Section 4).
2. **Local already dominates.** If the local `VersionVector` already
   dominates the remote's, the remote snapshot carries nothing new.
   No local state changes.
3. **Remote dominates (fast-forward).** If the remote's
   `VersionVector` dominates the local one, the remote's
   `KnowledgeStructure` is adopted directly and committed as a new
   local Version, without invoking the merge probe.
4. **Neither dominates, content identical.** If neither vector
   dominates the other but the two sides' `KnowledgeStructure`s are
   already structurally equivalent (an O(1) root-hash comparison),
   the Session is treated as converged. This covers two replicas that
   started tracking the same `session_id` but have not yet diverged —
   attempting a merge probe here would fail on "no common ancestor"
   despite nothing having actually diverged.
5. **Neither dominates, content differs (three-way merge).** The
   existing field-disjoint merge path is invoked as a probe (no
   persisted side effects on failure). A clean probe is committed as
   a new local Version. A conflicting probe is **escalated, not
   raised**: a `GossipConflictDetected` event, carrying the conflicting
   `session_id` and identities, is published via the Runtime
   `EventBus` instead of raising `RuntimeMergeConflictError`
   synchronously — a background gossip cycle has no caller waiting on
   the call the way a synchronous merge invocation does. `session_id`
   (added v1.31.2) lets a subscriber tell which of possibly several
   concurrently-gossiping sessions conflicted, since the event has no
   other way to disambiguate one conflict from another. A subscriber
   resolves the conflict later through the ordinary synchronous merge
   path.

`apply_remote_session` returns `True` for outcomes 1–4 and for a
successful merge in outcome 5, and `False` when a merge conflict was
escalated.

A conformant implementation shall not silently guess at a merge base
when none can be determined. No-common-ancestor divergence is
escalated exactly as a genuine field-level conflict is.

**Concurrency.** `apply_remote_session` calls that target the same
`session_id` are serialized against each other — a second call for a
`session_id` already being reconciled waits for the first to finish
rather than racing it through the begin/commit sequence above. Calls
for *different* `session_id`s are never blocked on each other. A
third-party transport (Section 9) that fans inbound requests out
across multiple concurrent handlers — as `GossipServer` does for
inbound HTTP — relies on this: without it, two requests for one
Session arriving close together could both observe no active
transaction yet and both attempt to open one, and the second would
fail with a transaction-conflict error instead of reconciling
normally.

---

# 9. The GossipTransport Contract

`GossipTransport` is the network boundary a Runtime implementation's
distributed replication may depend on. It is the extension point
third-party transports (gRPC, WebSocket, message queues, or
otherwise) implement to participate in Gossip Replication.

A conformant `GossipTransport` implementation exposes exactly one
operation:

```text
exchange(peer, envelope) -> envelope | None
```

* **Input:** a peer address (implementation-defined format —
  a URL for the HTTP reference transport, but this specification does
  not require that shape) and a signed `GossipEnvelope` for one
  Session.
* **Success, peer has a reply:** returns the peer's own current,
  signed `GossipEnvelope` snapshot of the same `session_id`.
* **Success, peer has nothing to reply with:** returns `None`. This
  is a **normal, expected outcome**, not an error condition — a
  transport shall not raise for this case.
* **Failure:** raises a single, transport-wide error type when the
  peer could not be reached at all, or replied with something this
  transport implementation could not interpret as an envelope. A
  conformant implementation shall not let a lower-level,
  transport-specific exception (a raw HTTP client error, a gRPC
  status exception, a socket error) escape uncaught — it shall be
  wrapped, chaining the original exception for inspectability.

A `GossipTransport` implementation owns *only* moving signed bytes to
a peer and a response back. It shall not itself decide which peer to
contact, how often, or what to do with a conflict — that policy
belongs to `GossipService` (Section 12) and `PeerScheduler`, layered
above the transport, not inside it.

## 9.1 Server-Side Obligations

A transport's server side (the reference implementation's
`GossipServer`) additionally:

* verifies the incoming envelope's signature (Section 6) before doing
  anything else with it;
* runs the incoming envelope through replay/order filtering
  (Section 7);
* applies the envelope through `apply_remote_session` (Section 8),
  never through any other merge path;
* replies with its own current signed envelope for the same
  `session_id` on success;
* escalates a merge conflict via the `EventBus` (Section 8, outcome
  5) rather than surfacing it as a transport-level error to the
  sender — the sender is not positioned to resolve a conflict on the
  receiver's behalf.

A conformant server implementation shall reject an unverifiable or
replayed envelope before it reaches `apply_remote_session` at all.

## 9.2 Reference HTTP Semantics (Non-Normative)

The reference HTTP transport maps the above onto standard status
codes: `200` (envelope reply), `404` (peer reachable, no reply to
give — see Section 8, though in practice `apply_remote_session`'s
bootstrap behavior means a well-formed, correctly signed, non-replayed
request to the reference server always produces a `200` reply, since
an unknown `session_id` is adopted rather than rejected), `400`
(malformed request body or envelope), `401` (signature verification
failed), `409` (rejected by the replay filter). A third-party
transport is free to map these outcomes onto whatever its own
protocol's native error signaling looks like; only the two-outcome
contract above (`envelope | None`, or a wrapped error) is normative.

---

# 10. Peer Discovery

Peer Discovery is a **separate exchange** from Session gossip
(Section 5–9). Folding peer lists into the signed envelope format
would require every envelope consumer to carry fields unrelated to a
snapshot's authenticity.

A conformant `PeerDiscovery` implementation exposes exactly one
operation:

```text
fetch_peers(peer) -> list[str]
```

Returning the peer addresses `peer` reports knowing about. A
reachable peer is expected, though not required by this contract
alone, to include its own externally-reachable address in that list —
this is what lets a replica that was only ever dialed into (never
listed in anyone's static configuration) still be discovered by a
third party.

A `PeerDiscoveryError` (or the implementation's equivalent) is raised
when `peer` could not be reached, or replied with something that
could not be interpreted as a peer list at all.

Discovered addresses are merged into a replica's known-peer set
additively: an address already known is a no-op; `self_address`,
when configured, is never added — a replica shall never schedule a
gossip round against itself.

Peer Discovery is deliberately minimal: no membership pruning,
expiry, or reachability pre-check before an address is added. An
unreachable discovered address costs at most one wasted gossip
attempt (handled by the backoff behavior in Section 11), never a
permanent bad entry requiring manual removal.

## 10.1 Reference `/gossip/peers` Route (Non-Normative)

The reference HTTP transport serves known peers unauthenticated and
unsigned, unlike the Session-gossip route — a peer address is not
sensitive the way a Session snapshot is, and requiring a signed
request would mean a brand-new replica (which by definition has not
exchanged a Session yet) could never discover anyone.

---

# 11. Peer Scheduling

Peer Scheduling decides which peer to gossip with next, and for how
long a peer that just failed is skipped. This specification does not
mandate a specific selection or backoff algorithm; it requires only
that:

* peer selection favors peers with a better recent success rate over
  peers with a worse one, without ever fully starving an unproven
  peer of a first attempt;
* a peer that fails is temporarily excluded from selection for a
  bounded, increasing period (exponential backoff, capped), so one
  unreachable peer cannot dominate every gossip round;
* a subsequent success clears any active backoff for that peer
  immediately.

---

# 12. Gossip Orchestration (`GossipService`)

`GossipService` ties `GossipAdapter` (merge semantics, Section 8), a
`GossipTransport` (Section 9), Peer Scheduling (Section 11), and
Peer Discovery (Section 10, optional) into a periodic anti-entropy
loop.

## 12.1 One Round

A single round:

1. chooses one eligible peer (Section 11);
2. gossips every currently tracked Session with that peer, in order,
   stopping at the first transport failure for this round (a peer
   that is genuinely down fails identically for every Session — there
   is nothing to learn from retrying it several times in one round);
3. records success or failure against the chosen peer for scheduling
   purposes;
4. if every tracked Session exchanged cleanly (or there were none to
   begin with) and Peer Discovery is configured, asks that same peer
   which other peers it knows about, and merges any new addresses in.

A tracked Session this replica has no local copy of is silently
skipped — not an error — matching Section 8's premise that there must
be local state to gossip from.

A Peer Discovery failure during step 4 shall not fail the round: a
peer that does not support discovery must not stop Session gossip
from working.

## 12.2 Startup Discovery

Before entering its periodic loop, `GossipService` shall, when Peer
Discovery is configured, query every peer it currently knows about
(its static seed list, or whatever `PeerScheduler` was constructed
with) for their own known-peer lists, merging every answer in. This
runs once, synchronously, before the first periodic round begins.

This closes the gap a purely reactive discovery model (Section 12.1,
step 4 alone) leaves open: a service configured with nothing but a
handful of static seed addresses would otherwise have to wait for its
first *successful* Session-gossip round — which itself only talks to
one scheduler-chosen peer — before its peer membership could grow at
all. Querying every known seed at startup lets membership grow from
the full seed set immediately, with each later round's own reactive
discovery continuing to grow it epidemically from there.

Startup discovery queries the peer set as it stood at the moment
`start()` was called. It does not transitively follow addresses
discovered during that same pass — a peer learned about this way is
itself queried on a later discovery pass, not within the same one.
This matches how epidemic membership protocols converge over several
rounds rather than in one traversal.

Startup discovery is best-effort per peer: one seed failing to answer
shall not prevent the others from being asked.

---

# 13. Reference Implementation (HTTP Long-Poll)

The reference `GossipTransport` implementation is HTTP long-poll:
`POST {peer}/gossip/{session_id}` with the signed envelope as the
JSON request body, expecting the peer's own envelope (or an empty
`404`) in reply. The reference `PeerDiscovery` implementation is
`GET {peer}/gossip/peers`, expecting `{"peers": [...]}`.

This is intentionally minimal — chosen for being easy to reason about
and to test against real sockets, not as a recommendation of HTTP
over gRPC, libp2p, or any other transport. Section 9 defines what a
conforming alternative transport must uphold; nothing above this
section assumes HTTP specifically.

---

# 14. Relationship to ADR-008

This specification stabilizes and supersedes, as the normative
contract, the original operation-log-replay design ADR-008 proposed.
The implemented design gossips whole `RuntimeSession` snapshots
(Section 5) and reconciles them through the existing three-way merge
path (Section 8), rather than replaying individual
`RuntimeFieldOperation` rows between replicas — that original design
could not work as specified, since an `"add_object"`/`"add_relation"`
operation-log entry carries no payload, only a marker that an
identity appeared, and so cannot reconstruct a genuinely new object
on its own. `fetch_operations_since` remains a transport-layer
accelerant for `MergeOperation`'s own field-level fast path; it is
not, and was never redesigned to be, the payload Gossip Replication
itself is built from.

ADR-008's persistent `replica_id` (Section 4) and conflict escalation
via `EventBus` (Section 8, outcome 5) were implemented as originally
decided.

---

# 15. Non-Goals

* **Not reopening full CRDT field types.** Gossip reuses the same
  field-disjoint fast path SPEC-007's Version model already
  established; it inherits that decision rather than revisiting it.
* **Not solving Byzantine or malicious peers.** This model assumes
  cooperating replicas within one deployment. Envelope authenticity
  (Section 6) prevents an unrelated third party from injecting or
  replaying traffic between replicas that already trust each other —
  it does not defend against a trusted peer that misbehaves.
* **Not solving operation-log retention/compaction.** Orthogonal to
  Gossip Replication; already an open question for SPEC-007.
* **Not mandating a production transport.** `GossipTransport` is a
  contract (Section 9); the reference implementation (Section 13) is
  one conforming transport among any number of others a deployment
  may choose instead.

---

# 16. Design Principles

## Snapshot Reuse, Not New Semantics

Gossip transmits the same Session state the Runtime already models
and reconciles it through the same merge mechanism SPEC-007 already
defines. It introduces no parallel conflict model.

---

## Transport Independence

Runtime distributed replication depends on the `GossipTransport`
contract (Section 9), never on a specific wire technology. Any
implementation satisfying that contract may be substituted.

---

## Authenticity Before Application

An envelope's signature and replay/order state are checked before it
is ever handed to `apply_remote_session`. Authenticity and
mergeability are separate, ordered concerns.

---

## Escalation, Not Silent Failure

A conflict a background gossip cycle cannot resolve on its own is
surfaced through the `EventBus`, never silently discarded and never
raised to a caller that isn't waiting for it.

---

## Additive Discovery

Peer Discovery only ever grows a replica's known-peer set. It never
prunes, and a bad discovered address costs at most one failed
attempt.

---

# 17. Conformance

A Runtime implementation that claims Gossip Replication support
conforms to this specification when it:

* sources `replica_id` as a durable identity, distinct from
  per-Session `node_id`;
* implements the `GossipEnvelope` fields and signing scheme of
  Section 5–6, or an equivalent envelope satisfying the same
  authenticity and replay-protection guarantees;
* checks signature verification before replay/order filtering, and
  both before applying remote state;
* reconciles remote Session state exclusively through
  `apply_remote_session`'s decision sequence (Section 8), including
  escalating unresolved conflicts via the `EventBus` rather than
  raising them synchronously;
* implements `GossipTransport`'s two-outcome contract (Section 9) —
  `envelope | None` on success, a single wrapped error type on
  failure — for any transport it provides;
* implements Peer Discovery, if offered, as an additive, separate
  exchange from Session gossip (Section 10);
* runs Peer Discovery once against every known peer at startup, when
  configured, in addition to any reactive per-round discovery
  (Section 12.2).

Gossip Replication conformance is independent of SPEC-008 Runtime
Conformance: a Runtime implementation may conform to SPEC-008 without
implementing Gossip Replication at all.

---

# 18. Summary

Gossip Replication defines the canonical model for reconciling
independent Runtime replicas without a shared Storage backend or
central coordinator.

It stabilizes the `GossipEnvelope` wire format, the authenticity and
replay-protection guarantees every transport must uphold, the
decision sequence `apply_remote_session` follows, and the
`GossipTransport`/`PeerDiscovery` contracts a third-party transport
(HTTP, gRPC, WebSocket, or otherwise) implements to participate.

Gossip Replication reuses the Runtime's existing merge mechanism. It
provides distributed reconciliation, not a new conflict model.