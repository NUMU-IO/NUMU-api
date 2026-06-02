"""Asset-key namespace tests (Phase 1.1).

The image-library bug was that ``upload_file`` generated a ``{bucket}/<uuid>``
key and discarded the caller's ``customization/{store_id}/`` prefix — but the
library *list* endpoint queries that prefix, so freshly-uploaded images never
appeared. The fix: honour a caller-supplied ``key`` (after sanitisation), and
``sanitize_object_key`` guards against path-traversal so a key can never escape
its prefix / the uploads dir.
"""

import pytest

from src.core.interfaces.services.storage_service import (
    StorageBucket,
    sanitize_object_key,
)
from src.infrastructure.external_services.local_storage import LocalStorageService


class TestSanitizeObjectKey:
    def test_preserves_namespace_prefix(self):
        key = "customization/store-1/section_image_1.png"
        assert sanitize_object_key(key) == key

    def test_strips_surrounding_slashes(self):
        assert sanitize_object_key("/a/b/") == "a/b"

    def test_strips_dot_and_dotdot_segments(self):
        # `..` traversal segments are dropped so the key can't escape upward.
        assert sanitize_object_key("customization/../../etc/passwd") == (
            "customization/etc/passwd"
        )
        assert sanitize_object_key("a/./b") == "a/b"

    def test_normalises_backslashes_to_forward_slashes(self):
        assert sanitize_object_key("a\\b\\c.png") == "a/b/c.png"

    def test_all_separators_collapse_to_empty(self):
        assert sanitize_object_key("///") == ""
        assert sanitize_object_key("..") == ""


class TestLocalStorageKeyNamespace:
    def _svc(self, tmp_path) -> LocalStorageService:
        return LocalStorageService(base_dir=tmp_path, base_url="http://test/uploads")

    @pytest.mark.asyncio
    async def test_explicit_key_is_respected(self, tmp_path):
        svc = self._svc(tmp_path)
        out = await svc.upload_file(
            b"img-bytes",
            "img.png",
            "image/png",
            bucket=StorageBucket.STORES,
            key="customization/store-1/section_image_1.png",
        )
        # The caller's namespaced prefix survives (NOT bucket-prefixed) so the
        # library list query under `customization/{store}/` finds the object.
        assert out.key == "customization/store-1/section_image_1.png"
        assert out.url == (
            "http://test/uploads/customization/store-1/section_image_1.png"
        )
        assert (tmp_path / "customization" / "store-1" / "section_image_1.png").exists()

    @pytest.mark.asyncio
    async def test_no_key_falls_back_to_bucket_prefix(self, tmp_path):
        svc = self._svc(tmp_path)
        out = await svc.upload_file(
            b"x", "logo.png", "image/png", bucket=StorageBucket.STORES
        )
        assert out.key.startswith("stores/")
        assert out.key.endswith(".png")

    @pytest.mark.asyncio
    async def test_traversal_key_cannot_escape_uploads_dir(self, tmp_path):
        svc = self._svc(tmp_path)
        out = await svc.upload_file(
            b"x", "x.png", "image/png", key="customization/../../evil.png"
        )
        assert ".." not in out.key
        written = (tmp_path / out.key).resolve()
        assert str(written).startswith(str(tmp_path.resolve()))
