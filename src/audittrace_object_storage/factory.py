"""Factory — config → provider.

Single entry point for consumers. Reads an :class:`ObjectStorageConfig`
(frozen dataclass; consumers populate it from their own Settings) and
returns an :class:`S3ObjectStorageProvider`.

Validation happens here:

- ``backend="minio"`` requires ``endpoint``, ``access_key``, ``secret_key``.
- ``backend="aws"`` requires ``region``; ``use_irsa=False`` additionally
  requires ``access_key_id`` + ``secret_access_key``.
- Any other ``backend`` value is rejected.

Misconfiguration raises :class:`ObjectStorageConfigError` at
construction time — not at first I/O call, which would surface as a
500 in a request handler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from audittrace_object_storage.aws_backend import AWSObjectStorageProvider
from audittrace_object_storage.minio_backend import MinIOObjectStorageProvider
from audittrace_object_storage.provider import S3ObjectStorageProvider
from audittrace_object_storage.types import ObjectStorageConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ObjectStorageConfig:
    """Backend-agnostic configuration for the factory.

    Consumers populate this from their own Settings; this package
    declares no dependency on any specific settings library.
    """

    backend: Literal["minio", "aws"]
    # MinIO fields
    endpoint: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    secure: bool = False
    # AWS fields
    region: str | None = None
    endpoint_url: str | None = None
    use_irsa: bool = True
    access_key_id: str | None = None
    secret_access_key: str | None = None


def _build_minio(config: ObjectStorageConfig) -> MinIOObjectStorageProvider:
    missing = [
        name
        for name, value in (
            ("endpoint", config.endpoint),
            ("access_key", config.access_key),
            ("secret_key", config.secret_key),
        )
        if not value
    ]
    if missing:
        raise ObjectStorageConfigError(
            f"backend='minio' requires {missing}; provide them in ObjectStorageConfig."
        )
    return MinIOObjectStorageProvider(
        endpoint=config.endpoint,  # type: ignore[arg-type]
        access_key=config.access_key,  # type: ignore[arg-type]
        secret_key=config.secret_key,  # type: ignore[arg-type]
        secure=config.secure,
    )


def _build_aws(config: ObjectStorageConfig) -> AWSObjectStorageProvider:
    if not config.region:
        raise ObjectStorageConfigError(
            "backend='aws' requires region; provide it in ObjectStorageConfig."
        )
    return AWSObjectStorageProvider(
        region=config.region,
        use_irsa=config.use_irsa,
        endpoint_url=config.endpoint_url,
        access_key_id=config.access_key_id,
        secret_access_key=config.secret_access_key,
    )


def create_provider(config: ObjectStorageConfig) -> S3ObjectStorageProvider:
    """Dispatch ``config`` to the appropriate backend constructor.

    Raises :class:`ObjectStorageConfigError` on missing-required-fields
    or unknown backend.
    """
    if config.backend == "minio":
        logger.info(
            "creating MinIOObjectStorageProvider",
            extra={
                "backend": "minio",
                "endpoint": config.endpoint,
                "secure": config.secure,
            },
        )
        return _build_minio(config)
    if config.backend == "aws":
        logger.info(
            "creating AWSObjectStorageProvider",
            extra={
                "backend": "aws",
                "region": config.region,
                "use_irsa": config.use_irsa,
                "endpoint_url": config.endpoint_url,
            },
        )
        return _build_aws(config)
    logger.error("unknown backend requested", extra={"backend": config.backend})
    raise ObjectStorageConfigError(
        f"unknown backend {config.backend!r}; expected 'minio' or 'aws'."
    )
