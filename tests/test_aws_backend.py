"""Tests for aws_backend.py — boto3 wrapping with moto.

Per ADR-006 acceptance criterion: "A test (in the AT-AI repo) covers
AWSObjectStorageProvider against moto's S3 mock." Same shape here.
moto[s3] gives us a process-local fake S3 endpoint that boto3 talks to
just like real AWS — no AWS credentials, no network egress.
"""

from __future__ import annotations

import io
import logging
import os
from unittest import mock

import boto3
import pytest
from moto import mock_aws

from audittrace_object_storage.aws_backend import (
    AWSObjectStorageProvider,
    _is_not_found,
)
from audittrace_object_storage.types import (
    ObjectNotFoundError,
    ObjectReader,
    ObjectStorageConfigError,
    ObjectStorageError,
)

BUCKET = "audittrace-loadtest-objects-test"
REGION = "eu-central-2"


@pytest.fixture
def aws_credentials() -> None:
    """Set fake AWS credentials so boto3 doesn't try IRSA in tests."""
    with mock.patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_SECURITY_TOKEN": "testing",
            "AWS_SESSION_TOKEN": "testing",
            "AWS_DEFAULT_REGION": REGION,
        },
    ):
        yield


@pytest.fixture
def mocked_s3(aws_credentials):  # noqa: ARG001
    """Spin up a moto S3 mock and pre-create the test bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield client


@pytest.fixture
def provider(mocked_s3) -> AWSObjectStorageProvider:  # noqa: ARG001
    """Build a provider against the moto-mocked S3 (static keys, not IRSA)."""
    return AWSObjectStorageProvider(
        region=REGION,
        use_irsa=False,
        access_key_id="testing",
        secret_access_key="testing",
    )


class TestIRSAAssertion:
    def test_irsa_with_env_succeeds(self) -> None:
        with mock_aws():
            with mock.patch.dict(
                os.environ,
                {
                    "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/secrets/eks.amazonaws.com/serviceaccount/token",
                    "AWS_ACCESS_KEY_ID": "testing",
                    "AWS_SECRET_ACCESS_KEY": "testing",
                },
            ):
                provider = AWSObjectStorageProvider(region=REGION, use_irsa=True)
                assert provider._region == REGION

    def test_irsa_without_env_raises(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AWS_WEB_IDENTITY_TOKEN_FILE", None)
            with pytest.raises(ObjectStorageConfigError, match="IRSA enabled"):
                AWSObjectStorageProvider(region=REGION, use_irsa=True)

    def test_static_keys_without_keys_raises(self) -> None:
        with pytest.raises(ObjectStorageConfigError, match="access_key_id"):
            AWSObjectStorageProvider(region=REGION, use_irsa=False)


class TestIsNotFoundHelper:
    def test_no_such_key_code(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            error_response={
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="GetObject",
        )
        assert _is_not_found(exc) is True

    def test_404_status(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            error_response={
                "Error": {"Code": "Whatever"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="HeadObject",
        )
        assert _is_not_found(exc) is True

    def test_no_such_bucket(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="GetObject",
        )
        assert _is_not_found(exc) is True

    def test_other_error(self) -> None:
        from botocore.exceptions import ClientError

        exc = ClientError(
            error_response={
                "Error": {"Code": "AccessDenied"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            operation_name="GetObject",
        )
        assert _is_not_found(exc) is False


class TestListObjects:
    def test_empty_bucket(self, provider: AWSObjectStorageProvider) -> None:
        assert list(provider.list_objects(BUCKET)) == []

    def test_with_objects(self, provider: AWSObjectStorageProvider, mocked_s3) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="episodic/a.md", Body=b"a")
        mocked_s3.put_object(Bucket=BUCKET, Key="episodic/b.md", Body=b"bb")
        mocked_s3.put_object(Bucket=BUCKET, Key="procedural/c.md", Body=b"ccc")
        objects = list(provider.list_objects(BUCKET, prefix="episodic/"))
        keys = sorted(o.object_name for o in objects)
        assert keys == ["episodic/a.md", "episodic/b.md"]

    def test_size_populated(
        self, provider: AWSObjectStorageProvider, mocked_s3
    ) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="x", Body=b"123456789")
        objects = list(provider.list_objects(BUCKET))
        assert objects[0].size == 9

    def test_pagination(self, provider: AWSObjectStorageProvider, mocked_s3) -> None:
        # boto3's list_objects_v2 default page size is 1000; create 1010
        # objects to ensure pagination triggers.
        for i in range(1010):
            mocked_s3.put_object(Bucket=BUCKET, Key=f"obj-{i:04d}", Body=b"x")
        count = sum(1 for _ in provider.list_objects(BUCKET))
        assert count == 1010


class TestGetObject:
    def test_round_trip(
        self, provider: AWSObjectStorageProvider, mocked_s3, small_body: bytes
    ) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="hello", Body=small_body)
        with provider.get_object(BUCKET, "hello") as reader:
            assert reader.read() == small_body

    def test_missing_key_raises(self, provider: AWSObjectStorageProvider) -> None:
        with pytest.raises(ObjectNotFoundError):
            provider.get_object(BUCKET, "no-such-key")

    def test_returns_object_reader(
        self, provider: AWSObjectStorageProvider, mocked_s3
    ) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")
        reader = provider.get_object(BUCKET, "k")
        try:
            assert isinstance(reader, ObjectReader)
        finally:
            reader.close()


class TestPutObject:
    def test_round_trip(self, provider: AWSObjectStorageProvider, mocked_s3) -> None:
        body = io.BytesIO(b"payload")
        provider.put_object(BUCKET, "p", body, length=7)
        response = mocked_s3.get_object(Bucket=BUCKET, Key="p")
        assert response["Body"].read() == b"payload"

    def test_with_content_type(
        self, provider: AWSObjectStorageProvider, mocked_s3
    ) -> None:
        provider.put_object(
            BUCKET,
            "p.md",
            io.BytesIO(b"hi"),
            length=2,
            content_type="text/markdown",
        )
        response = mocked_s3.head_object(Bucket=BUCKET, Key="p.md")
        assert response["ContentType"] == "text/markdown"

    def test_missing_bucket_raises(self, provider: AWSObjectStorageProvider) -> None:
        with pytest.raises(ObjectStorageError):
            provider.put_object("no-such-bucket", "k", io.BytesIO(b""), length=0)


class TestStatObject:
    def test_happy_path(self, provider: AWSObjectStorageProvider, mocked_s3) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="foo", Body=b"hello")
        meta = provider.stat_object(BUCKET, "foo")
        assert meta.size == 5
        assert meta.object_name == "foo"

    def test_missing_raises(self, provider: AWSObjectStorageProvider) -> None:
        with pytest.raises(ObjectNotFoundError):
            provider.stat_object(BUCKET, "missing")


class TestRemoveObject:
    def test_happy_path(self, provider: AWSObjectStorageProvider, mocked_s3) -> None:
        from botocore.exceptions import ClientError

        mocked_s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")
        provider.remove_object(BUCKET, "k")
        with pytest.raises(ClientError):  # 404 via moto
            mocked_s3.head_object(Bucket=BUCKET, Key="k")

    def test_missing_is_noop(self, provider: AWSObjectStorageProvider) -> None:
        # S3 DELETE on a missing object returns 204, not an error
        provider.remove_object(BUCKET, "no-such-key")  # must not raise


class TestCopyObject:
    def test_happy_path(self, provider: AWSObjectStorageProvider, mocked_s3) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="src", Body=b"copied")
        provider.copy_object(BUCKET, "src", BUCKET, "dst")
        response = mocked_s3.get_object(Bucket=BUCKET, Key="dst")
        assert response["Body"].read() == b"copied"

    def test_missing_source_raises(self, provider: AWSObjectStorageProvider) -> None:
        with pytest.raises(ObjectNotFoundError):
            provider.copy_object(BUCKET, "no-such-key", BUCKET, "dst")


class TestHealthCheck:
    def test_returns_true(self, provider: AWSObjectStorageProvider) -> None:
        assert provider.health_check() is True

    def test_returns_false_on_error(self, provider: AWSObjectStorageProvider) -> None:
        with mock.patch.object(
            provider._client, "list_buckets", side_effect=RuntimeError("offline")
        ):
            assert provider.health_check() is False


class TestEndpointUrlPassThrough:
    def test_endpoint_url_used(self, aws_credentials) -> None:  # noqa: ARG002
        # OVH OS / non-AWS S3 endpoints route through endpoint_url
        provider = AWSObjectStorageProvider(
            region=REGION,
            use_irsa=False,
            access_key_id="x",
            secret_access_key="y",
            endpoint_url="https://s3.gra.io.cloud.ovh.net",
        )
        assert provider._client.meta.endpoint_url == "https://s3.gra.io.cloud.ovh.net"


class TestAWSBackendLogs:
    """Verify the aws_backend module emits diagnostics (PYTHON-ENGINEERING §7)."""

    LOGGER_NAME = "audittrace_object_storage.aws_backend"

    def test_get_object_logs_at_debug(
        self,
        provider: AWSObjectStorageProvider,
        mocked_s3,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mocked_s3.put_object(Bucket=BUCKET, Key="k", Body=b"x")
        with caplog.at_level(logging.DEBUG, logger=self.LOGGER_NAME):
            with provider.get_object(BUCKET, "k"):
                pass
        assert any(
            r.levelno == logging.DEBUG and "get_object" in r.message
            for r in caplog.records
        )

    def test_get_object_error_logs_at_warning(
        self,
        provider: AWSObjectStorageProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=self.LOGGER_NAME):
            with pytest.raises(ObjectNotFoundError):
                provider.get_object(BUCKET, "no-such-key")
        assert any(
            r.levelno == logging.WARNING and "get_object failed" in r.message
            for r in caplog.records
        )

    def test_irsa_missing_logs_at_error(self, caplog: pytest.LogCaptureFixture) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AWS_WEB_IDENTITY_TOKEN_FILE", None)
            with caplog.at_level(logging.ERROR, logger=self.LOGGER_NAME):
                with pytest.raises(ObjectStorageConfigError):
                    AWSObjectStorageProvider(region=REGION, use_irsa=True)
        assert any(
            r.levelno == logging.ERROR
            and "IRSA enabled but AWS_WEB_IDENTITY_TOKEN_FILE missing" in r.message
            for r in caplog.records
        )
