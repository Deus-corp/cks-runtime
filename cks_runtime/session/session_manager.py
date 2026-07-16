"""
Runtime Session Manager.

Owns Runtime Session lifecycle.

Responsibilities:

- create Runtime Sessions;
- retrieve Runtime Sessions;
- enumerate active Runtime Sessions;
- close Runtime Sessions.

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
    Owns Runtime Session lifecycle.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, RuntimeSession] = {}

    def create_session(
        self,
        knowledge_structure: Any,
    ) -> RuntimeSession:
        """
        Create and register a Runtime Session.
        """

        session = RuntimeSession(
            knowledge_structure=knowledge_structure,
        )

        self._sessions[
            session.session_id
        ] = session

        return session

    def get_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Retrieve a Runtime Session.

        Returns None when the Session
        does not exist.
        """

        return self._sessions.get(
            session_id,
        )

    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return active Runtime Sessions.

        A tuple is returned to prevent
        accidental external mutation.
        """

        return tuple(
            self._sessions.values(),
        )

    def close_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Close and unregister a Runtime Session.

        Returns
        -------
        True
            Session existed.

        False
            Session was unknown.
        """

        session = self._sessions.get(
            session_id,
        )

        if session is None:
            return False

        session.close()

        del self._sessions[
            session_id
        ]

        return True