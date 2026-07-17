"""
Runtime Session Manager.

Owns RuntimeSession lifecycle.

Responsibilities:

- create RuntimeSessions;
- retrieve RuntimeSessions;
- enumerate active RuntimeSessions;
- close RuntimeSessions.

Does not own:

- semantic validation;
- persistence;
- transactions;
- version history.
"""

from __future__ import annotations

from typing import Any

from .session import RuntimeSession


class SessionManager:
    """
    Owns RuntimeSession lifecycle.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}

    #
    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    #

    def create_session(
        self,
        knowledge_structure: Any,
    ) -> RuntimeSession:
        """
        Create and register a RuntimeSession.
        """

        session = RuntimeSession(
            knowledge_structure=knowledge_structure,
        )

        self._sessions[session.session_id] = session

        return session

    #
    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    #

    def get_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Retrieve a RuntimeSession.

        Returns
        -------
        RuntimeSession
            Existing session.

        None
            Session does not exist.
        """

        return self._sessions.get(session_id)

    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Whether a RuntimeSession exists.
        """

        return session_id in self._sessions

    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return all active RuntimeSessions.

        An immutable tuple is returned to prevent
        accidental external mutation.
        """

        return tuple(self._sessions.values())

    @property
    def session_count(self) -> int:
        """
        Number of active RuntimeSessions.
        """

        return len(self._sessions)

    #
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    #

    def close_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Close and unregister a RuntimeSession.

        Returns
        -------
        bool
            True if the session existed,
            otherwise False.
        """

        session = self._sessions.get(session_id)

        if session is None:
            return False

        session.close()

        del self._sessions[session_id]

        return True

    def clear(self) -> None:
        """
        Remove every RuntimeSession.

        Primarily intended for testing.
        """

        for session in self._sessions.values():
            if session.is_active:
                session.close()

        self._sessions.clear()