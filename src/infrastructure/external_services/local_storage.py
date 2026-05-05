"""Local filesystem storage service for development.

Saves files to a local directory and serves them via the API's
/uploads/ static route. Used automatically when Cloudflare R2
credentials are not configured.
"""

from pathlib import Path
from uuid import uuid4

from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
    UploadedFile,
)

# Base directory for local uploads (project root / uploads)
UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads"
# Base URL served by FastAPI static mount
LOCAL_BASE_URL = "http://localhost:8021/uploads"


class LocalStorageService(IStorageService):
    """File storage backed by the local filesystem.

    Suitable for development only. Files are stored under
    ``<project_root>/uploads/<bucket>/<unique_id>.<ext>``.
    """

    def __init__(self, base_dir: Path | None = None, base_url: str | None = None):
        self.base_dir = base_dir or UPLOAD_DIR
        self.base_url = base_url or LOCAL_BASE_URL
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _generate_key(self, filename: str, bucket: StorageBucket) -> str:
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
        key = self._generate_key(filename, bucket)
        file_path = self.base_dir / key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(file_content)
        return UploadedFile(
            key=key,
            url=self.get_public_url(key),
            size=len(file_content),
            content_type=content_type,
        )

    async def delete_file(self, key: str) -> bool:
        file_path = self.base_dir / key
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    async def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        return self.get_public_url(key)

    async def file_exists(self, key: str) -> bool:
        return (self.base_dir / key).exists()

    def get_public_url(self, key: str) -> str:
        return f"{self.base_url}/{key}"

    async def get_object_bytes(self, key: str) -> tuple[bytes, str | None]:
        file_path = self.base_dir / key
        if not file_path.exists():
            raise FileNotFoundError(key)
        # Local mode doesn't track content-type on disk; the route
        # falls back to magic-byte sniffing when this is None.
        return file_path.read_bytes(), None

    async def list_files(self, prefix: str) -> list[dict]:
        base = self.base_dir / prefix
        if not base.exists():
            return []
        out: list[dict] = []
        for path in base.rglob("*"):
            if path.is_file():
                stat = path.stat()
                rel_key = str(path.relative_to(self.base_dir)).replace("\\", "/")
                out.append({
                    "key": rel_key,
                    "url": self.get_public_url(rel_key),
                    "size": stat.st_size,
                    "last_modified": "",
                })
        return out
