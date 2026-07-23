"""Blog + Article (merchant content marketing) routes nested under stores.

URL: /stores/{store_id}/blogs
"""

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import (
    get_article_repository,
    get_blog_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.blog import (
    ArticleResponse,
    BlogResponse,
    CreateArticleRequest,
    CreateBlogRequest,
    UpdateArticleRequest,
    UpdateBlogRequest,
)
from src.core.entities.blog import Article, ArticleStatus, Blog
from src.core.entities.store import Store
from src.infrastructure.repositories.blog_repository import (
    ArticleRepository,
    BlogRepository,
)

router = APIRouter(prefix="/{store_id}/blogs")

_HANDLE_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(handle: str, fallback: str) -> str:
    """Normalize a handle to URL-safe lowercase-with-dashes."""
    s = handle.strip().lower().replace(" ", "-")
    s = _HANDLE_RE.sub("", s)
    return s.strip("-") or fallback


def _parse_dt(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be an ISO 8601 datetime",
        )
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _blog_response(entity: Blog, article_count: int = 0) -> BlogResponse:
    return BlogResponse(
        id=str(entity.id),
        store_id=str(entity.store_id),
        handle=entity.handle,
        title=entity.title or {},
        description=entity.description or {},
        is_published=entity.is_published,
        article_count=article_count,
        created_at=str(entity.created_at),
        updated_at=str(entity.updated_at),
    )


def _article_response(entity: Article, blog_handle: str) -> ArticleResponse:
    return ArticleResponse(
        id=str(entity.id),
        store_id=str(entity.store_id),
        blog_id=str(entity.blog_id),
        blog_handle=blog_handle,
        handle=entity.handle,
        title=entity.title or {},
        excerpt=entity.excerpt or {},
        body=entity.body or {},
        image_url=entity.image_url,
        author=entity.author,
        tags=list(entity.tags or []),
        seo=entity.seo or {},
        status=entity.status.value,
        published_at=entity.published_at.isoformat() if entity.published_at else None,
        scheduled_at=entity.scheduled_at.isoformat() if entity.scheduled_at else None,
        previous_handles=list(entity.previous_handles or []),
        created_at=str(entity.created_at),
        updated_at=str(entity.updated_at),
    )


async def _revalidate(
    store: Store, blog_handle: str, article_handle: str | None = None
) -> None:
    """Best-effort: bust the storefront's cached blog fetches after a change."""
    if not store.subdomain:
        return
    try:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_blog_change,
        )

        await revalidate_on_blog_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
            blog_handle=blog_handle,
            article_handle=article_handle,
        )
    except Exception:
        # Revalidation is non-fatal — the ISR window self-heals.
        pass


def _apply_status_transition(
    article: Article, new_status: str, scheduled_at_raw: str | None
) -> None:
    """Move an article through its lifecycle, keeping the invariants:

    - `scheduled` always carries a scheduled_at.
    - entering `published` stamps published_at once (re-publishing after an
      archive keeps the original date, Shopify-style) and clears scheduled_at.
    """
    target = ArticleStatus(new_status)
    if target == ArticleStatus.SCHEDULED:
        raw = scheduled_at_raw or (
            article.scheduled_at.isoformat() if article.scheduled_at else None
        )
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status=scheduled requires scheduled_at",
            )
        article.scheduled_at = (
            _parse_dt(raw, "scheduled_at") if isinstance(raw, str) else raw
        )
    if target == ArticleStatus.PUBLISHED:
        if article.published_at is None:
            article.published_at = datetime.now(UTC)
        article.scheduled_at = None
    article.status = target


# ── Blogs ────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=SuccessResponse[list[BlogResponse]],
    summary="List blogs",
    operation_id="list_blogs",
)
async def list_blogs(
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
):
    """List all blogs for the store with article counts."""
    blogs = await blog_repo.get_by_store(store.id, include_unpublished=True)
    counts = await article_repo.count_by_blog_for_store(store.id)
    return SuccessResponse(
        data=[_blog_response(b, counts.get(b.id, 0)) for b in blogs],
        message="Blogs retrieved successfully",
    )


@router.post(
    "/",
    response_model=SuccessResponse[BlogResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create blog",
    operation_id="create_blog",
)
async def create_blog(
    request: CreateBlogRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
):
    """Create a new blog. The handle is immutable after creation."""
    handle = _slugify(request.handle, "blog")
    if await blog_repo.get_by_handle(store.id, handle):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A blog with handle '{handle}' already exists",
        )
    blog = await blog_repo.create(
        Blog(
            store_id=store.id,
            tenant_id=store.tenant_id,
            handle=handle,
            title=request.title,
            description=request.description,
            is_published=request.is_published,
        )
    )
    await _revalidate(store, handle)
    return SuccessResponse(data=_blog_response(blog), message="Blog created")


@router.get(
    "/{blog_handle}",
    response_model=SuccessResponse[BlogResponse],
    summary="Get blog by handle",
    operation_id="get_blog",
)
async def get_blog(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
):
    blog = await blog_repo.get_by_handle(store.id, blog_handle)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    counts = await article_repo.count_by_blog_for_store(store.id)
    return SuccessResponse(
        data=_blog_response(blog, counts.get(blog.id, 0)),
        message="Blog retrieved successfully",
    )


@router.patch(
    "/{blog_handle}",
    response_model=SuccessResponse[BlogResponse],
    summary="Update blog",
    operation_id="update_blog",
)
async def update_blog(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    request: UpdateBlogRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
):
    blog = await blog_repo.get_by_handle(store.id, blog_handle)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    if request.title is not None:
        blog.title = request.title
    if request.description is not None:
        blog.description = request.description
    if request.is_published is not None:
        blog.is_published = request.is_published
    blog = await blog_repo.update(blog)
    await _revalidate(store, blog.handle)
    return SuccessResponse(data=_blog_response(blog), message="Blog updated")


@router.delete(
    "/{blog_handle}",
    response_model=SuccessResponse[dict],
    summary="Delete blog (and all its articles)",
    operation_id="delete_blog",
)
async def delete_blog(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
):
    blog = await blog_repo.get_by_handle(store.id, blog_handle)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    await blog_repo.delete(blog.id)
    await _revalidate(store, blog_handle)
    return SuccessResponse(data={"deleted": True}, message="Blog deleted")


# ── Articles ─────────────────────────────────────────────────────────────────


async def _require_blog(
    store: Store, blog_handle: str, blog_repo: BlogRepository
) -> Blog:
    blog = await blog_repo.get_by_handle(store.id, blog_handle)
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found")
    return blog


@router.get(
    "/{blog_handle}/articles",
    response_model=SuccessResponse[list[ArticleResponse]],
    summary="List articles",
    operation_id="list_articles",
)
async def list_articles(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", pattern=r"^(draft|scheduled|published|archived)$"),
    ] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """List a blog's articles (all statuses; filter with ?status=)."""
    blog = await _require_blog(store, blog_handle, blog_repo)
    articles = await article_repo.get_by_blog(
        blog.id, status=status_filter, skip=skip, limit=limit
    )
    return SuccessResponse(
        data=[_article_response(a, blog.handle) for a in articles],
        message="Articles retrieved successfully",
    )


@router.post(
    "/{blog_handle}/articles",
    response_model=SuccessResponse[ArticleResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create article",
    operation_id="create_article",
)
async def create_article(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    request: CreateArticleRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
):
    blog = await _require_blog(store, blog_handle, blog_repo)

    seed = request.handle or request.title.get("en") or request.title.get("ar") or ""
    handle = _slugify(seed, "article")
    if await article_repo.get_by_handle(blog.id, handle):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An article with handle '{handle}' already exists in this blog",
        )

    article = Article(
        store_id=store.id,
        tenant_id=store.tenant_id,
        blog_id=blog.id,
        handle=handle,
        title=request.title,
        excerpt=request.excerpt,
        body=request.body,
        image_url=request.image_url,
        author=request.author,
        tags=request.tags,
        seo=request.seo,
    )
    _apply_status_transition(article, request.status, request.scheduled_at)
    article = await article_repo.create(article)
    await _revalidate(store, blog.handle, article.handle)
    return SuccessResponse(
        data=_article_response(article, blog.handle), message="Article created"
    )


@router.get(
    "/{blog_handle}/articles/{article_handle}",
    response_model=SuccessResponse[ArticleResponse],
    summary="Get article",
    operation_id="get_article",
)
async def get_article(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    article_handle: Annotated[str, Path(description="Article handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
):
    blog = await _require_blog(store, blog_handle, blog_repo)
    article = await article_repo.get_by_handle(blog.id, article_handle)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return SuccessResponse(
        data=_article_response(article, blog.handle),
        message="Article retrieved successfully",
    )


@router.patch(
    "/{blog_handle}/articles/{article_handle}",
    response_model=SuccessResponse[ArticleResponse],
    summary="Update article (content, rename, lifecycle)",
    operation_id="update_article",
)
async def update_article(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    article_handle: Annotated[str, Path(description="Article handle")],
    request: UpdateArticleRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
):
    blog = await _require_blog(store, blog_handle, blog_repo)
    article = await article_repo.get_by_handle(blog.id, article_handle)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    old_handle = article.handle
    if request.handle is not None:
        new_handle = _slugify(request.handle, article.handle)
        if new_handle != article.handle:
            existing = await article_repo.get_by_handle(blog.id, new_handle)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"An article with handle '{new_handle}' already exists",
                )
            # Remember every former handle so inbound links keep resolving.
            history = [h for h in article.previous_handles if h != new_handle]
            if article.handle not in history:
                history.append(article.handle)
            article.previous_handles = history
            article.handle = new_handle

    if request.title is not None:
        article.title = request.title
    if request.excerpt is not None:
        article.excerpt = request.excerpt
    if request.body is not None:
        article.body = request.body
    if request.image_url is not None:
        article.image_url = request.image_url or None
    if request.author is not None:
        article.author = request.author or None
    if request.tags is not None:
        article.tags = request.tags
    if request.seo is not None:
        article.seo = request.seo
    if request.status is not None:
        _apply_status_transition(article, request.status, request.scheduled_at)
    elif request.scheduled_at is not None:
        article.scheduled_at = _parse_dt(request.scheduled_at, "scheduled_at")

    article = await article_repo.update(article)
    await _revalidate(store, blog.handle, old_handle)
    if article.handle != old_handle:
        await _revalidate(store, blog.handle, article.handle)
    return SuccessResponse(
        data=_article_response(article, blog.handle), message="Article updated"
    )


@router.delete(
    "/{blog_handle}/articles/{article_handle}",
    response_model=SuccessResponse[dict],
    summary="Delete article",
    operation_id="delete_article",
)
async def delete_article(
    blog_handle: Annotated[str, Path(description="Blog handle")],
    article_handle: Annotated[str, Path(description="Article handle")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
):
    blog = await _require_blog(store, blog_handle, blog_repo)
    article = await article_repo.get_by_handle(blog.id, article_handle)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    await article_repo.delete(article.id)
    await _revalidate(store, blog.handle, article_handle)
    return SuccessResponse(data={"deleted": True}, message="Article deleted")
