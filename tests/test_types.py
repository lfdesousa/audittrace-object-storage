"""Tests for types.py — ObjectMetadata, ObjectReader, exceptions."""

from __future__ import annotations

import dataclasses
import io
from datetime import UTC, datetime

import pytest

from audittrace_object_storage.types import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectReader,
    ObjectStorageConfigError,
    ObjectStorageError,
    QuarantinedObjectAccessError,
)


class TestObjectMetadata:
    def test_minimal_construction(self) -> None:
        meta = ObjectMetadata(object_name="foo", size=42)
        assert meta.object_name == "foo"
        assert meta.size == 42
        assert meta.etag is None
        assert meta.last_modified is None
        assert meta.content_type is None

    def test_full_construction(self) -> None:
        now = datetime.now(UTC)
        meta = ObjectMetadata(
            object_name="bar.md",
            size=128,
            etag="abc123",
            last_modified=now,
            content_type="text/markdown",
        )
        assert meta.last_modified == now
        assert meta.content_type == "text/markdown"

    def test_is_frozen(self) -> None:
        meta = ObjectMetadata(object_name="x", size=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            meta.size = 999  # type: ignore[misc]


class _FakeStream:
    """Minimal stream double for ObjectReader tests."""

    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)
        self.closed = False

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            return self._buf.read()
        return self._buf.read(amt)

    def close(self) -> None:
        self.closed = True


class TestObjectReader:
    def test_read_full(self) -> None:
        stream = _FakeStream(b"payload")
        reader = ObjectReader(stream=stream)
        assert reader.read() == b"payload"

    def test_read_partial(self) -> None:
        stream = _FakeStream(b"payload")
        reader = ObjectReader(stream=stream)
        assert reader.read(3) == b"pay"

    def test_context_manager_closes_stream(self) -> None:
        stream = _FakeStream(b"x")
        with ObjectReader(stream=stream) as reader:
            assert reader.read() == b"x"
        assert stream.closed is True

    def test_context_manager_closes_on_exception(self) -> None:
        stream = _FakeStream(b"x")
        with pytest.raises(RuntimeError, match="boom"), ObjectReader(stream=stream):
            raise RuntimeError("boom")
        assert stream.closed is True

    def test_on_close_callback_runs(self) -> None:
        stream = _FakeStream(b"x")
        marker = {"fired": False}

        def cleanup() -> None:
            marker["fired"] = True

        with ObjectReader(stream=stream, on_close=cleanup):
            pass
        assert marker["fired"] is True

    def test_on_close_runs_even_if_stream_close_raises(self) -> None:
        class _BadStream:
            def read(self, amt: int | None = None) -> bytes:
                return b""

            def close(self) -> None:
                raise OSError("disconnect")

        marker = {"fired": False}

        def cleanup() -> None:
            marker["fired"] = True

        reader = ObjectReader(stream=_BadStream(), on_close=cleanup)
        with pytest.raises(OSError, match="disconnect"):
            reader.close()
        assert marker["fired"] is True

    def test_close_is_idempotent(self) -> None:
        stream = _FakeStream(b"x")
        reader = ObjectReader(stream=stream)
        reader.close()
        reader.close()  # second close is no-op, must not raise
        assert stream.closed is True

    def test_read_after_close_raises(self) -> None:
        stream = _FakeStream(b"x")
        reader = ObjectReader(stream=stream)
        reader.close()
        with pytest.raises(ValueError):
            reader.read()


class TestExceptions:
    def test_hierarchy(self) -> None:
        assert issubclass(ObjectNotFoundError, ObjectStorageError)
        assert issubclass(ObjectStorageConfigError, ObjectStorageError)
        assert issubclass(QuarantinedObjectAccessError, ObjectStorageError)

    def test_can_catch_subclasses_as_base(self) -> None:
        with pytest.raises(ObjectStorageError):
            raise ObjectNotFoundError("nope")
        with pytest.raises(ObjectStorageError):
            raise ObjectStorageConfigError("bad config")
        with pytest.raises(ObjectStorageError):
            raise QuarantinedObjectAccessError("quarantine/foo")
