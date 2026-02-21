"""S3-compatible storage service implementation (MinIO / Cloudflare R2 / AWS S3)."""

from io import BytesIO
from uuid import uuid4

import boto3
from botocore.config import Config

from src.config import settings
from src.core.exceptions import ExternalServiceError
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
    UploadedFile,
)


class CloudflareR2StorageService(IStorageService):
    """Storage service implementation using any S3-compatible backend."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        bucket_name: str | None = None,
    ) -> None:
        # Prefer new s3_* settings, fall back to legacy r2_* settings
        self.endpoint_url = (
            endpoint_url
            or settings.s3_endpoint_url
            or (
                f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
                if settings.r2_account_id
                else None
            )
        )
        self.access_key_id = (
            access_key_id or settings.s3_access_key_id or settings.r2_access_key_id
        )
        self.secret_access_key = (
            secret_access_key
            or settings.s3_secret_access_key
            or settings.r2_secret_access_key
        )
        self.bucket_name = (
            bucket_name or settings.s3_bucket_name or settings.r2_bucket_name
        )
        self.public_url = settings.s3_public_url or settings.r2_public_url

        if self.endpoint_url and self.access_key_id and self.secret_access_key:
            self.client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=settings.s3_region,
                config=Config(signature_version="s3v4"),
            )
        else:
            self.client = None

    def _generate_key(
        self,
        filename: str,
        bucket: StorageBucket,
    ) -> str:
        """Generate unique key for file."""
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        unique_id = uuid4().hex[:12]
        safe_filename = f"{unique_id}.{ext}" if ext else unique_id
        return f"{bucket.value}/{safe_filename}"

    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        bucket: StorageBucket = StorageBucket.PRODUCTS,
    ) -> UploadedFile:
        """Upload a file to S3-compatible storage."""
        if not self.client:
            raise ExternalServiceError("S3 Storage", "Storage not configured")

        try:
            key = self._generate_key(filename, bucket)

            self.client.upload_fileobj(
                BytesIO(file_content),
                self.bucket_name,
                key,
                ExtraArgs={"ContentType": content_type},
            )

            return UploadedFile(
                key=key,
                url=self.get_public_url(key),
                size=len(file_content),
                content_type=content_type,
            )
        except Exception as e:
            raise ExternalServiceError("S3 Storage", str(e))

    async def delete_file(self, key: str) -> bool:
        """Delete a file from S3-compatible storage."""
        if not self.client:
            raise ExternalServiceError("S3 Storage", "Storage not configured")

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as e:
            raise ExternalServiceError("S3 Storage", str(e))

    async def get_signed_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> str:
        """Get a signed URL for a file."""
        if not self.client:
            raise ExternalServiceError("S3 Storage", "Storage not configured")

        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_in,
            )
            return url
        except Exception as e:
            raise ExternalServiceError("S3 Storage", str(e))

    async def file_exists(self, key: str) -> bool:
        """Check if a file exists in S3-compatible storage."""
        if not self.client:
            raise ExternalServiceError("S3 Storage", "Storage not configured")

        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception:
            return False

    def get_public_url(self, key: str) -> str:
        """Get the public URL for a file."""
        if self.public_url:
            return f"{self.public_url}/{key}"
        return f"{self.endpoint_url}/{self.bucket_name}/{key}"
