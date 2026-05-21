"""audittrace-object-storage — shared object-storage abstraction.

Public API for AuditTrace-AI + audittrace-content-control consumers.
See ``README.md`` for usage; see ADR-006 in the audittrace-deployment
repo for the design rationale.
"""

from __future__ import annotations

from audittrace_object_storage.aws_backend import AWSObjectStorageProvider
from audittrace_object_storage.factory import (
    ObjectStorageConfig,
    create_provider,
)
from audittrace_object_storage.minio_backend import MinIOObjectStorageProvider
from audittrace_object_storage.provider import S3ObjectStorageProvider
from audittrace_object_storage.types import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectReader,
    ObjectStorageConfigError,
    ObjectStorageError,
    QuarantinedObjectAccessError,
)

__all__ = [
    "AWSObjectStorageProvider",
    "MinIOObjectStorageProvider",
    "ObjectMetadata",
    "ObjectNotFoundError",
    "ObjectReader",
    "ObjectStorageConfig",
    "ObjectStorageConfigError",
    "ObjectStorageError",
    "QuarantinedObjectAccessError",
    "S3ObjectStorageProvider",
    "create_provider",
]

__version__ = "0.1.0"
