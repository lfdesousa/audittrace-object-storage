"""Tests for provider.py — the abstract base class contract."""

from __future__ import annotations

import pytest

from audittrace_object_storage.provider import S3ObjectStorageProvider


class TestS3ObjectStorageProviderABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            S3ObjectStorageProvider()  # type: ignore[abstract]

    def test_partial_implementation_fails_instantiation(self) -> None:
        class _Half(S3ObjectStorageProvider):
            def list_objects(self, bucket, prefix=""):  # noqa: ANN001
                return iter(())

            # All other methods left unimplemented.

        with pytest.raises(TypeError):
            _Half()  # type: ignore[abstract]

    def test_full_implementation_can_be_instantiated(self) -> None:
        class _Complete(S3ObjectStorageProvider):
            def list_objects(self, bucket, prefix=""):  # noqa: ANN001
                return iter(())

            def get_object(self, bucket, key):  # noqa: ANN001
                raise NotImplementedError

            def put_object(self, bucket, key, data, length, content_type=None):  # noqa: ANN001
                pass

            def stat_object(self, bucket, key):  # noqa: ANN001
                raise NotImplementedError

            def remove_object(self, bucket, key):  # noqa: ANN001
                pass

            def copy_object(self, src_bucket, src_key, dst_bucket, dst_key):  # noqa: ANN001
                pass

            def health_check(self):
                return True

        provider = _Complete()
        assert provider.health_check() is True

    def test_required_method_set_is_seven(self) -> None:
        # Guard against accidental ABC surface growth — the union of
        # consumer call sites in AT-AI + content-control is exactly
        # these seven methods. New surface needs a design review.
        expected = {
            "list_objects",
            "get_object",
            "put_object",
            "stat_object",
            "remove_object",
            "copy_object",
            "health_check",
        }
        assert S3ObjectStorageProvider.__abstractmethods__ == expected
