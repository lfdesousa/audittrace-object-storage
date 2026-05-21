"""AWS S3 backend — wraps ``boto3.client('s3')``.

IRSA-native by default: boto3's standard credential chain resolves
``AWS_ROLE_ARN`` + ``AWS_WEB_IDENTITY_TOKEN_FILE`` (env vars injected
by the EKS pod-identity mutating webhook on pods whose ServiceAccount
carries the ``eks.amazonaws.com/role-arn`` annotation) via
:class:`botocore.credentials.AssumeRoleWithWebIdentityProvider`. STS
credentials cache + auto-refresh — no explicit plumbing here.

Loud-fail assertion: if ``use_irsa=True`` and the env var is missing,
construction raises :class:`ObjectStorageConfigError` rather than
silently falling through to anonymous access (which would surface
later as a 403 from S3).

Reference: botocore ``AssumeRoleWithWebIdentityProvider`` in
``botocore/credentials.py``; in botocore since 1.12.0 (2018-09).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any, BinaryIO

import boto3
from botocore.exceptions import ClientError

from audittrace_object_storage.provider import S3ObjectStorageProvider
from audittrace_object_storage.types import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectReader,
    ObjectStorageConfigError,
    ObjectStorageError,
)

logger = logging.getLogger(__name__)


def _is_not_found(exc: ClientError) -> bool:
    """Return True iff the ClientError encodes a 404-equivalent.

    S3 returns several distinct codes for 'not found' depending on the
    operation: ``NoSuchKey`` for GET/DELETE, ``404`` for HEAD, and
    sometimes ``NoSuchBucket`` when the bucket itself is missing. The
    HTTP status code is the most reliable single signal.
    """
    code = exc.response.get("Error", {}).get("Code")
    http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("NoSuchKey", "NoSuchBucket", "404") or http_status == 404


def _translate_client_error(exc: ClientError) -> ObjectStorageError:
    """Map boto3 ClientError to the package's exception hierarchy."""
    if _is_not_found(exc):
        return ObjectNotFoundError(str(exc))
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    return ObjectStorageError(f"{code}: {exc}")


class AWSObjectStorageProvider(S3ObjectStorageProvider):
    """Object-storage provider backed by ``boto3.client('s3')``.

    Two auth modes:

    - **IRSA (default, ``use_irsa=True``)** — boto3's default credential
      chain reads ``AWS_ROLE_ARN`` + ``AWS_WEB_IDENTITY_TOKEN_FILE``
      (set by the EKS pod-identity webhook). No keys plumbed here.
    - **Static keys (``use_irsa=False``)** — caller provides
      ``access_key_id`` + ``secret_access_key``. Used for non-AWS
      S3-compatible backends (OVH OS, MinIO via S3 endpoint).
    """

    def __init__(
        self,
        region: str,
        use_irsa: bool = True,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        if use_irsa and not os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
            logger.error(
                "IRSA enabled but AWS_WEB_IDENTITY_TOKEN_FILE missing",
                extra={"region": region, "use_irsa": True},
            )
            raise ObjectStorageConfigError(
                "IRSA enabled but AWS_WEB_IDENTITY_TOKEN_FILE not set — "
                "check ServiceAccount eks.amazonaws.com/role-arn annotation "
                "and that the pod-identity webhook is running."
            )
        self._region = region
        self._use_irsa = use_irsa
        client_kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if not use_irsa:
            if not access_key_id or not secret_access_key:
                raise ObjectStorageConfigError(
                    "use_irsa=False requires both access_key_id and secret_access_key."
                )
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key
        self._client = boto3.client("s3", **client_kwargs)
        logger.debug(
            "AWSObjectStorageProvider initialised",
            extra={
                "region": region,
                "use_irsa": use_irsa,
                "endpoint_url": endpoint_url,
            },
        )

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[ObjectMetadata]:
        logger.debug(
            "list_objects",
            extra={"bucket": bucket, "prefix": prefix, "backend": "aws"},
        )
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    yield ObjectMetadata(
                        object_name=obj["Key"],
                        size=int(obj.get("Size", 0)),
                        etag=obj.get("ETag"),
                        last_modified=obj.get("LastModified"),
                    )
        except ClientError as exc:
            logger.warning(
                "list_objects failed",
                extra={
                    "bucket": bucket,
                    "prefix": prefix,
                    "code": exc.response.get("Error", {}).get("Code"),
                },
            )
            raise _translate_client_error(exc) from exc

    def get_object(self, bucket: str, key: str) -> ObjectReader:
        logger.debug(
            "get_object",
            extra={"bucket": bucket, "key": key, "backend": "aws"},
        )
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            logger.warning(
                "get_object failed",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "code": exc.response.get("Error", {}).get("Code"),
                },
            )
            raise _translate_client_error(exc) from exc
        body = response["Body"]  # botocore StreamingBody
        return ObjectReader(stream=body)

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
            extra={"bucket": bucket, "key": key, "length": length, "backend": "aws"},
        )
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Body": data,
            "ContentLength": length,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            self._client.put_object(**kwargs)
        except ClientError as exc:
            logger.warning(
                "put_object failed",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "code": exc.response.get("Error", {}).get("Code"),
                },
            )
            raise _translate_client_error(exc) from exc

    def stat_object(self, bucket: str, key: str) -> ObjectMetadata:
        logger.debug(
            "stat_object",
            extra={"bucket": bucket, "key": key, "backend": "aws"},
        )
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            raise _translate_client_error(exc) from exc
        return ObjectMetadata(
            object_name=key,
            size=int(response.get("ContentLength", 0)),
            etag=response.get("ETag"),
            last_modified=response.get("LastModified"),
            content_type=response.get("ContentType"),
        )

    def remove_object(self, bucket: str, key: str) -> None:
        # S3 DELETE is idempotent — missing key returns 204, not 404.
        logger.debug(
            "remove_object",
            extra={"bucket": bucket, "key": key, "backend": "aws"},
        )
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            raise _translate_client_error(exc) from exc

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
                "backend": "aws",
            },
        )
        try:
            self._client.copy_object(
                Bucket=dst_bucket,
                Key=dst_key,
                CopySource={"Bucket": src_bucket, "Key": src_key},
            )
        except ClientError as exc:
            raise _translate_client_error(exc) from exc

    def health_check(self) -> bool:
        """Touch S3 to confirm credentials + connectivity. False on any error."""
        logger.debug("health_check", extra={"backend": "aws", "region": self._region})
        try:
            self._client.list_buckets()
            return True
        except Exception as exc:
            logger.warning(
                "health_check failed",
                extra={"backend": "aws", "error": str(exc)},
            )
            return False
