"""
Runtime Session.

Represents a single Runtime execution context.

A RuntimeSession owns operational Runtime state.

Semantic meaning always belongs to the attached CKS Core.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class RuntimeSession:
    """
    Runtime execution context.

    Owns:

    - operational Runtime state;
    - version history;
    - diagnostics;
    - active Runtime transaction.

    A RuntimeSession never owns semantic behaviour.
    """

    knowledge_structure: Any

    session_id: str = field(
        default_factory=lambda: str(uuid4()),
    )

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )

    diagnostics: list[Any] = field(
        default_factory=list,
    )

    version_history: list[Any] = field(
        default_factory=list,
    )

    active_transaction: Any | None = None

    closed: bool = False

    #
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    #

    @property
    def is_active(self) -> bool:
        """
        Whether this RuntimeSession is active.
        """

        return not self.closed

    @property
    def has_active_transaction(self) -> bool:
        """
        Whether a transaction is currently attached.
        """

        return self.active_transaction is not None

    @property
    def version_count(self) -> int:
        """
        Number of committed Runtime versions.
        """

        return len(self.version_history)
    
    @property
    def has_versions(self) -> bool:
        """
        Whether the session has any committed versions.
        """
        return bool(self.version_history)

    def close(self) -> None:
        """
        Close this RuntimeSession.

        Closing changes only the local lifecycle state.

        SessionManager remains responsible for removing
        the session from Runtime.
        """

        self.closed = True

    #
    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    #

    def add_diagnostic(
        self,
        diagnostic: Any,
    ) -> None:
        """
        Attach a Runtime diagnostic to the session.
        """

        self.diagnostics.append(
            diagnostic,
        )

    #
    # ------------------------------------------------------------------
    # Version history
    # ------------------------------------------------------------------
    #

    def add_version(
        self,
        version: Any,
    ) -> None:
        """
        Append a committed Runtime version.
        """

        self.version_history.append(
            version,
        )

    #
    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------
    #

    def attach_transaction(
        self,
        transaction: Any,
    ) -> None:
        """
        Attach an active Runtime transaction.
        """

        self.active_transaction = transaction

    def detach_transaction(self) -> None:
        """
        Remove the active Runtime transaction.
        """

        self.active_transaction = None


@property
def has_versions(self) -> bool:
    return bool(self.version_history)