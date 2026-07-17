"""
Runtime Validation Result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class RuntimeValidationResult:
    """Immutable Runtime validation result."""

    valid: bool
    diagnostics: tuple[Any, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # diagnostics всегда должен быть кортежем
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        # metadata оставляем как есть, он уже Mapping (может быть dict)
        # больше не оборачиваем в MappingProxyType

    @property
    def has_diagnostics(self) -> bool:
        return bool(self.diagnostics)

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def __bool__(self) -> bool:
        return self.valid

    @classmethod
    def success(cls, *, metadata: Mapping[str, Any] | None = None) -> RuntimeValidationResult:
        return cls(valid=True, metadata=metadata if metadata is not None else {})

    @classmethod
    def failure(cls, diagnostics: tuple[Any, ...], *, metadata: Mapping[str, Any] | None = None) -> RuntimeValidationResult:
        return cls(valid=False, diagnostics=diagnostics, metadata=metadata if metadata is not None else {})