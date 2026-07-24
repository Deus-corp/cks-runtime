import cks
from cks_runtime.runtime import Runtime
from cks_runtime.operations.operation_types import ValidateOperation
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime_plugins.cks_core import CksCoreAdapter


def test_version_created_adds_outbox_task():
    """When a version is created, the outbox should contain a new task."""
    storage = SQLiteStorage(":memory:")
    runtime = Runtime(core=CksCoreAdapter(), storage=storage)

    ks = cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )
    session = runtime.create_session(ks)
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("v1", knowledge_structure=ks))
    runtime.commit_transaction(tx)

    # Check outbox table
    rows = storage._conn.execute(
        "SELECT * FROM cks_outbox_tasks"
    ).fetchall()
    assert len(rows) == 1
    # Indices: 1=task_type, 2=session_id, 4=status
    assert rows[0][1] == "projection"
    assert rows[0][2] == session.session_id
    assert rows[0][4] == "PENDING"