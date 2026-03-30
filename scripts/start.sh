#!/bin/bash
# claude-slack-bridge LaunchAgent 起動
set -euo pipefail

LABEL="com.user.claude-slack-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if launchctl list "$LABEL" &>/dev/null; then
    echo "既に起動中です" >&2
    exit 1
fi

if [ ! -f "$PLIST_PATH" ]; then
    echo "Error: $PLIST_PATH が見つかりません。先に scripts/install.sh を実行してください" >&2
    exit 1
fi

launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
echo "起動しました"
