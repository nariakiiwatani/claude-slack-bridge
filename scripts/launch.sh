#!/bin/bash
# claude-slack-bridge 起動ラッパー
# LaunchAgentから呼び出される。venv有効化 + .env読み込み + bridge.py実行

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

# LaunchAgentはPATHが最小限のため、ユーザー環境の一般的なパスを追加
export PATH="$HOME/.local/bin:$HOME/.nodenv/shims:$HOME/.nvm/versions/node/*/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

# venv有効化
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "Error: venv/bin/activate not found in $PROJECT_DIR" >&2
    exit 1
fi

# .env読み込み（値に括弧等を含む行にも対応）
if [ -f ".env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # 空行・コメント行をスキップ
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # KEY=VALUE形式の行のみexport
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            export "$key=$value"
        fi
    done < .env
else
    echo "Error: .env not found in $PROJECT_DIR" >&2
    exit 1
fi

# 既存のbridge.pyプロセスを検出・終了（orphan対策）
existing_pids=$(pgrep -f "python.*bridge\.py" 2>/dev/null || true)
if [ -n "$existing_pids" ]; then
    echo "Killing stale bridge process(es): $existing_pids" >&2
    echo "$existing_pids" | xargs kill 2>/dev/null || true
    sleep 1
    # SIGKILLフォールバック
    remaining=$(pgrep -f "python.*bridge\.py" 2>/dev/null || true)
    if [ -n "$remaining" ]; then
        echo "Force-killing remaining process(es): $remaining" >&2
        echo "$remaining" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

exec python bridge.py
