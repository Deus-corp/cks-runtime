"""
Runtime Operation Registry.

Owns Runtime Operation discovery.

The Registry stores Runtime Operation types and is responsible
only for registration and lookup.

Execution belongs to OperationExecutor.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cks_runtime.execution.operation_executor import Operation


class OperationRegistry:
    """
    Registry of Runtime Operation types.

    Operations are registered by unique operation_id.
    """

    def __init__(self) -> None:
        self._operations: dict[str, type[Operation]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        operation_type: type[Operation],
    ) -> None:
        """
        Register an Operation type.
        """

        operation_id = getattr(operation_type, "operation_id", None)

        if not operation_id:
            raise ValueError(
                f"{operation_type.__name__} does not define operation_id."
            )

        if operation_id in self._operations:
            raise ValueError(
                f"Operation '{operation_id}' is already registered."
            )

        self._operations[operation_id] = operation_type

    def register_many(
        self,
        operations: Iterable[type[Operation]],
    ) -> None:
        """
        Register multiple Operation types.
        """

        for operation in operations:
            self.register(operation)

    def unregister(
        self,
        operation_id: str,
    ) -> bool:
        """
        Remove an Operation type.

        Returns
        -------
        True
            Operation existed.

        False
            Unknown operation.
        """

        return self._operations.pop(operation_id, None) is not None

    def clear(self) -> None:
        """
        Remove every registered Operation.
        """

        self._operations.clear()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(
        self,
        operation_id: str,
    ) -> type[Operation] | None:
        """
        Retrieve an Operation type.
        """

        return self._operations.get(operation_id)

    def create(
        self,
        operation_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Operation:
        """
        Instantiate an Operation.
        """

        operation_type = self.get(operation_id)

        if operation_type is None:
            raise KeyError(
                f"Unknown operation '{operation_id}'."
            )

        return operation_type(
            *args,
            **kwargs,
        )

    def has(
        self,
        operation_id: str,
    ) -> bool:
        """
        Whether an Operation exists.
        """

        return operation_id in self._operations

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def ids(self) -> tuple[str, ...]:
        """
        Return registered operation identifiers.
        """

        return tuple(self._operations.keys())

    def list_all(
        self,
    ) -> tuple[type[Operation], ...]:
        """
        Return registered Operation types.
        """

        return tuple(self._operations.values())

    # ------------------------------------------------------------------
    # Python protocol helpers
    # ------------------------------------------------------------------

    def __contains__(
        self,
        operation_id: str,
    ) -> bool:
        return operation_id in self._operations

    def __len__(self) -> int:
        return len(self._operations)

    def __iter__(self):
        return iter(self._operations.values())