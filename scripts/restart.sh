#!/bin/bash
# claude-slack-bridge LaunchAgent 再起動
#
# 動作:
#   - 既にロード済み → `launchctl kickstart -k` で SIGTERM 送信 → KeepAlive で自動再起動
#   - 未ロード（bootout 後など）→ `launchctl bootstrap` で初回ロード
#
# kickstart はサービスをアンロードせず launchd への指示のみで完結するため、
# ブリッジ自身からこのスクリプトを呼んでも（呼び出し元プロセスが死んでも）
# 再起動は launchd 側で完遂する。bootout は呼び出し元のプロセスツリーごと
# 殺すため、自己再起動には使えない。
set -euo pipefail

LABEL="com.user.claude-slack-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [ ! -f "$PLIST_PATH" ]; then
    echo "Error: $PLIST_PATH が見つかりません。先に scripts/install.sh を実行してください" >&2
    exit 1
fi

if launchctl print "$DOMAIN/$LABEL" &>/dev/null; then
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "再起動しました (kickstart)"
else
    launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
    echo "起動しました (bootstrap)"
fi
