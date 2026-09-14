"""BlobStore over any S3 API (MinIO locally), via aiobotocore.

A client is opened per call — cheap, and keeps this stateless. ``ensure_bucket`` is a
local-bootstrap.
"""

from typing import Any

import aiobotocore.session
from botocore.exceptions import ClientError

from specter.core.errors import NotFoundError
from specter.core.settings import S3Settings

_MISSING = {"NoSuchKey", "NoSuchBucket", "404"}


class MinioBlobStore:
    def __init__(self, s3: S3Settings) -> None:
        self._cfg = s3
        self._session = aiobotocore.session.get_session()

    def _client(self) -> Any:
        return self._session.create_client(
            "s3",
            endpoint_url=self._cfg.endpoint_url,
            aws_access_key_id=self._cfg.access_key,
            aws_secret_access_key=self._cfg.secret_key.get_secret_value(),
            region_name=self._cfg.region,
            use_ssl=self._cfg.secure,
        )

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        async with self._client() as client:
            await client.put_object(
                Bucket=self._cfg.bucket, Key=key, Body=data, ContentType=content_type
            )
        return key

    async def get(self, key: str) -> bytes:
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self._cfg.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    raise NotFoundError(f"blob not found: {key}") from exc
                raise
            async with response["Body"] as body:
                return bytes(await body.read())

    async def presigned_url(self, key: str, *, ttl_s: int = 900) -> str:
        async with self._client() as client:
            url = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._cfg.bucket, "Key": key},
                ExpiresIn=ttl_s,
            )
            return str(url)

    async def ensure_bucket(self) -> None:
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self._cfg.bucket)
            except ClientError:
                await client.create_bucket(Bucket=self._cfg.bucket)

    async def __aenter__(self) -> "MinioBlobStore":
        await self.ensure_bucket()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None
