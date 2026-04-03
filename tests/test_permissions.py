"""Tests for the permission system."""

from __future__ import annotations

from code_review_agent.interactive.permissions import (
    PermissionManager,
    PermissionMode,
)


class TestPermissionManager:
    """Test permission checks for PR actions."""

    def test_ask_mode_requires_confirmation(self) -> None:
        pm = PermissionManager(mode=PermissionMode.ASK)
        decision = pm.check_post_comment(0.9, "SQL injection")
        assert decision.is_allowed
        assert decision.requires_confirmation

    def test_deny_mode_blocks(self) -> None:
        pm = PermissionManager(mode=PermissionMode.DENY)
        decision = pm.check_post_comment(0.9, "SQL injection")
        assert not decision.is_allowed
        assert pm.denial_count == 1

    def test_auto_mode_above_threshold(self) -> None:
        pm = PermissionManager(mode=PermissionMode.AUTO, auto_confidence_threshold=0.8)
        decision = pm.check_post_comment(0.9, "High confidence")
        assert decision.is_allowed
        assert not decision.requires_confirmation

    def test_auto_mode_below_threshold(self) -> None:
        pm = PermissionManager(mode=PermissionMode.AUTO, auto_confidence_threshold=0.8)
        decision = pm.check_post_comment(0.5, "Low confidence")
        assert decision.is_allowed
        assert decision.requires_confirmation

    def test_delete_denied_in_deny_mode(self) -> None:
        pm = PermissionManager(mode=PermissionMode.DENY)
        decision = pm.check_delete_comment()
        assert not decision.is_allowed

    def test_delete_requires_confirmation(self) -> None:
        pm = PermissionManager(mode=PermissionMode.ASK)
        decision = pm.check_delete_comment()
        assert decision.requires_confirmation

    def test_denial_rate(self) -> None:
        pm = PermissionManager()
        pm.record_user_approval()
        pm.record_user_approval()
        pm.record_user_denial("test")
        assert pm.denial_rate == 1 / 3

    def test_recent_denials(self) -> None:
        pm = PermissionManager()
        for i in range(15):
            pm.record_user_denial(f"action-{i}")
        assert len(pm.recent_denials) == 10
        assert pm.recent_denials[-1] == "action-14"
