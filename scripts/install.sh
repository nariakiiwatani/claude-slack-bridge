#!/bin/bash
# claude-slack-bridge LaunchAgent インストールスクリプト
# macOSログイン時に自動起動 + クラッシュ時自動再起動を設定する

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.user.claude-slack-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCH_SCRIPT="$SCRIPT_DIR/launch.sh"
LOG_DIR="$HOME/Library/Logs/claude-slack-bridge"

# launch.shに実行権限を付与
chmod +x "$LAUNCH_SCRIPT"

# ログディレクトリ作成
mkdir -p "$LOG_DIR"

# 既存のサービスがあれば停止
if launchctl list "$LABEL" &>/dev/null; then
    echo "既存のサービスを停止中..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# plist生成
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${LAUNCH_SCRIPT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/stderr.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

# サービス登録・起動
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

echo "=== インストール完了 ==="
echo "  plist: $PLIST_PATH"
echo "  ログ:  $LOG_DIR/"
echo ""
echo "操作コマンド:"
echo "  停止:   launchctl bootout gui/$(id -u)/$LABEL"
echo "  起動:   launchctl bootstrap gui/$(id -u) $PLIST_PATH"
echo "  状態:   launchctl print gui/$(id -u)/$LABEL"
echo "  ログ:   tail -f $LOG_DIR/stderr.log"
