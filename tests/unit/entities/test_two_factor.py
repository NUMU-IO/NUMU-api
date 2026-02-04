"""Tests for Two-Factor Authentication entity."""

from datetime import datetime
from uuid import uuid4

import pytest

from src.core.entities.two_factor import (
    TwoFactorAuth,
    TwoFactorMethod,
    TwoFactorStatus,
)


class TestTwoFactorAuth:
    """Tests for the TwoFactorAuth entity."""

    def test_create_default_entity(self):
        """Test creating a TwoFactorAuth with defaults."""
        user_id = uuid4()
        two_factor = TwoFactorAuth(user_id=user_id)

        assert two_factor.user_id == user_id
        assert two_factor.method == TwoFactorMethod.TOTP
        assert two_factor.status == TwoFactorStatus.DISABLED
        assert two_factor.secret is None
        assert two_factor.backup_codes == []
        assert two_factor.backup_codes_remaining == 0
        assert two_factor.verified_at is None
        assert two_factor.last_used_at is None

    def test_is_enabled_property(self):
        """Test is_enabled property."""
        user_id = uuid4()

        # Disabled
        disabled = TwoFactorAuth(user_id=user_id, status=TwoFactorStatus.DISABLED)
        assert disabled.is_enabled is False

        # Pending
        pending = TwoFactorAuth(user_id=user_id, status=TwoFactorStatus.PENDING)
        assert pending.is_enabled is False

        # Enabled
        enabled = TwoFactorAuth(user_id=user_id, status=TwoFactorStatus.ENABLED)
        assert enabled.is_enabled is True

    def test_is_pending_property(self):
        """Test is_pending property."""
        user_id = uuid4()

        disabled = TwoFactorAuth(user_id=user_id, status=TwoFactorStatus.DISABLED)
        assert disabled.is_pending is False

        pending = TwoFactorAuth(user_id=user_id, status=TwoFactorStatus.PENDING)
        assert pending.is_pending is True

        enabled = TwoFactorAuth(user_id=user_id, status=TwoFactorStatus.ENABLED)
        assert enabled.is_pending is False

    def test_has_backup_codes_property(self):
        """Test has_backup_codes property."""
        user_id = uuid4()

        no_codes = TwoFactorAuth(user_id=user_id, backup_codes_remaining=0)
        assert no_codes.has_backup_codes is False

        has_codes = TwoFactorAuth(user_id=user_id, backup_codes_remaining=5)
        assert has_codes.has_backup_codes is True

    def test_enable_method(self):
        """Test enable() method."""
        user_id = uuid4()
        two_factor = TwoFactorAuth(
            user_id=user_id,
            status=TwoFactorStatus.PENDING,
            secret="TESTSECRET",
        )

        assert two_factor.verified_at is None

        two_factor.enable()

        assert two_factor.status == TwoFactorStatus.ENABLED
        assert two_factor.verified_at is not None
        assert isinstance(two_factor.verified_at, datetime)

    def test_disable_method(self):
        """Test disable() method clears all data."""
        user_id = uuid4()
        two_factor = TwoFactorAuth(
            user_id=user_id,
            status=TwoFactorStatus.ENABLED,
            secret="TESTSECRET",
            backup_codes=["hash1", "hash2", "hash3"],
            backup_codes_remaining=3,
            verified_at=datetime.utcnow(),
        )

        two_factor.disable()

        assert two_factor.status == TwoFactorStatus.DISABLED
        assert two_factor.secret is None
        assert two_factor.backup_codes == []
        assert two_factor.backup_codes_remaining == 0
        assert two_factor.verified_at is None

    def test_set_pending_method(self):
        """Test set_pending() method."""
        user_id = uuid4()
        two_factor = TwoFactorAuth(user_id=user_id)

        secret = "NEWSECRET123456"
        hashed_codes = ["hash1", "hash2", "hash3", "hash4", "hash5"]

        two_factor.set_pending(secret, hashed_codes)

        assert two_factor.status == TwoFactorStatus.PENDING
        assert two_factor.secret == secret
        assert two_factor.backup_codes == hashed_codes
        assert two_factor.backup_codes_remaining == 5

    def test_use_backup_code_method(self):
        """Test use_backup_code() method."""
        user_id = uuid4()
        codes = ["hash1", "hash2", "hash3"]
        two_factor = TwoFactorAuth(
            user_id=user_id,
            backup_codes=codes.copy(),
            backup_codes_remaining=3,
        )

        # Use a valid code
        result = two_factor.use_backup_code("hash2")
        assert result is True
        assert "hash2" not in two_factor.backup_codes
        assert two_factor.backup_codes_remaining == 2

        # Use an invalid code
        result = two_factor.use_backup_code("invalid")
        assert result is False
        assert two_factor.backup_codes_remaining == 2

    def test_record_use_method(self):
        """Test record_use() method."""
        user_id = uuid4()
        two_factor = TwoFactorAuth(user_id=user_id)

        assert two_factor.last_used_at is None

        two_factor.record_use()

        assert two_factor.last_used_at is not None
        assert isinstance(two_factor.last_used_at, datetime)

    def test_regenerate_backup_codes_method(self):
        """Test regenerate_backup_codes() method."""
        user_id = uuid4()
        old_codes = ["old1", "old2"]
        two_factor = TwoFactorAuth(
            user_id=user_id,
            backup_codes=old_codes,
            backup_codes_remaining=2,
        )

        new_codes = ["new1", "new2", "new3", "new4", "new5"]
        two_factor.regenerate_backup_codes(new_codes)

        assert two_factor.backup_codes == new_codes
        assert two_factor.backup_codes_remaining == 5
        assert "old1" not in two_factor.backup_codes

    def test_two_factor_method_enum(self):
        """Test TwoFactorMethod enum values."""
        assert TwoFactorMethod.TOTP.value == "totp"

    def test_two_factor_status_enum(self):
        """Test TwoFactorStatus enum values."""
        assert TwoFactorStatus.DISABLED.value == "disabled"
        assert TwoFactorStatus.PENDING.value == "pending"
        assert TwoFactorStatus.ENABLED.value == "enabled"
