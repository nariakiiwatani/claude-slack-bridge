"""アクセス制御関数のテスト。

対象: _is_user_allowed, _is_channel_allowed
モジュールレベルグローバル変数（ADMIN_SLACK_USER_ID, SLACK_ALLOWED_USERS,
SLACK_ALLOWED_CHANNELS）をパッチしてテストする。
"""

from unittest.mock import patch

import bridge


# ── _is_user_allowed ──────────────────────────────────────

class TestIsUserAllowed:
    def test_admin_always_allowed(self):
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', ''):
            assert bridge._is_user_allowed('UADMIN') is True

    def test_wildcard_allows_all(self):
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', '*'):
            assert bridge._is_user_allowed('UANY') is True

    def test_listed_user_allowed(self):
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', 'U1,U2,U3'):
            assert bridge._is_user_allowed('U2') is True

    def test_unlisted_user_denied(self):
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', 'U1,U2'):
            assert bridge._is_user_allowed('U99') is False

    def test_empty_allowlist_denies_non_admin(self):
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', ''):
            assert bridge._is_user_allowed('UOTHER') is False

    def test_whitespace_in_allowlist(self):
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', ' U1 , U2 '):
            assert bridge._is_user_allowed('U1') is True
            assert bridge._is_user_allowed('U2') is True

    def test_admin_with_wildcard(self):
        """管理者はワイルドカードと併用でも許可"""
        with patch.object(bridge, 'ADMIN_SLACK_USER_ID', 'UADMIN'), \
             patch.object(bridge, 'SLACK_ALLOWED_USERS', '*'):
            assert bridge._is_user_allowed('UADMIN') is True


# ── _is_channel_allowed ──────────────────────────────────

class TestIsChannelAllowed:
    def test_wildcard_allows_all(self):
        with patch.object(bridge, 'SLACK_ALLOWED_CHANNELS', '*'):
            assert bridge._is_channel_allowed('CANY') is True

    def test_listed_channel_allowed(self):
        with patch.object(bridge, 'SLACK_ALLOWED_CHANNELS', 'C1,C2'):
            assert bridge._is_channel_allowed('C1') is True

    def test_unlisted_channel_denied(self):
        with patch.object(bridge, 'SLACK_ALLOWED_CHANNELS', 'C1,C2'):
            assert bridge._is_channel_allowed('C99') is False

    def test_empty_allowlist_denies(self):
        with patch.object(bridge, 'SLACK_ALLOWED_CHANNELS', ''):
            assert bridge._is_channel_allowed('C1') is False

    def test_whitespace_handling(self):
        with patch.object(bridge, 'SLACK_ALLOWED_CHANNELS', ' C1 , C2 '):
            assert bridge._is_channel_allowed('C1') is True
            assert bridge._is_channel_allowed('C2') is True
            assert bridge._is_channel_allowed('C3') is False
