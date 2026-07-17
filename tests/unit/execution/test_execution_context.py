"""Unit tests for ExecutionPipeline."""

import pytest

from unittest.mock import MagicMock
from cks_runtime.pipeline.execution_pipeline import ExecutionPipeline
from cks_runtime.transaction.transaction import RuntimeTransaction
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion

def create_transaction(with_core=False):
    runtime = MagicMock()
    runtime.versions = MagicMock()
    runtime.versions.create.return_value = RuntimeVersion(
        session_id="test", transaction_id="test", knowledge_structure={}, metadata={}
    )
    runtime.transactions = MagicMock()
    runtime.transactions.rollback = MagicMock()
    runtime.transactions.commit = MagicMock()
    runtime.transactions.abort = MagicMock()
    runtime.core_bridge = MagicMock()
    runtime.core_bridge.validate.return_value = MagicMock(
        valid=not with_core,  # если with_core=True, то валидация невалидна для проверки ошибки
        diagnostics=[],
        has_diagnostics=False,
    )
    runtime.storage = MagicMock()
    runtime.storage.save_session = MagicMock()
    runtime.storage.save_version = MagicMock()
    runtime.diagnostics = MagicMock()
    session = RuntimeSession(knowledge_structure={})
    transaction = RuntimeTransaction(session=session)
    return runtime, transaction

def test_pipeline_commit():
    runtime, transaction = create_transaction()
    pipeline = ExecutionPipeline(runtime)
    version = pipeline.commit(transaction)
    assert isinstance(version, RuntimeVersion)
    runtime.versions.create.assert_called_once()
    runtime.storage.save_version.assert_called_once()

def test_pipeline_commit_with_core():
    runtime, transaction = create_transaction(with_core=True)
    pipeline = ExecutionPipeline(runtime)
    with pytest.raises(RuntimeError, match="semantic validation failed"):
        pipeline.commit(transaction)

def test_pipeline_rollback():
    runtime, transaction = create_transaction()
    pipeline = ExecutionPipeline(runtime)
    pipeline.rollback(transaction)
    runtime.transactions.rollback.assert_called_once_with(transaction)
    runtime.storage.save_session.assert_called_once()

def test_pipeline_abort():
    runtime, transaction = create_transaction()
    pipeline = ExecutionPipeline(runtime)
    pipeline.abort(transaction)
    runtime.transactions.abort.assert_called_once_with(transaction)
    runtime.storage.save_session.assert_called_once()

def test_pipeline_collects_no_diagnostics_for_valid_core():
    runtime, transaction = create_transaction()  # valid=True по умолчанию
    pipeline = ExecutionPipeline(runtime)
    pipeline.commit(transaction)
    # Диагностики не должны добавляться, если их нет
    runtime.diagnostics.extend.assert_not_called()