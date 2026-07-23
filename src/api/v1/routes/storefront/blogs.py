"""Storefront blog endpoints (public, published-only).

Matches the contract numu-storefront's ``src/lib/blogs.ts`` was built
against (mounted under ``/storefront/store/{store_id}``):

    GET /blogs                              → list[BlogSummary]
    GET /blogs/{handle}                     → BlogSummary
    GET /blogs/{handle}/articles            → list[ArticleSummary]
    GET /blogs/{blog}/articles/{handle}     → ArticleDetail

Text fields are bilingual dicts (``{en, ar}``) like the pages endpoints —
the host picks the visitor's language. Drafts/scheduled/archived articles
and unpublished blogs never appear here. A renamed article's OLD handle
still resolves (via ``previous_handles``): the payload carries the current
``handle`` so the host can 301 to the canonical URL.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from src.api.dependencies.repositories import (
    get_article_repository,
    get_blog_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.core.entities.blog import Article, Blog
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories.blog_repository import (
    ArticleRepository,
    BlogRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository

router = APIRouter()


def _blog_summary(blog: Blog) -> dict[str, Any]:
    return {
        "handle": blog.handle,
        "title": blog.title or {},
        "description": blog.description or {},
    }


def _article_summary(article: Article) -> dict[str, Any]:
    return {
        "handle": article.handle,
        "title": article.title or {},
        "excerpt": article.excerpt or {},
        "image_url": article.image_url,
        "published_at": (
            article.published_at.isoformat() if article.published_at else None
        ),
        "author": article.author,
        "tags": list(article.tags or []),
    }


def _article_detail(article: Article, blog: Blog) -> dict[str, Any]:
    return {
        **_article_summary(article),
        "body": article.body or {},
        "seo": article.seo or {},
        "blog": _blog_summary(blog),
    }


async def _require_store(store_repo: StoreRepository, store_id: UUID):
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))
    return store


async def _require_blog(blog_repo: BlogRepository, store_id: UUID, handle: str) -> Blog:
    blog = await blog_repo.get_by_handle(store_id, handle)
    if not blog or not blog.is_published:
        raise EntityNotFoundError("Blog", handle)
    return blog


@router.get(
    "/blogs",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="List a store's published blogs",
    operation_id="list_store_blogs_public",
)
async def list_store_blogs_public(
    store_id: Annotated[UUID, Path(description="Store ID")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
) -> SuccessResponse[list[dict[str, Any]]]:
    await _require_store(store_repo, store_id)
    blogs = await blog_repo.get_by_store(store_id, include_unpublished=False)
    return SuccessResponse(
        data=[_blog_summary(b) for b in blogs],
        message="Blogs retrieved successfully",
    )


@router.get(
    "/blogs/{handle}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Get a published blog by handle",
    operation_id="get_store_blog_public",
)
async def get_store_blog_public(
    store_id: Annotated[UUID, Path(description="Store ID")],
    handle: Annotated[str, Path(description="Blog handle")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
) -> SuccessResponse[dict[str, Any]]:
    await _require_store(store_repo, store_id)
    blog = await _require_blog(blog_repo, store_id, handle)
    return SuccessResponse(
        data=_blog_summary(blog), message="Blog retrieved successfully"
    )


@router.get(
    "/blogs/{handle}/articles",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="List a blog's published articles",
    operation_id="list_store_articles_public",
)
async def list_store_articles_public(
    store_id: Annotated[UUID, Path(description="Store ID")],
    handle: Annotated[str, Path(description="Blog handle")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
) -> SuccessResponse[list[dict[str, Any]]]:
    await _require_store(store_repo, store_id)
    blog = await _require_blog(blog_repo, store_id, handle)
    articles = await article_repo.list_published(blog.id, skip=skip, limit=limit)
    return SuccessResponse(
        data=[_article_summary(a) for a in articles],
        message="Articles retrieved successfully",
    )


@router.get(
    "/blogs/{handle}/articles/{article_handle}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Get a published article",
    operation_id="get_store_article_public",
)
async def get_store_article_public(
    store_id: Annotated[UUID, Path(description="Store ID")],
    handle: Annotated[str, Path(description="Blog handle")],
    article_handle: Annotated[str, Path(description="Article handle")],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    blog_repo: Annotated[BlogRepository, Depends(get_blog_repository)],
    article_repo: Annotated[ArticleRepository, Depends(get_article_repository)],
) -> SuccessResponse[dict[str, Any]]:
    await _require_store(store_repo, store_id)
    blog = await _require_blog(blog_repo, store_id, handle)
    article = await article_repo.get_by_handle(blog.id, article_handle)
    if not article:
        # Renamed? Resolve the former handle so old links keep working —
        # the payload's `handle` is the CURRENT one; the host 301s to it.
        article = await article_repo.find_by_previous_handle(blog.id, article_handle)
    if not article or not article.is_publicly_visible:
        raise EntityNotFoundError("Article", article_handle)
    return SuccessResponse(
        data=_article_detail(article, blog),
        message="Article retrieved successfully",
    )
