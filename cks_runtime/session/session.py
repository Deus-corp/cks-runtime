"""
Runtime Session model.

A Runtime Session is the fundamental operational
boundary of CKS Runtime.

A Session owns Runtime operational state.

A Session never owns semantic meaning, which remains
the responsibility of CKS Core.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class RuntimeSession:
    """
    Represents an isolated Runtime execution context.

    A Runtime Session owns operational state only.

    Ownership rules:

    - exactly one Runtime owns a Session;
    - exactly one Session owns its Runtime state;
    - semantic meaning belongs exclusively to CKS Core.
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
        Returns True while the Session remains active.
        """

        return not self.closed

    def close(self) -> None:
        """
        Close the Runtime Session.

        Closing a Session ends its operational lifecycle.

        Lifecycle ownership remains the responsibility of
        SessionManager.
        """

        self.closed = True