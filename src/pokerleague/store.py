"""JSON document storage: a local folder for development, S3 in production.

Keys used:
  private/players.json           player registry (contact + payment details)
  tournaments/<YYYY-MM-DD>.json  one record per tournament (draft or approved)
  raw/<YYYY-MM-DD>/<file>.csv    uploaded PokerNow logs
The S3 bucket is private and versioned, so every change can be rolled back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Store:
    def get_bytes(self, key: str) -> bytes | None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> None: ...

    def get_json(self, key: str, default=None):
        data = self.get_bytes(key)
        return json.loads(data) if data is not None else default

    def put_json(self, key: str, value) -> None:
        self.put_bytes(key, json.dumps(value, indent=2, sort_keys=False, default=str).encode(), "application/json")


class LocalStore(Store):
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError(f"key escapes store root: {key}")
        return path

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def list(self, prefix: str) -> list[str]:
        base = self.root
        if not base.exists():
            return []
        keys = [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()]
        return sorted(k for k in keys if k.startswith(prefix))

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class S3Store(Store):
    def __init__(self, bucket: str, client=None):
        import boto3

        self.bucket = bucket
        self.s3 = client or boto3.client("s3")

    def get_bytes(self, key: str) -> bytes | None:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def list(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.s3.list_objects_v2(**kwargs)
            keys += [o["Key"] for o in resp.get("Contents", [])]
            if not resp.get("IsTruncated"):
                return sorted(keys)
            token = resp["NextContinuationToken"]

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def store_from_env() -> Store:
    bucket = os.environ.get("DATA_BUCKET")
    if bucket:
        return S3Store(bucket)
    return LocalStore(os.environ.get("DATA_DIR", "data/store"))
