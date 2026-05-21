"""S3ObjectStorageProvider — the abstract base class.

Derived from the union of consumer call sites in AT-AI + content-control
(2026-05-21 inventory). Seven methods; no `__getattr__` escape hatch;
backends MUST implement every method explicitly.

The ABC surface intentionally excludes minio-specific extras
(``make_bucket``, ``bucket_exists``, ``presigned_*``, ``set_bucket_*``)
— if a consumer needs them, the right move is to provision the bucket
deployment-side (Terraform on AWS, the chart's bucket-init Job on
MinIO) rather than add API surface here.

PYTHON-ENGINEERING §11: keep the ABC small; reading the file should
take seconds, not minutes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import BinaryIO

from audittrace_object_storage.types import ObjectMetadata, ObjectReader


class S3ObjectStorageProvider(ABC):
    """Abstract base for object-storage backends.

    Concrete impls: :class:`MinIOObjectStorageProvider` (wraps the
    ``minio`` library), :class:`AWSObjectStorageProvider` (wraps
    ``boto3.client('s3')`` with IRSA-native auth).
    """

    @abstractmethod
    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectMetadata]:
        """Yield metadata for every object under ``bucket/prefix``.

        Lazy iterator — backends MUST paginate transparently
        (boto3's ``Paginator`` / minio's ``recursive=True`` etc.).
        """

    @abstractmethod
    def get_object(self, bucket: str, key: str) -> ObjectReader:
        """Open an object for reading. Returns a context-managed reader.

        Raises :class:`ObjectNotFoundError` if the object does not exist.
        """

    @abstractmethod
    def put_object(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str | None = None,
    ) -> None:
        """Write an object to ``bucket/key``.

        ``data`` is a file-like object positioned at the start of the
        payload; ``length`` is the byte length (backends require it for
        chunked upload). ``content_type`` is optional.
        """

    @abstractmethod
    def stat_object(self, bucket: str, key: str) -> ObjectMetadata:
        """Return metadata for ``bucket/key`` without downloading the body.

        Raises :class:`ObjectNotFoundError` if the object does not exist.
        """

    @abstractmethod
    def remove_object(self, bucket: str, key: str) -> None:
        """Delete ``bucket/key``. Idempotent — missing object is a no-op.

        Note: idempotence matches the existing minio-py and boto3
        behaviour; consumers that want a "did it exist?" check should
        call :meth:`stat_object` first.
        """

    @abstractmethod
    def copy_object(
        self,
        src_bucket: str,
        src_key: str,
        dst_bucket: str,
        dst_key: str,
    ) -> None:
        """Copy ``src_bucket/src_key`` to ``dst_bucket/dst_key``.

        Used by content-control's ``promote`` (copy-then-delete) flow.
        Raises :class:`ObjectNotFoundError` if the source does not exist.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the backend is reachable and credentials work.

        Used by liveness probes. Implementation is typically a cheap
        ``list_objects`` against a known bucket; backends decide which.
        Never raises — connectivity / auth failures return ``False``.
        """
