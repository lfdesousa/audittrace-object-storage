"""Tests for minio_backend.py — MinIO library wrapping.

Hand-rolled MagicMock pattern matching AT-AI's existing approach
(see ``tests/test_episodic_service.py::_FakeMinio``). No external
MinIO container required; ``_FakeMinio`` is keyed on the seven
methods the ABC actually calls.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from unittest import mock

import pytest
from minio.error import S3Error

from audittrace_object_storage.minio_backend import (
    MinIOObjectStorageProvider,
    _from_minio_object,
    _translate_s3_error,
)
from audittrace_object_storage.types import (
    ObjectNotFoundError,
    ObjectReader,
    ObjectStorageError,
)


def _s3_error(code: str, message: str = "") -> S3Error:
    """Construct a minio S3Error matching the runtime shape."""
    return S3Error(
        code=code,
        message=message or code,
        resource="bucket/key",
        request_id="req",
        host_id="host",
        response=mock.Mock(headers={}, status=404 if code == "NoSuchKey" else 500),
    )


class _MinioObj:
    """Tiny stand-in for a minio response.Object."""

    def __init__(self, object_name: str, size: int = 0) -> None:
        self.object_name = object_name
        self.size = size
        self.etag = "etag-" + object_name
        self.last_modified = datetime.now(UTC)
        self.content_type = "application/octet-stream"


class _MinioResponse:
    """Tiny stand-in for the response object returned by Minio.get_object."""

    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)
        self.closed = False
        self.conn_released = False

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            return self._buf.read()
        return self._buf.read(amt)

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.conn_released = True


@pytest.fixture
def provider() -> MinIOObjectStorageProvider:
    """Build a provider with the underlying minio client mocked."""
    with mock.patch("audittrace_object_storage.minio_backend.Minio"):
        return MinIOObjectStorageProvider(
            endpoint="localhost:9000",
            access_key="a",
            secret_key="b",
            secure=False,
        )


class TestHelpers:
    def test_from_minio_object_full(self) -> None:
        obj = _MinioObj("foo", size=123)
        meta = _from_minio_object(obj)
        assert meta.object_name == "foo"
        assert meta.size == 123
        assert meta.etag == "etag-foo"

    def test_from_minio_object_missing_attrs(self) -> None:
        obj = object()  # no attributes at all
        meta = _from_minio_object(obj)
        assert meta.object_name == ""
        assert meta.size == 0

    def test_translate_s3_error_no_such_key(self) -> None:
        err = _translate_s3_error(_s3_error("NoSuchKey"))
        assert isinstance(err, ObjectNotFoundError)

    def test_translate_s3_error_other(self) -> None:
        err = _translate_s3_error(_s3_error("AccessDenied"))
        assert isinstance(err, ObjectStorageError)
        assert not isinstance(err, ObjectNotFoundError)
        assert "AccessDenied" in str(err)


class TestListObjects:
    def test_yields_metadata(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.list_objects.return_value = [
            _MinioObj("episodic/2026-01.md", 10),
            _MinioObj("episodic/2026-02.md", 20),
        ]
        results = list(provider.list_objects("memory-shared", prefix="episodic/"))
        assert [m.object_name for m in results] == [
            "episodic/2026-01.md",
            "episodic/2026-02.md",
        ]
        provider._client.list_objects.assert_called_once_with(
            "memory-shared", prefix="episodic/", recursive=True
        )

    def test_empty(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.list_objects.return_value = []
        assert list(provider.list_objects("memory-shared")) == []

    def test_translates_s3_error(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.list_objects.side_effect = _s3_error("AccessDenied")
        with pytest.raises(ObjectStorageError):
            list(provider.list_objects("memory-shared"))


class TestGetObject:
    def test_returns_reader_with_payload(
        self, provider: MinIOObjectStorageProvider
    ) -> None:
        provider._client.get_object.return_value = _MinioResponse(b"abc")
        with provider.get_object("memory-shared", "x") as reader:
            assert reader.read() == b"abc"

    def test_release_conn_called_on_exit(
        self, provider: MinIOObjectStorageProvider
    ) -> None:
        response = _MinioResponse(b"abc")
        provider._client.get_object.return_value = response
        with provider.get_object("memory-shared", "x") as reader:
            reader.read()
        assert response.closed is True
        assert response.conn_released is True

    def test_release_conn_called_on_exception(
        self, provider: MinIOObjectStorageProvider
    ) -> None:
        response = _MinioResponse(b"abc")
        provider._client.get_object.return_value = response
        with pytest.raises(RuntimeError):
            with provider.get_object("memory-shared", "x"):
                raise RuntimeError("boom")
        assert response.conn_released is True

    def test_no_such_key_translates(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.get_object.side_effect = _s3_error("NoSuchKey")
        with pytest.raises(ObjectNotFoundError):
            provider.get_object("memory-shared", "missing")

    def test_other_error_translates(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.get_object.side_effect = _s3_error("AccessDenied")
        with pytest.raises(ObjectStorageError) as excinfo:
            provider.get_object("memory-shared", "x")
        assert not isinstance(excinfo.value, ObjectNotFoundError)


class TestPutObject:
    def test_happy_path(self, provider: MinIOObjectStorageProvider) -> None:
        body = io.BytesIO(b"data")
        provider.put_object("memory-shared", "k", body, length=4)
        provider._client.put_object.assert_called_once_with(
            "memory-shared",
            "k",
            body,
            length=4,
            content_type="application/octet-stream",
        )

    def test_with_content_type(self, provider: MinIOObjectStorageProvider) -> None:
        body = io.BytesIO(b"data")
        provider.put_object(
            "memory-shared", "k.md", body, length=4, content_type="text/markdown"
        )
        provider._client.put_object.assert_called_once_with(
            "memory-shared",
            "k.md",
            body,
            length=4,
            content_type="text/markdown",
        )

    def test_translates_error(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.put_object.side_effect = _s3_error("BucketNotFound")
        with pytest.raises(ObjectStorageError):
            provider.put_object("memory-shared", "k", io.BytesIO(b""), length=0)


class TestStatObject:
    def test_happy_path(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.stat_object.return_value = _MinioObj("foo", size=99)
        meta = provider.stat_object("memory-shared", "foo")
        assert meta.size == 99

    def test_no_such_key_translates(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.stat_object.side_effect = _s3_error("NoSuchKey")
        with pytest.raises(ObjectNotFoundError):
            provider.stat_object("memory-shared", "missing")


class TestRemoveObject:
    def test_happy_path(self, provider: MinIOObjectStorageProvider) -> None:
        provider.remove_object("memory-shared", "k")
        provider._client.remove_object.assert_called_once_with("memory-shared", "k")

    def test_no_such_key_is_noop(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.remove_object.side_effect = _s3_error("NoSuchKey")
        provider.remove_object("memory-shared", "missing")  # must not raise

    def test_other_error_propagates(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.remove_object.side_effect = _s3_error("AccessDenied")
        with pytest.raises(ObjectStorageError):
            provider.remove_object("memory-shared", "k")


class TestCopyObject:
    def test_happy_path(self, provider: MinIOObjectStorageProvider) -> None:
        provider.copy_object("src", "a", "dst", "b")
        provider._client.copy_object.assert_called_once()
        # The second positional + kwargs should reach CopySource — we just
        # verify the method was invoked; the CopySource arg is constructed
        # inside the method.

    def test_translates_error(self, provider: MinIOObjectStorageProvider) -> None:
        provider._client.copy_object.side_effect = _s3_error("AccessDenied")
        with pytest.raises(ObjectStorageError):
            provider.copy_object("src", "a", "dst", "b")


class TestHealthCheck:
    def test_returns_true_when_reachable(
        self, provider: MinIOObjectStorageProvider
    ) -> None:
        provider._client.list_buckets.return_value = []
        assert provider.health_check() is True

    def test_returns_false_on_any_error(
        self, provider: MinIOObjectStorageProvider
    ) -> None:
        provider._client.list_buckets.side_effect = RuntimeError("network down")
        assert provider.health_check() is False


class TestObjectReaderInterop:
    """Smoke-tests that the ObjectReader returned by get_object behaves correctly."""

    def test_reader_is_object_reader_instance(
        self, provider: MinIOObjectStorageProvider
    ) -> None:
        provider._client.get_object.return_value = _MinioResponse(b"x")
        reader = provider.get_object("memory-shared", "x")
        assert isinstance(reader, ObjectReader)
        reader.close()


class TestMinIOBackendLogs:
    """Verify the minio_backend module emits diagnostics (PYTHON-ENGINEERING §7)."""

    LOGGER_NAME = "audittrace_object_storage.minio_backend"

    def test_get_object_logs_at_debug(
        self,
        provider: MinIOObjectStorageProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        provider._client.get_object.return_value = _MinioResponse(b"x")
        with caplog.at_level(logging.DEBUG, logger=self.LOGGER_NAME):
            with provider.get_object("memory-shared", "key1"):
                pass
        assert any(
            r.levelno == logging.DEBUG and "get_object" in r.message
            for r in caplog.records
        )

    def test_get_object_error_logs_at_warning(
        self,
        provider: MinIOObjectStorageProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        provider._client.get_object.side_effect = _s3_error("AccessDenied")
        with caplog.at_level(logging.WARNING, logger=self.LOGGER_NAME):
            with pytest.raises(ObjectStorageError):
                provider.get_object("memory-shared", "k")
        assert any(
            r.levelno == logging.WARNING and "get_object failed" in r.message
            for r in caplog.records
        )

    def test_health_check_failure_logs_at_warning(
        self,
        provider: MinIOObjectStorageProvider,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        provider._client.list_buckets.side_effect = RuntimeError("offline")
        with caplog.at_level(logging.WARNING, logger=self.LOGGER_NAME):
            assert provider.health_check() is False
        assert any(
            r.levelno == logging.WARNING and "health_check failed" in r.message
            for r in caplog.records
        )
