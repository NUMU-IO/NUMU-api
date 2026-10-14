"""Social import must not fetch look-alike hosts or redirect into internal ones."""

import httpx
import pytest

from src.core.url_guard import UnsafeUrlError
from src.infrastructure.external_services.meta.url_scraper import (
    detect_platform,
    get_public,
)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.facebook.com/page/posts/1", "facebook"),
        ("https://fb.com/photo?fbid=1", "facebook"),
        ("https://www.instagram.com/p/ABC/", "instagram"),
        ("https://facebook.com.attacker.tld/x", None),
        ("https://notfacebook.com/x", None),
        ("https://instagram.com.evil.io/p/ABC/", None),
    ],
)
def test_detect_platform_matches_exact_hosts(url, platform):
    assert detect_platform(url) == platform


async def test_get_public_refuses_a_redirect_to_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "8.8.8.8":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        raise AssertionError(f"fetched {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnsafeUrlError):
            await get_public(client, "http://8.8.8.8/image.jpg")


async def test_get_public_follows_a_public_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(200, content=b"img")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await get_public(client, "http://8.8.8.8/a")

    assert resp.content == b"img"
