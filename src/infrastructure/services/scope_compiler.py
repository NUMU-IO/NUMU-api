"""Scope compiler for permission scope resolution."""

from uuid import UUID


class ScopeType:
    """Scope type enum values."""

    ALL = "all"
    OWN = "own"
    ASSIGNED = "assigned"
    RESOURCE = "resource"


class ScopeCompiler:
    """Compiles permission scope_qualifier to SQLAlchemy filters.

    Converts scope_type and scope_qualifier JSONB into filter conditions
    for resource-level access control.
    """

    @staticmethod
    def compile(
        scope_type: str,
        scope_qualifier: dict | None,
        user_id: UUID,
        resource_model,
        owner_field: str = "owner_id",
        assigned_field: str = "assigned_to_id",
    ) -> list:
        """Compile scope to filter conditions.

        Args:
            scope_type: ALL, OWN, ASSIGNED, RESOURCE
            scope_qualifier: JSONB with resource_ids or tags
            user_id: Current user ID
            resource_model: SQLAlchemy model class
            owner_field: Field name for owner
            assigned_field: Field name for assignee

        Returns:
            List of SQLAlchemy filter conditions
        """
        if scope_type == ScopeType.ALL:
            return []

        if scope_type == ScopeType.OWN:
            return [getattr(resource_model, owner_field) == user_id]

        if scope_type == ScopeType.ASSIGNED:
            return [getattr(resource_model, assigned_field) == user_id]

        if scope_type == ScopeType.RESOURCE:
            if not scope_qualifier:
                return []
            resource_ids = scope_qualifier.get("resource_ids", [])
            tags = scope_qualifier.get("tags", [])
            conditions = []
            if resource_ids:
                conditions.append(resource_model.id.in_(resource_ids))
            if tags and hasattr(resource_model, "tags"):
                conditions.append(resource_model.tags.overlap(tags))
            return conditions

        return []

    @staticmethod
    def check_access(
        scope_type: str,
        scope_qualifier: dict | None,
        user_id: UUID,
        resource,
        owner_field: str = "owner_id",
        assigned_field: str = "assigned_to_id",
    ) -> bool:
        """Check if user has access to a specific resource.

        Args:
            scope_type: ALL, OWN, ASSIGNED, RESOURCE
            scope_qualifier: JSONB with resource_ids or tags
            user_id: Current user ID
            resource: Resource object
            owner_field: Field name for owner
            assigned_field: Field name for assignee

        Returns:
            True if access is allowed
        """
        if scope_type == ScopeType.ALL:
            return True

        owner = getattr(resource, owner_field, None)
        if scope_type == ScopeType.OWN:
            return owner == user_id

        assigned = getattr(resource, assigned_field, None)
        if scope_type == ScopeType.ASSIGNED:
            return assigned == user_id

        if scope_type == ScopeType.RESOURCE:
            if not scope_qualifier:
                return False
            resource_ids = scope_qualifier.get("resource_ids", [])
            resource_id = getattr(resource, "id", None)
            if resource_id in resource_ids:
                return True
            tags = scope_qualifier.get("tags", [])
            resource_tags = getattr(resource, "tags", [])
            return bool(set(tags) & set(resource_tags))

        return False


class ScopeValidator:
    """Validates scope_qualifier JSONB structures."""

    @staticmethod
    def validate_resource_scope(qualifier: dict) -> tuple[bool, str | None]:
        """Validate RESOURCE scope qualifier.

        Expected structure:
        {
            "resource_ids": ["uuid1", "uuid2"],
            "tags": ["tag1", "tag2"]
        }

        Returns:
            (is_valid, error_message)
        """
        if not isinstance(qualifier, dict):
            return False, "Scope qualifier must be a JSON object"

        resource_ids = qualifier.get("resource_ids")
        if resource_ids is not None:
            if not isinstance(resource_ids, list):
                return False, "resource_ids must be an array"
            for rid in resource_ids:
                if not isinstance(rid, str):
                    return False, "resource_ids must contain strings"

        tags = qualifier.get("tags")
        if tags is not None:
            if not isinstance(tags, list):
                return False, "tags must be an array"
            for tag in tags:
                if not isinstance(tag, str):
                    return False, "tags must contain strings"

        if not resource_ids and not tags:
            return False, "At least one of resource_ids or tags required"

        return True, None

    @staticmethod
    def is_valid_scope_type(scope_type: str) -> bool:
        """Check if scope type is valid."""
        return scope_type in (
            ScopeType.ALL,
            ScopeType.OWN,
            ScopeType.ASSIGNED,
            ScopeType.RESOURCE,
        )
