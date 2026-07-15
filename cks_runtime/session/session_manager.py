"""
Runtime Session Manager.

Owns Runtime Session lifecycle.

Responsibilities:

- create Sessions;
- retrieve Sessions;
- enumerate active Sessions;
- close Sessions.

Does not own:

- semantic validation;
- persistence;
- transactions;
- version history.
"""

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
        Create and register a new Runtime Session.
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
        Return a Runtime Session by identifier.

        Returns None if the Session does not exist.
        """

        return self._sessions.get(
            session_id,
        )

    def list_sessions(
        self,
    ) -> list[RuntimeSession]:
        """
        Return all currently active Runtime Sessions.
        """

        return list(
            self._sessions.values(),
        )

    def close_session(
        self,
        session_id: str,
    ) -> None:
        """
        Close and unregister a Runtime Session.

        Unknown Session identifiers are ignored.
        """

        session = self._sessions.get(
            session_id,
        )

        if session is None:
            return

        session.close()

        del self._sessions[
            session_id
        ]