"""
Runtime Session model.

Represents one Runtime execution context.

A Session owns operational Runtime state.

Semantic meaning always belongs
to the attached CKS Core.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from uuid import uuid4


@dataclass
class RuntimeSession:
    """
    Runtime execution context.

    Owns:

    - operational Runtime state;
    - Version history;
    - Diagnostics;
    - currently active Transaction.

    Does not own semantic behaviour.
    """

    knowledge_structure: Any

    session_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    diagnostics: list[Any] = field(
        default_factory=list
    )

    version_history: list[Any] = field(
        default_factory=list
    )

    active_transaction: Any | None = None

    closed: bool = False

    @property
    def is_active(self) -> bool:
        """
        Whether the Session is active.
        """

        return not self.closed

    def close(self) -> None:
        """
        Close the Runtime Session.

        Closing a Session changes only
        its local lifecycle state.

        SessionManager remains responsible
        for removing it from the Runtime.
        """

        self.closed = True