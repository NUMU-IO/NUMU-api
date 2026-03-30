"""Use case: Fetch posts from a connected social account."""

from uuid import UUID

from src.core.entities.social_post import SocialPost
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.social_connection_repository import (
    ISocialConnectionRepository,
)
from src.core.interfaces.repositories.social_post_repository import (
    ISocialPostRepository,
)
from src.infrastructure.external_services.meta import MetaSocialService


class FetchSocialPostsUseCase:
    """Fetch posts from the social platform and store them locally."""

    def __init__(
        self,
        connection_repo: ISocialConnectionRepository,
        post_repo: ISocialPostRepository,
        meta_service: MetaSocialService,
    ) -> None:
        self.connection_repo = connection_repo
        self.post_repo = post_repo
        self.meta_service = meta_service

    async def execute(
        self,
        connection_id: UUID,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[SocialPost], str | None]:
        """Fetch posts from the platform and upsert into local DB.

        Returns (posts, next_cursor).
        """
        connection = await self.connection_repo.get_by_id(connection_id)
        if not connection:
            raise EntityNotFoundError("SocialConnection", str(connection_id))

        # Fetch from platform API (mock for MVP)
        fetched, next_cursor = await self.meta_service.fetch_posts(
            platform=connection.platform,
            access_token=connection.access_token_encrypted or "",
            limit=limit,
            cursor=cursor,
        )

        posts: list[SocialPost] = []
        for fp in fetched:
            # Check if we already have this post
            existing = await self.post_repo.get_by_platform_post_id(
                connection_id, fp.platform_post_id
            )
            if existing:
                posts.append(existing)
                continue

            post = SocialPost(
                social_connection_id=connection_id,
                store_id=connection.store_id,
                tenant_id=connection.tenant_id,
                platform_post_id=fp.platform_post_id,
                image_url=fp.image_url,
                caption=fp.caption,
                likes=fp.likes,
                comments=fp.comments,
                posted_at=fp.posted_at,
                suggested_name=fp.suggested_name,
                suggested_name_ar=fp.suggested_name_ar,
                suggested_price=fp.suggested_price,
            )
            created = await self.post_repo.create(post)
            posts.append(created)

        # Update last_synced_at on the connection
        connection.touch()
        await self.connection_repo.update(connection)

        return posts, next_cursor
