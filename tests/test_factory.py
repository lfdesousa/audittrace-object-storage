"""Tests for factory.py — config validation + dispatch."""

from __future__ import annotations

import logging
import os
from unittest import mock

import pytest

from audittrace_object_storage.aws_backend import AWSObjectStorageProvider
from audittrace_object_storage.factory import (
    ObjectStorageConfig,
    create_provider,
)
from audittrace_object_storage.minio_backend import MinIOObjectStorageProvider
from audittrace_object_storage.types import ObjectStorageConfigError


class TestObjectStorageConfig:
    def test_defaults(self) -> None:
        config = ObjectStorageConfig(backend="minio")
        assert config.secure is False
        assert config.use_irsa is True
        assert config.endpoint is None
        assert config.region is None

    def test_is_frozen(self) -> None:
        import dataclasses as _dc

        config = ObjectStorageConfig(backend="minio")
        with pytest.raises(_dc.FrozenInstanceError):
            config.backend = "aws"  # type: ignore[misc]


class TestCreateProviderMinIO:
    def test_minio_happy_path(self) -> None:
        config = ObjectStorageConfig(
            backend="minio",
            endpoint="localhost:9000",
            access_key="a",
            secret_key="b",
            secure=False,
        )
        provider = create_provider(config)
        assert isinstance(provider, MinIOObjectStorageProvider)

    @pytest.mark.parametrize(
        "missing_field",
        ["endpoint", "access_key", "secret_key"],
    )
    def test_minio_missing_required_raises(self, missing_field: str) -> None:
        kwargs = dict(
            backend="minio",
            endpoint="localhost:9000",
            access_key="a",
            secret_key="b",
        )
        kwargs[missing_field] = None  # type: ignore[assignment]
        config = ObjectStorageConfig(**kwargs)  # type: ignore[arg-type]
        with pytest.raises(ObjectStorageConfigError, match=missing_field):
            create_provider(config)

    def test_minio_all_three_missing_lists_all(self) -> None:
        config = ObjectStorageConfig(backend="minio")
        with pytest.raises(ObjectStorageConfigError) as excinfo:
            create_provider(config)
        msg = str(excinfo.value)
        assert "endpoint" in msg
        assert "access_key" in msg
        assert "secret_key" in msg


class TestCreateProviderAWS:
    def test_aws_irsa_happy_path(self, tmp_path) -> None:
        # boto3 resolves AssumeRoleWithWebIdentityProvider eagerly at
        # client creation. We need: a real token file on disk + both
        # AWS_WEB_IDENTITY_TOKEN_FILE and AWS_ROLE_ARN env vars set, OR
        # we mock boto3.client. Real token file is the cleaner fixture
        # because it also exercises our IRSA-env-presence assertion.
        token_file = tmp_path / "token"
        token_file.write_text("fake-irsa-token")
        config = ObjectStorageConfig(
            backend="aws",
            region="eu-central-2",
            use_irsa=True,
        )
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AWS_WEB_IDENTITY_TOKEN_FILE": str(token_file),
                    "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/test-irsa",
                },
            ),
            mock.patch("audittrace_object_storage.aws_backend.boto3.client"),
        ):
            provider = create_provider(config)
        assert isinstance(provider, AWSObjectStorageProvider)

    def test_aws_static_keys_happy_path(self) -> None:
        config = ObjectStorageConfig(
            backend="aws",
            region="eu-central-2",
            use_irsa=False,
            access_key_id="AKIA-test",
            secret_access_key="secret",
        )
        provider = create_provider(config)
        assert isinstance(provider, AWSObjectStorageProvider)

    def test_aws_missing_region_raises(self) -> None:
        config = ObjectStorageConfig(
            backend="aws", use_irsa=False, access_key_id="x", secret_access_key="y"
        )
        with pytest.raises(ObjectStorageConfigError, match="region"):
            create_provider(config)

    def test_aws_irsa_without_env_raises(self) -> None:
        config = ObjectStorageConfig(
            backend="aws",
            region="eu-central-2",
            use_irsa=True,
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AWS_WEB_IDENTITY_TOKEN_FILE", None)
            with pytest.raises(
                ObjectStorageConfigError, match="AWS_WEB_IDENTITY_TOKEN_FILE"
            ):
                create_provider(config)

    def test_aws_static_keys_missing_secret_raises(self) -> None:
        config = ObjectStorageConfig(
            backend="aws",
            region="eu-central-2",
            use_irsa=False,
            access_key_id="AKIA-test",
            # secret_access_key missing
        )
        with pytest.raises(ObjectStorageConfigError, match="secret_access_key"):
            create_provider(config)


class TestUnknownBackend:
    def test_unknown_backend_raises(self) -> None:
        # Bypass dataclass typing by constructing directly with a bogus value
        config = ObjectStorageConfig.__new__(ObjectStorageConfig)
        object.__setattr__(config, "backend", "swift")
        object.__setattr__(config, "endpoint", None)
        object.__setattr__(config, "access_key", None)
        object.__setattr__(config, "secret_key", None)
        object.__setattr__(config, "secure", False)
        object.__setattr__(config, "region", None)
        object.__setattr__(config, "endpoint_url", None)
        object.__setattr__(config, "use_irsa", True)
        object.__setattr__(config, "access_key_id", None)
        object.__setattr__(config, "secret_access_key", None)
        with pytest.raises(ObjectStorageConfigError, match="unknown backend"):
            create_provider(config)


class TestFactoryLogs:
    """Verify the factory module emits diagnostics (PYTHON-ENGINEERING §7)."""

    def test_minio_creation_logs_at_info(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = ObjectStorageConfig(
            backend="minio",
            endpoint="localhost:9000",
            access_key="a",
            secret_key="b",
        )
        with caplog.at_level(logging.INFO, logger="audittrace_object_storage.factory"):
            create_provider(config)
        assert any(
            "MinIOObjectStorageProvider" in r.message and r.levelno == logging.INFO
            for r in caplog.records
        )

    def test_aws_creation_logs_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        config = ObjectStorageConfig(
            backend="aws",
            region="eu-central-2",
            use_irsa=False,
            access_key_id="x",
            secret_access_key="y",
        )
        with caplog.at_level(logging.INFO, logger="audittrace_object_storage.factory"):
            create_provider(config)
        assert any(
            "AWSObjectStorageProvider" in r.message and r.levelno == logging.INFO
            for r in caplog.records
        )

    def test_unknown_backend_logs_at_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = ObjectStorageConfig.__new__(ObjectStorageConfig)
        for field in (
            "endpoint",
            "access_key",
            "secret_key",
            "region",
            "endpoint_url",
            "access_key_id",
            "secret_access_key",
        ):
            object.__setattr__(config, field, None)
        object.__setattr__(config, "backend", "swift")
        object.__setattr__(config, "secure", False)
        object.__setattr__(config, "use_irsa", True)
        with caplog.at_level(logging.ERROR, logger="audittrace_object_storage.factory"):
            with pytest.raises(ObjectStorageConfigError):
                create_provider(config)
        assert any(
            r.levelno == logging.ERROR and "unknown backend" in r.message
            for r in caplog.records
        )
