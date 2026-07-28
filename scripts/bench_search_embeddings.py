"""
Benchmark: old (pure-Python loop) vs new (numpy-vectorized) search_embeddings.

Extracts the OLD implementation verbatim from git history (old_sqlite_storage.py,
identical logic to pre-patch HEAD) and times it against the same SQLite database
used by the NEW patched SQLiteStorage, for a range of collection sizes.
"""
import array
import sqlite3
import time

import numpy as np

DIM = 384  # typical MiniLM/HF sentence-embedding dimension


def old_search_embeddings(conn, query_embedding: bytes, session_id: str, top_k: int = 5):
    """Verbatim logic of the pre-patch implementation (no session_id index used)."""
    rows = conn.execute(
        "SELECT object_id, embedding FROM cks_object_embeddings WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    if not rows:
        return []

    q = array.array("f")
    q.frombytes(query_embedding)

    def score(emb):
        v = array.array("f")
        v.frombytes(emb)
        if len(v) != len(q):
            return None
        similarity = sum(a * b for a, b in zip(v, q))
        return max(0.0, min(1.0, similarity))

    scored = sorted(
        (
            (oid, s)
            for oid, emb in ((r[0], r[1]) for r in rows)
            if (s := score(emb)) is not None
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return scored[:top_k]


def new_search_embeddings(conn, query_embedding: bytes, session_id: str, top_k: int = 5):
    rows = conn.execute(
        "SELECT object_id, embedding FROM cks_object_embeddings WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    if not rows:
        return []

    query_vec = np.frombuffer(query_embedding, dtype=np.float32)
    object_ids, vectors = [], []
    for object_id, emb in rows:
        v = np.frombuffer(emb, dtype=np.float32)
        if v.shape[0] != query_vec.shape[0]:
            continue
        object_ids.append(object_id)
        vectors.append(v)
    if not vectors:
        return []

    matrix = np.stack(vectors)
    similarities = np.clip(matrix @ query_vec, 0.0, 1.0)
    order = np.argsort(-similarities, kind="stable")[:top_k]
    return [(object_ids[i], float(similarities[i])) for i in order]


def make_db(n_objects: int, with_index: bool, n_sessions: int = 1) -> sqlite3.Connection:
    """
    n_sessions=1 isolates the numpy-vectorization effect (every row is a
    genuine candidate either way, so the index can't help -- SQLite must
    return all of them regardless).
    n_sessions>1 spreads n_objects across many sessions and always
    searches session "s1": this isolates the *index's own* contribution,
    since without it SQLite must scan every other session's rows too
    just to find the ones matching session_id = 's1'.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE cks_object_embeddings (
            object_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, embedding BLOB NOT NULL
        )"""
    )
    if with_index:
        conn.execute(
            "CREATE INDEX idx_object_embeddings_session ON cks_object_embeddings(session_id)"
        )
    rng = np.random.default_rng(42)
    rows = []
    for i in range(n_objects):
        vec = rng.normal(size=DIM).astype(np.float32)
        vec /= np.linalg.norm(vec)
        session_id = f"s{i % n_sessions}" if n_sessions > 1 else "s1"
        rows.append((f"obj-{i}", session_id, vec.tobytes()))
    conn.executemany(
        "INSERT INTO cks_object_embeddings VALUES (?, ?, ?)", rows
    )
    conn.commit()
    return conn


def query_vector() -> bytes:
    rng = np.random.default_rng(7)
    v = rng.normal(size=DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tobytes()


def time_it(fn, *args, repeats=20):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(*args)
        best = min(best, time.perf_counter() - t0)
    return best


if __name__ == "__main__":
    q = query_vector()

    print("=== Scenario A: single session (isolates numpy vectorization only) ===")
    print(f"{'n_objects':>10} | {'old (no idx)':>14} | {'new (idx+numpy)':>16} | speedup")
    print("-" * 62)
    for n in (100, 1_000, 5_000, 20_000):
        conn_old = make_db(n, with_index=False)
        conn_new = make_db(n, with_index=True)

        t_old = time_it(old_search_embeddings, conn_old, q, "s1")
        t_new = time_it(new_search_embeddings, conn_new, q, "s1")

        print(f"{n:>10} | {t_old*1000:>11.3f} ms | {t_new*1000:>13.3f} ms | {t_old/t_new:>6.1f}x")

    print()
    print("=== Scenario B: 200 sessions sharing the table, searching just one ===")
    print("    (isolates the index's own contribution to the SQL scan)")
    print(f"{'total_rows':>10} | {'no index':>14} | {'with index':>14} | speedup")
    print("-" * 58)
    for n in (2_000, 20_000, 100_000):
        conn_noidx = make_db(n, with_index=False, n_sessions=200)
        conn_idx = make_db(n, with_index=True, n_sessions=200)

        sql = "SELECT object_id, embedding FROM cks_object_embeddings WHERE session_id = ?"
        t_noidx = time_it(lambda c, s=sql: c.execute(s, ("s1",)).fetchall(), conn_noidx)
        t_idx = time_it(lambda c, s=sql: c.execute(s, ("s1",)).fetchall(), conn_idx)

        print(f"{n:>10} | {t_noidx*1000:>11.3f} ms | {t_idx*1000:>11.3f} ms | {t_noidx/t_idx:>6.1f}x")

        if n == 20_000:
            plan_noidx = conn_noidx.execute(f"EXPLAIN QUERY PLAN {sql}", ("s1",)).fetchall()
            plan_idx = conn_idx.execute(f"EXPLAIN QUERY PLAN {sql}", ("s1",)).fetchall()
            print(f"    no index  -> {plan_noidx[0][3]}")
            print(f"    with index -> {plan_idx[0][3]}")