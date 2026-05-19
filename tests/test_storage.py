"""ObjectStorage — hash-keyed uploads, idempotent puts."""

import hashlib
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from health_metrics.storage import ObjectStorage


def _make_storage_with_existing(keys: set[str]):
    client = MagicMock()

    def head_object(Bucket, Key):
        if Key in keys:
            return {}
        err = {"Error": {"Code": "404", "Message": "Not Found"}}
        raise ClientError(err, "HeadObject")

    client.head_object.side_effect = head_object
    return client, ObjectStorage(client, bucket="test-bucket")


def test_upload_with_sha_uses_sha256_of_bytes():
    client, store = _make_storage_with_existing(set())
    data = b"abc123"
    key = store.upload_with_sha(data, prefix="meals", ext="jpg")
    expected = f"meals/{hashlib.sha256(data).hexdigest()}.jpg"
    assert key == expected
    client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key=expected,
        Body=data,
        ContentType="image/jpeg",
    )


def test_upload_with_sha_skips_existing():
    data = b"abc123"
    existing_key = f"meals/{hashlib.sha256(data).hexdigest()}.jpg"
    client, store = _make_storage_with_existing({existing_key})
    key = store.upload_with_sha(data, prefix="meals", ext="jpg")
    assert key == existing_key
    client.put_object.assert_not_called()


def test_stream_yields_chunks():
    client = MagicMock()
    body = MagicMock()
    body.iter_chunks.return_value = iter([b"chunk1", b"chunk2"])
    client.get_object.return_value = {"Body": body}
    store = ObjectStorage(client, bucket="test-bucket")
    chunks = list(store.stream("meals/abc.jpg"))
    assert chunks == [b"chunk1", b"chunk2"]
    client.get_object.assert_called_once_with(Bucket="test-bucket", Key="meals/abc.jpg")


def test_delete_calls_delete_object():
    client = MagicMock()
    store = ObjectStorage(client, bucket="test-bucket")
    store.delete("meals/abc.jpg")
    client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="meals/abc.jpg")


def test_get_storage_returns_none_when_unconfigured(monkeypatch):
    from health_metrics import storage as storage_mod

    class _FakeSettings:
        s3_endpoint_url = "https://example.com"
        s3_bucket = "test-bucket"
        s3_access_key_id = None  # missing → should return None
        s3_secret_access_key = "secret"
        s3_region = "us-east-1"

    monkeypatch.setattr(storage_mod, "get_settings", lambda: _FakeSettings())
    storage_mod.reset_storage_for_tests()
    try:
        assert storage_mod.get_storage() is None
    finally:
        storage_mod.reset_storage_for_tests()


def test_get_storage_returns_object_storage_when_configured(monkeypatch):
    from health_metrics import storage as storage_mod

    class _FakeSettings:
        s3_endpoint_url = "https://example.com"
        s3_bucket = "test-bucket"
        s3_access_key_id = "AKIA"
        s3_secret_access_key = "secret"
        s3_region = "us-east-1"

    monkeypatch.setattr(storage_mod, "get_settings", lambda: _FakeSettings())
    storage_mod.reset_storage_for_tests()
    try:
        store1 = storage_mod.get_storage()
        assert store1 is not None
        assert isinstance(store1, storage_mod.ObjectStorage)
        assert store1.bucket == "test-bucket"
        store2 = storage_mod.get_storage()
        assert store2 is store1  # cached singleton
    finally:
        storage_mod.reset_storage_for_tests()
