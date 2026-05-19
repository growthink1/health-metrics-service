"""Object storage wrapper around boto3 (S3 / S3-compatible).

Single responsibility: upload bytes keyed by sha256; stream them back; delete.
No public URLs are returned to clients — all reads go through a backend proxy.
"""

import hashlib
from typing import Any, Iterator, Optional

import boto3
from botocore.exceptions import ClientError

from .config import get_settings


_EXT_TO_CONTENT_TYPE = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class ObjectStorage:
    def __init__(self, client: Any, bucket: str):
        self.client = client
        self.bucket = bucket

    def upload_with_sha(self, data: bytes, prefix: str, ext: str = "jpg") -> str:
        """Upload bytes keyed at <prefix>/<sha256>.<ext>. Idempotent."""
        sha = hashlib.sha256(data).hexdigest()
        key = f"{prefix}/{sha}.{ext}"
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return key  # already there
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey", "NotFound"):
                raise
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=_EXT_TO_CONTENT_TYPE.get(ext, "application/octet-stream"),
        )
        return key

    def stream(self, key: str) -> Iterator[bytes]:
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        body = resp["Body"]
        for chunk in body.iter_chunks(8192):
            yield chunk

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_storage: Optional[ObjectStorage] = None


def get_storage() -> Optional[ObjectStorage]:
    """Lazy singleton. Returns None if S3 env not configured (CI / local without S3)."""
    global _storage
    if _storage is not None:
        return _storage
    s = get_settings()
    if not (s.s3_endpoint_url and s.s3_bucket and s.s3_access_key_id and s.s3_secret_access_key):
        return None
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key_id,
        aws_secret_access_key=s.s3_secret_access_key,
        region_name=s.s3_region,
    )
    _storage = ObjectStorage(client, bucket=s.s3_bucket)
    return _storage


def reset_storage_for_tests() -> None:
    """Tests that monkeypatch settings should call this after to drop the cached client."""
    global _storage
    _storage = None
