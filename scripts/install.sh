#!/bin/bash
# claude-slack-bridge 統合セットアップスクリプト
# 前提条件チェック → venv作成 → 依存インストール → .env設定 → LaunchAgent登録
#
# 既にセットアップ済みの場合、各ステップはスキップされるため再実行しても安全です。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.user.claude-slack-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCH_SCRIPT="$SCRIPT_DIR/launch.sh"
LOG_DIR="$HOME/Library/Logs/claude-slack-bridge"

# 色定義（ターミナルが対応していれば使用）
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' NC=''
fi

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo -e "${BOLD}"
echo "============================================"
echo "  Claude Code ⇔ Slack Bridge — Setup"
echo "============================================"
echo -e "${NC}"

# ============================================
# Step 1: 前提条件チェック
# ============================================
info "Step 1/5: 前提条件チェック..."

# Python3
if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    ok "Python3: $PYTHON_VERSION"
else
    error "python3 が見つかりません"
    echo "  brew install python3 または公式サイトからインストールしてください"
    exit 1
fi

# Python バージョンチェック (3.10+)
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    error "Python 3.10 以上が必要です (現在: $(python3 --version))"
    exit 1
fi

# Claude CLI
CLAUDE_CMD="${CLAUDE_CMD:-claude}"
if command -v "$CLAUDE_CMD" &>/dev/null; then
    ok "Claude CLI: $(which "$CLAUDE_CMD")"
else
    warn "claude コマンドが見つかりません (PATH: $CLAUDE_CMD)"
    echo "  Claude Code CLI をインストールしてください: https://docs.anthropic.com/en/docs/claude-code"
    echo "  別のパスにインストール済みの場合は .env の CLAUDE_CMD で指定できます"
    echo ""
    read -p "  セットアップを続行しますか？ (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ============================================
# Step 2: Python仮想環境 (venv)
# ============================================
info "Step 2/5: Python仮想環境..."

cd "$PROJECT_DIR"

if [ -f "venv/bin/activate" ]; then
    ok "venv は既に存在します"
else
    info "venv を作成中..."
    python3 -m venv venv
    ok "venv を作成しました"
fi

# venv を有効化して依存パッケージをインストール
source venv/bin/activate

# ============================================
# Step 3: 依存パッケージ
# ============================================
info "Step 3/5: 依存パッケージインストール..."

pip install -q -r requirements.txt
ok "依存パッケージをインストールしました"

# ============================================
# Step 4: .env 設定
# ============================================
info "Step 4/5: .env 設定..."

if [ -f ".env" ]; then
    ok ".env は既に存在します"

    # 必須項目が設定されているかチェック
    _check_env_var() {
        local key="$1"
        local placeholder="$2"
        local value
        value=$(grep "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2-)
        if [ -z "$value" ] || [ "$value" = "$placeholder" ]; then
            warn "${key} が未設定またはプレースホルダーのままです"
            return 1
        fi
        return 0
    }

    needs_edit=0
    _check_env_var "SLACK_BOT_TOKEN" "xoxb-your-bot-token" || needs_edit=1
    _check_env_var "SLACK_APP_TOKEN" "xapp-your-app-level-token" || needs_edit=1
    _check_env_var "ADMIN_SLACK_USER_ID" "U0123456789" || needs_edit=1

    if [ "$needs_edit" -eq 1 ]; then
        echo ""
        warn ".env に未設定の必須項目があります"
        read -p "  対話的に設定しますか？ (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            _prompt_env_var() {
                local key="$1"
                local desc="$2"
                local prefix="$3"
                local current
                current=$(grep "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2-)

                echo ""
                echo -e "  ${BOLD}${desc}${NC}"
                if [ -n "$prefix" ]; then
                    echo "  (${prefix}... で始まるトークン)"
                fi

                read -p "  ${key}: " value
                if [ -n "$value" ]; then
                    if grep -q "^${key}=" .env; then
                        # macOS sed の -i はバックアップ拡張子が必要
                        sed -i '' "s|^${key}=.*|${key}=${value}|" .env
                    else
                        echo "${key}=${value}" >> .env
                    fi
                    ok "${key} を設定しました"
                else
                    warn "${key} をスキップしました（後で .env を直接編集してください）"
                fi
            }

            _prompt_env_var "SLACK_BOT_TOKEN" \
                "Bot User OAuth Token (Slack App > OAuth & Permissions)" "xoxb"
            _prompt_env_var "SLACK_APP_TOKEN" \
                "App-Level Token (Slack App > Basic Information > App-Level Tokens)" "xapp"
            _prompt_env_var "ADMIN_SLACK_USER_ID" \
                "管理者の Slack User ID (プロフィール → … → Copy member ID)" "U"
        fi
    fi
else
    info ".env.example から .env を作成..."
    cp .env.example .env
    ok ".env を作成しました"

    echo ""
    echo -e "${BOLD}  Slack App のトークンを設定してください。${NC}"
    echo "  (Enterでスキップ → 後で .env を直接編集できます)"

    _prompt_and_set() {
        local key="$1"
        local desc="$2"
        local prefix="$3"

        echo ""
        echo -e "  ${BOLD}${desc}${NC}"
        if [ -n "$prefix" ]; then
            echo "  (${prefix}... で始まるトークン)"
        fi

        read -p "  ${key}: " value
        if [ -n "$value" ]; then
            sed -i '' "s|^${key}=.*|${key}=${value}|" .env
            ok "${key} を設定しました"
        else
            warn "${key} をスキップしました"
        fi
    }

    _prompt_and_set "SLACK_BOT_TOKEN" \
        "Bot User OAuth Token (Slack App > OAuth & Permissions)" "xoxb"
    _prompt_and_set "SLACK_APP_TOKEN" \
        "App-Level Token (Slack App > Basic Information > App-Level Tokens)" "xapp"
    _prompt_and_set "ADMIN_SLACK_USER_ID" \
        "管理者の Slack User ID (プロフィール → … → Copy member ID)" "U"
fi

echo ""

# ============================================
# Step 5: LaunchAgent 登録
# ============================================
info "Step 5/5: LaunchAgent 登録..."

# launch.shに実行権限を付与
chmod +x "$LAUNCH_SCRIPT"

# ログディレクトリ作成
mkdir -p "$LOG_DIR"

# 既存のサービスがあれば停止
if launchctl list "$LABEL" &>/dev/null; then
    info "既存のサービスを停止中..."
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

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${GREEN}  セットアップ完了！${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
echo "  plist: $PLIST_PATH"
echo "  ログ:  $LOG_DIR/"
echo ""
echo "操作コマンド:"
echo "  停止:     ./scripts/stop.sh"
echo "  起動:     ./scripts/start.sh"
echo "  再起動:   ./scripts/restart.sh"
echo "  ログ確認: tail -f $LOG_DIR/stderr.log"
echo ""
echo "Slack App がまだの場合:"
echo "  slack-app-manifest.yml を使って一括作成できます"
echo "  → https://api.slack.com/apps → Create New App → From an app manifest"
