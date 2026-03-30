#!/bin/bash
# claude-slack-bridge LaunchAgent 停止
set -euo pipefail

LABEL="com.user.claude-slack-bridge"
DOMAIN="gui/$(id -u)"

if ! launchctl list "$LABEL" &>/dev/null; then
    echo "起動していません" >&2
    exit 1
fi

launchctl bootout "$DOMAIN/$LABEL"
echo "停止しました"
