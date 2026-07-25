"""
Runtime Storage Interface.

Defines the persistence boundary for Runtime operational state.

Storage implementations persist Runtime objects but never
own Runtime behaviour or semantic interpretation.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass

from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion


class ConcurrentModificationError(RuntimeError):
    """
    Raised by ``save_session`` when ``expected_version_id`` was given
    and no longer matches the persisted ``latest_version_id`` -- i.e.
    another writer (another process, or another concurrent commit on
    this one) has already advanced this session since the caller last
    read it. Callers should reload the session and retry, not treat
    this as a generic storage failure.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(
            f"Session '{session_id}' was modified concurrently; "
            "reload and retry."
        )


class RuntimeStorage(ABC):
    """
    Abstract Runtime storage.

    Storage is responsible only for persistence.

    Storage never:

    - owns RuntimeSessions;
    - owns RuntimeTransactions;
    - owns RuntimeVersions;
    - performs semantic validation.
    """

    #
    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
        """
        Persist a RuntimeSession.

        expected_version_id
            Optional compare-and-swap guard. When given, the write is
            rejected with ``ConcurrentModificationError`` unless the
            backend's currently persisted ``latest_version_id`` for
            this session equals this value (``None`` matching "no
            version persisted yet"). Callers that are not committing
            a new version against a specific prior version (initial
            creation, rollback, abort) may omit it to write
            unconditionally, as before.
        """

    @abstractmethod
    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Restore a RuntimeSession.

        Returns None when the session does not exist.
        """

    @abstractmethod
    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Whether a RuntimeSession exists.
        """

    @abstractmethod
    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return every persisted RuntimeSession.
        """

    #
    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a RuntimeVersion.
        """

    @abstractmethod
    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Restore a RuntimeVersion.

        Returns None when the version does not exist.
        """

    @abstractmethod
    def has_version(
        self,
        version_id: str,
    ) -> bool:
        """
        Whether a RuntimeVersion exists.
        """

    @abstractmethod
    def list_versions(
        self,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return every persisted RuntimeVersion.
        """

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def clear(self) -> None:
        """
        Remove every persisted object.

        Primarily intended for testing and
        reference implementations.
        """


    def enqueue_outbox_task(
        self,
        session_id: str,
        previous_version_id: str | None,
        new_version_id: str,
    ) -> None:
        """
        Write a task to the projection outbox (if supported).
        Default implementation does nothing — storage backends that
        support projections override this.
        """


    def enqueue_task(
        self,
        task_type: str,
        session_id: str,
        payload: str,
    ) -> None:
        """
        Write a generic task to the outbox (if supported).
        Default implementation does nothing -- storage backends that
        support the task bus override this. Callers must not assume
        this method's mere presence means the task will actually be
        persisted; check ``supports_outbox`` first.
        """

    def dequeue_next_outbox_task(self) -> OutboxTask | None:
        """
        Return the next pending outbox task, or None if the backend
        doesn't support outbox operations.
        """
        return None

    def complete_outbox_task(self, task_id: int) -> None:
        """Mark an outbox task as completed. No-op by default."""
        pass

    def fail_outbox_task(self, task_id: int, retry_count: int, error: str, next_retry_at: str) -> None:
        """Mark a task as failed with exponential backoff. No-op by default."""
        pass

    def save_object_embeddings(self, object_id: str, session_id: str, embedding: bytes) -> None:
        """Save an embedding for an object. No-op by default."""
        pass

    def delete_object_embeddings(self, object_id: str, session_id: str) -> None:
        """Delete embeddings for an object. No-op by default."""
        pass

    @property
    def supports_outbox(self) -> bool:
        """Whether this storage backend supports outbox operations."""
        return False


@dataclass(frozen=True, slots=True)
class OutboxTask:
    """A task read from the outbox table."""
    task_id: int
    task_type: str
    session_id: str
    payload: str
    retry_count: int = 0