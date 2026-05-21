"""MinIO backend — wraps the ``minio`` library.

Default backend on laptop + homelab. Provisioning is done by the
chart's MinIO StatefulSet + bucket-init Job; this backend only does
data-plane I/O.

The wrapping pattern: every minio-py call is one method here. Native
``S3Error`` is translated to :class:`ObjectNotFoundError` when
``code == "NoSuchKey"``; other codes propagate as
:class:`ObjectStorageError`.

PYTHON-ENGINEERING §1: get_object returns an ObjectReader that
deterministically closes the underlying HTTP response on ``__exit__``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import BinaryIO

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error

from audittrace_object_storage.provider import S3ObjectStorageProvider
from audittrace_object_storage.types import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectReader,
    ObjectStorageError,
)

logger = logging.getLogger(__name__)


def _from_minio_object(obj: object) -> ObjectMetadata:
    """Translate a minio-py object (list/stat shape) to ObjectMetadata."""
    return ObjectMetadata(
        object_name=getattr(obj, "object_name", "") or "",
        size=int(getattr(obj, "size", 0) or 0),
        etag=getattr(obj, "etag", None),
        last_modified=getattr(obj, "last_modified", None),
        content_type=getattr(obj, "content_type", None),
    )


def _translate_s3_error(exc: S3Error) -> ObjectStorageError:
    """Map minio S3Error to the package's exception hierarchy.

    Only ``NoSuchKey`` becomes ObjectNotFoundError; everything else is
    wrapped in a generic ObjectStorageError so consumers can catch
    the base class.
    """
    if exc.code == "NoSuchKey":
        return ObjectNotFoundError(str(exc))
    return ObjectStorageError(f"{exc.code}: {exc}")


class MinIOObjectStorageProvider(S3ObjectStorageProvider):
    """Object-storage provider backed by the ``minio`` library."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
    ) -> None:
        self._endpoint = endpoint
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        logger.debug(
            "MinIOObjectStorageProvider initialised",
            extra={"endpoint": endpoint, "secure": secure},
        )

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectMetadata]:
        logger.debug(
            "list_objects",
            extra={"bucket": bucket, "prefix": prefix, "backend": "minio"},
        )
        try:
            for obj in self._client.list_objects(bucket, prefix=prefix, recursive=True):
                yield _from_minio_object(obj)
        except S3Error as exc:
            logger.warning(
                "list_objects failed",
                extra={"bucket": bucket, "prefix": prefix, "code": exc.code},
            )
            raise _translate_s3_error(exc) from exc

    def get_object(self, bucket: str, key: str) -> ObjectReader:
        logger.debug(
            "get_object",
            extra={"bucket": bucket, "key": key, "backend": "minio"},
        )
        try:
            response = self._client.get_object(bucket, key)
        except S3Error as exc:
            logger.warning(
                "get_object failed",
                extra={"bucket": bucket, "key": key, "code": exc.code},
            )
            raise _translate_s3_error(exc) from exc

        def _cleanup() -> None:
            # minio's response needs both close() AND release_conn() to
            # return the underlying urllib3 connection to the pool.
            try:
                response.release_conn()
            except Exception:  # pragma: no cover — defensive
                pass

        return ObjectReader(stream=response, on_close=_cleanup)

    def put_object(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str | None = None,
    ) -> None:
        logger.debug(
            "put_object",
            extra={"bucket": bucket, "key": key, "length": length, "backend": "minio"},
        )
        try:
            self._client.put_object(
                bucket,
                key,
                data,
                length=length,
                content_type=content_type or "application/octet-stream",
            )
        except S3Error as exc:
            logger.warning(
                "put_object failed",
                extra={"bucket": bucket, "key": key, "code": exc.code},
            )
            raise _translate_s3_error(exc) from exc

    def stat_object(self, bucket: str, key: str) -> ObjectMetadata:
        logger.debug(
            "stat_object",
            extra={"bucket": bucket, "key": key, "backend": "minio"},
        )
        try:
            obj = self._client.stat_object(bucket, key)
        except S3Error as exc:
            raise _translate_s3_error(exc) from exc
        return _from_minio_object(obj)

    def remove_object(self, bucket: str, key: str) -> None:
        logger.debug(
            "remove_object",
            extra={"bucket": bucket, "key": key, "backend": "minio"},
        )
        try:
            self._client.remove_object(bucket, key)
        except S3Error as exc:
            # remove is idempotent per S3 semantics — NoSuchKey is a no-op.
            if exc.code == "NoSuchKey":
                logger.debug(
                    "remove_object: NoSuchKey treated as no-op",
                    extra={"bucket": bucket, "key": key},
                )
                return
            raise _translate_s3_error(exc) from exc

    def copy_object(
        self,
        src_bucket: str,
        src_key: str,
        dst_bucket: str,
        dst_key: str,
    ) -> None:
        logger.debug(
            "copy_object",
            extra={
                "src_bucket": src_bucket,
                "src_key": src_key,
                "dst_bucket": dst_bucket,
                "dst_key": dst_key,
                "backend": "minio",
            },
        )
        try:
            self._client.copy_object(
                dst_bucket,
                dst_key,
                CopySource(src_bucket, src_key),
            )
        except S3Error as exc:
            raise _translate_s3_error(exc) from exc

    def health_check(self) -> bool:
        """List buckets — cheap + auth-touching.

        Returns False on any error (network, credentials, DNS).
        """
        logger.debug(
            "health_check", extra={"backend": "minio", "endpoint": self._endpoint}
        )
        try:
            self._client.list_buckets()
            return True
        except Exception as exc:
            logger.warning(
                "health_check failed",
                extra={"backend": "minio", "error": str(exc)},
            )
            return False
