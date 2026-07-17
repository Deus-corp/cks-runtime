"""
CKS Runtime – CKS Core Integration.

This package provides the concrete implementation of the
CoreInterface using the real `cks-core` library.

It bridges the abstract Runtime model with the canonical
CKS Core Python API, enabling full transactional execution
with semantic validation.
"""

from .adapter import CksCoreAdapter

__all__ = (
    "CksCoreAdapter",
)