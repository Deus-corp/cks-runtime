"""
Runtime Session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class RuntimeSession:
    knowledge_structure: Any
    session_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Any] = field(default_factory=list)
    version_history: list[Any] = field(default_factory=list)
    active_transaction: Any | None = None
    closed: bool = False

    @property
    def is_active(self) -> bool:
        return not self.closed

    @property
    def has_active_transaction(self) -> bool:
        return self.active_transaction is not None

    @property
    def version_count(self) -> int:
        return len(self.version_history)

    @property
    def has_versions(self) -> bool:
        return bool(self.version_history)

    def close(self) -> None:
        self.closed = True

    def add_diagnostic(self, diagnostic: Any) -> None:
        self.diagnostics.append(diagnostic)

    def add_version(self, version: Any) -> None:
        self.version_history.append(version)

    def attach_transaction(self, transaction: Any) -> None:
        self.active_transaction = transaction

    def detach_transaction(self) -> None:
        self.active_transaction = None