"""Cloudflare R2 storage via the boto3 S3 client.

R2 quirks handled: region="auto", signature_version="s3v4", and **no ACLs**
(R2 rejects S3 ACL params). Presigned PUT/GET let the browser move large files
directly. The boto3 client is created lazily and reused (clients are safe for
concurrent API calls across threads).
"""
from __future__ import annotations

from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.storage.base import StorageBackend, StoredObject, content_disposition

log = get_logger(__name__)


class R2Storage(StorageBackend):
    scheme = "r2"

    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.r2_bucket or ""
        self._endpoint = s.r2_endpoint_url()
        self._access = s.r2_access_key_id
        self._secret = s.r2_secret_access_key
        self._client = None

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access,
                aws_secret_access_key=self._secret,
                region_name="auto",
                config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            )
        return self._client

    @staticmethod
    def _parse(uri: str) -> tuple[str, str]:
        rest = uri[len("r2://"):]
        bucket, _, key = rest.partition("/")
        return bucket, key

    def put(self, data: bytes, *, key: str, content_type: Optional[str] = None) -> StoredObject:
        extra = {"ContentType": content_type} if content_type else {}
        self.client().put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return StoredObject(
            uri=f"r2://{self.bucket}/{key}", key=key, bucket=self.bucket, size=len(data)
        )

    def get(self, uri: str) -> bytes:
        bucket, key = self._parse(uri)
        return self.client().get_object(Bucket=bucket, Key=key)["Body"].read()

    def presign_get(
        self, uri: str, *, expires: int = 900, download_name: Optional[str] = None
    ) -> Optional[str]:
        bucket, key = self._parse(uri)
        params: dict = {"Bucket": bucket, "Key": key}
        if download_name:
            # Force the saved filename even though the URL is cross-origin (the
            # browser would otherwise name the file after the object key).
            params["ResponseContentDisposition"] = content_disposition(download_name)
        return self.client().generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires
        )

    def presign_put(
        self, key: str, *, content_type: Optional[str] = None, expires: int = 900
    ) -> Optional[str]:
        params: dict = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return self.client().generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires
        )

    def ensure_bucket(self) -> None:
        try:
            self.client().head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client().create_bucket(Bucket=self.bucket)
                log.info("Created R2 bucket %s", self.bucket)
            except Exception as e:  # noqa: BLE001
                log.warning("Could not ensure R2 bucket %s: %s", self.bucket, e)
