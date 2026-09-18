import httpx
import pytest

from reddit_archiver.config import BackendConfig, RateLimitConfig
from reddit_archiver.sources.arctic_shift import ArcticShiftBackend


def _backend_with_transport(handler) -> ArcticShiftBackend:
    cfg = BackendConfig(rate_limit=RateLimitConfig(min_interval_seconds=0, max_workers=2))
    backend = ArcticShiftBackend(cfg)
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return backend


@pytest.mark.asyncio
async def test_search_posts_returns_next_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/posts/search"
        assert request.url.params["sort"] == "asc"
        return httpx.Response(
            200,
            json={"data": [
                {"id": "a", "created_utc": 100},
                {"id": "b", "created_utc": 200},
            ]},
            headers={"X-RateLimit-Remaining": "100"},
        )

    backend = _backend_with_transport(handler)
    page = await backend.search_posts("python", after=0, before=1000, cursor=None)
    assert [i["id"] for i in page.items] == ["a", "b"]
    assert page.next_cursor == 200          # last item's created_utc
    assert page.exhausted is False
    await backend.aclose()


@pytest.mark.asyncio
async def test_empty_page_is_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    backend = _backend_with_transport(handler)
    page = await backend.search_comments("python", after=0, before=1000)
    assert page.items == []
    assert page.next_cursor is None
    assert page.exhausted is True
    await backend.aclose()


@pytest.mark.asyncio
async def test_retries_then_raises_on_persistent_5xx():
    from reddit_archiver.sources.base import BackendError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    cfg = BackendConfig(
        rate_limit=RateLimitConfig(min_interval_seconds=0, max_retries=2,
                                   backoff_base_seconds=0, backoff_max_seconds=0)
    )
    backend = ArcticShiftBackend(cfg)
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(BackendError):
        await backend.search_posts("python", after=0, before=10)
    await backend.aclose()
