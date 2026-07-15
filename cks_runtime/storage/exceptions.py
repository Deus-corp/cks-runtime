"""
Storage exceptions.

SPEC-006 Storage
"""

from __future__ import annotations


class StorageError(Exception):
    """
    Base Runtime Storage exception.
    """


class SessionNotFound(StorageError):
    """
    Requested Runtime Session does not exist.
    """

    def __init__(self, session_id: str):
        super().__init__(
            f"Runtime Session '{session_id}' was not found."
        )
        self.session_id = session_id


class VersionNotFound(StorageError):
    """
    Requested Runtime Version does not exist.
    """

    def __init__(self, version_id: str):
        super().__init__(
            f"Runtime Version '{version_id}' was not found."
        )
        self.version_id = version_id