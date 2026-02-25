"""共有フィクスチャ — bridge.py のインポートに必要なモックを設定。

bridge.py はモジュールレベルで環境変数と Slack SDK を必要とするため、
テスト時はこれらをモックして安全にインポートする。
"""

import os
import sys

import pytest
from unittest.mock import patch, MagicMock

# ── Slack SDK モジュールのモック ──
# patch.dict はコンテキスト終了時に復元してしまうため、
# sys.modules に直接差し込んで永続化する。
# これにより各テストファイルの `import bridge` が同一モジュールを参照する。
_slack_mocks = {
    "slack_bolt": MagicMock(),
    "slack_bolt.adapter": MagicMock(),
    "slack_bolt.adapter.socket_mode": MagicMock(),
    "slack_sdk": MagicMock(),
}
for mod_name, mock_obj in _slack_mocks.items():
    sys.modules.setdefault(mod_name, mock_obj)

_env_vars = {
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "SLACK_APP_TOKEN": "xapp-test-token",
    "ADMIN_SLACK_USER_ID": "UADMIN",
    "SLACK_ALLOWED_USERS": "",
    "SLACK_ALLOWED_CHANNELS": "",
}

with patch.dict(os.environ, _env_vars):
    import bridge

# bridge モジュールを sys.modules に確実に残す（他のテストファイルの import bridge と同一オブジェクト）
sys.modules["bridge"] = bridge

# BOT_USER_ID を事前設定（get_bot_user_id() が API を呼ばないようにする）
bridge.BOT_USER_ID = "UBOTTEST"


# ── ファクトリフィクスチャ ──

@pytest.fixture
def make_task():
    """Task ファクトリ"""
    def _factory(id=1, prompt="test", **kwargs):
        return bridge.Task(id=id, prompt=prompt, **kwargs)
    return _factory


@pytest.fixture
def make_session():
    """Session ファクトリ"""
    def _factory(thread_ts="ts1", channel_id="C123", **kwargs):
        return bridge.Session(thread_ts=thread_ts, channel_id=channel_id, **kwargs)
    return _factory


@pytest.fixture
def make_project():
    """Project ファクトリ"""
    def _factory(channel_id="C123", root_dir="/tmp/test", **kwargs):
        return bridge.Project(channel_id=channel_id, root_dir=root_dir, **kwargs)
    return _factory
