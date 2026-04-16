#!/bin/bash
# claude-slack-bridge LaunchAgent 再起動
set -euo pipefail

LABEL="com.user.claude-slack-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [ ! -f "$PLIST_PATH" ]; then
    echo "Error: $PLIST_PATH が見つかりません。先に scripts/install.sh を実行してください" >&2
    exit 1
fi

if launchctl list "$LABEL" &>/dev/null; then
    launchctl bootout "$DOMAIN/$LABEL"
    echo "停止しました"
    # プロセス完全終了を待つ（bootoutは非同期の場合がある）
    for i in $(seq 1 15); do
        if ! launchctl list "$LABEL" &>/dev/null; then
            break
        fi
        sleep 1
    done
fi

launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
echo "起動しました"
