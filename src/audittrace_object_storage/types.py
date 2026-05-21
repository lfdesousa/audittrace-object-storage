"""Types + exceptions for the object-storage abstraction.

Foundation module — every other module imports from here. Keeps the
backend-specific stream-handling out of the consumer code: every
consumer uses ``with provider.get_object(...) as reader: reader.read()``
regardless of whether ``provider`` wraps minio or boto3.

PYTHON-ENGINEERING §1: PEP-343 (`with`) is the form. The ABC returns
``ObjectReader``; consumers use it as a context manager; backends are
responsible for the underlying ``release_conn`` / ``close`` plumbing
on ``__exit__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol


@dataclass(frozen=True)
class ObjectMetadata:
    """Backend-agnostic object metadata returned by list_objects / stat_object."""

    object_name: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None
    content_type: str | None = None


class _StreamLike(Protocol):
    """Minimal stream protocol — backends adapt their native response to this."""

    def read(self, amt: int | None = ...) -> bytes: ...
    def close(self) -> None: ...


class ObjectReader:
    """Context-manager wrapper for an object-storage GET response.

    Backends instantiate this with a callable that closes the
    underlying connection (minio's ``release_conn`` + ``close``, or
    boto3's ``StreamingBody.close``). ``__exit__`` always runs cleanup,
    even on exception — the PEP-343 form of the old try/finally pattern.
    """

    def __init__(self, stream: _StreamLike, on_close: callable | None = None) -> None:  # type: ignore[type-arg]
        self._stream = stream
        self._on_close = on_close
        self._closed = False

    def read(self, amt: int | None = None) -> bytes:
        """Return up to ``amt`` bytes from the underlying stream.

        ``amt=None`` reads to EOF.
        """
        if self._closed:
            raise ValueError("ObjectReader is closed")
        if amt is None:
            return self._stream.read()
        return self._stream.read(amt)

    def close(self) -> None:
        """Close the underlying stream and any backend-specific resources."""
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        finally:
            if self._on_close is not None:
                self._on_close()

    def __enter__(self) -> ObjectReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


class ObjectStorageError(Exception):
    """Base for all object-storage errors raised by this package."""


class ObjectNotFoundError(ObjectStorageError):
    """The requested object does not exist.

    Backends translate native 404-equivalents to this single exception so
    consumer code can do ``except ObjectNotFoundError`` regardless of the
    backend in use.
    """


class ObjectStorageConfigError(ObjectStorageError):
    """Misconfiguration detected at construction time.

    The factory raises this when required fields are missing
    (e.g. ``backend="aws"`` with no ``region``), or when ``use_irsa=True``
    but the EKS pod-identity webhook hasn't injected
    ``AWS_WEB_IDENTITY_TOKEN_FILE``. Loud-fail beats silent fall-through
    to anonymous access.
    """


class QuarantinedObjectAccessError(ObjectStorageError):
    """Defense-in-depth guard fired — a quarantine/* GET reached the provider.

    Raised by ``QuarantineDenyingObjectStorageClient`` (consumer-side
    wrapper, lives in the AuditTrace-AI repo, not here). Reserved here
    so consumers can ``from audittrace_object_storage import
    QuarantinedObjectAccessError`` and catch it without depending on
    AT-AI internals.
    """
