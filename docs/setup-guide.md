# Setup Guide

Step-by-step guide for setting up Claude Code ⇔ Slack Bridge.

[日本語版はこちら / Japanese below](#セットアップガイド)

---

## 1. Create Slack App

1. Go to [Slack API](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Enter app name (e.g., `Claude Code Bridge`) and select your workspace

---

## 2. Enable Socket Mode

1. Left menu: **Socket Mode** → **Enable Socket Mode**
2. Enter Token Name: `claude-bridge` → **Generate**
3. Copy the `xapp-...` token → Set as `SLACK_APP_TOKEN` in `.env`

---

## 3. Add Bot Token Scopes

1. Left menu: **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**
2. Add the following scopes:
   - `chat:write` — Send messages
   - `channels:history` — Read public channel messages
   - `groups:history` — Read private channel messages
   - `files:read` — Read files (for attachment downloads)
   - `files:write` — Upload files (for large results)

---

## 4. Configure Event Subscriptions

1. Left menu: **Event Subscriptions** → **Enable Events**
2. Under **Subscribe to bot events**, add:
   - `message.channels` — Public channel messages
   - `message.groups` — Private channel messages

---

## 5. Install App

1. Left menu: **Install App** → **Install to Workspace** → Authorize
2. Copy **Bot User OAuth Token** (`xoxb-...`) → Set as `SLACK_BOT_TOKEN` in `.env`

---

## 6. Configure `.env`

```bash
# Clone and set up
git clone <repository-url>
cd claude-slack-bridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your tokens:

```bash
# Required
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_APP_TOKEN=xapp-1-xxxxxxxxxxxx-xxxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Admin user ID (required)
# Profile → … → Copy member ID
ADMIN_SLACK_USER_ID=U0123456789

# Access control
SLACK_ALLOWED_USERS=U1111111111,U2222222222   # Specific users ("*" for all)
SLACK_ALLOWED_CHANNELS=C3333333333             # Specific channels ("*" for all)

# Auto-approved tools
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite
```

**Finding your User ID:** Open your Slack profile → "..." → "Copy member ID"

---

## 7. Start the Bridge

Use `scripts/install.sh` to set up and start as a macOS LaunchAgent:

```bash
./scripts/install.sh
```

To restart:

```bash
launchctl bootout gui/$(id -u)/com.user.claude-slack-bridge
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.claude-slack-bridge.plist
```

If `NOTIFICATION_CHANNEL` is set, you'll see a startup notification:
> 🚀 **Claude Code Bridge has started**

---

## 8. First Task

1. Invite the bot to a channel
2. Send your first task with a working directory:
   ```
   @bot in /path/to/your/project Hello! What files are in this project?
   ```
3. The bot creates a thread for the session. Reply in the thread to continue (no `@bot` needed).

---

## セットアップガイド

Claude Code ⇔ Slack Bridge のセットアップ手順です。

---

### 1. Slack App を作成

1. [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From scratch**
2. アプリ名（例: `Claude Code Bridge`）とワークスペースを選択

---

### 2. Socket Mode を有効化

1. 左メニュー **Socket Mode** → **Enable Socket Mode**
2. Token Name に `claude-bridge` と入力 → **Generate**
3. 表示される `xapp-...` トークンをコピー → `.env` の `SLACK_APP_TOKEN` に設定

---

### 3. Bot Token Scopes を追加

1. 左メニュー **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**
2. 以下のスコープを追加:
   - `chat:write` — メッセージ送信
   - `channels:history` — パブリックチャンネルのメッセージ読み取り
   - `groups:history` — プライベートチャンネルのメッセージ読み取り
   - `files:read` — ファイル読み取り（添付ファイルダウンロード用）
   - `files:write` — ファイル送信（結果が大きい場合用）

---

### 4. Event Subscriptions を設定

1. 左メニュー **Event Subscriptions** → **Enable Events**
2. **Subscribe to bot events** に以下を追加:
   - `message.channels` — パブリックチャンネルのメッセージ
   - `message.groups` — プライベートチャンネルのメッセージ

---

### 5. アプリをインストール

1. 左メニュー **Install App** → **Install to Workspace** → 許可
2. **Bot User OAuth Token** (`xoxb-...`) をコピー → `.env` の `SLACK_BOT_TOKEN` に設定

---

### 6. `.env` を設定

```bash
# クローンとセットアップ
git clone <repository-url>
cd claude-slack-bridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` にトークンを設定:

```bash
# 必須
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_APP_TOKEN=xapp-1-xxxxxxxxxxxx-xxxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 管理者のSlackユーザーID（必須）
# 自分のプロフィール → … → Copy member ID
ADMIN_SLACK_USER_ID=U0123456789

# アクセス制御
SLACK_ALLOWED_USERS=U1111111111,U2222222222   # 特定ユーザーのみ（"*" で全許可）
SLACK_ALLOWED_CHANNELS=C3333333333             # 特定チャンネルのみ（"*" で全許可）

# 自動承認ツール
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite
```

**ユーザーIDの確認方法:** Slackで自分のプロフィールを開く → 「…」→ 「メンバーIDをコピー」

---

### 7. Bridge を起動

`scripts/install.sh` でLaunchAgentとしてセットアップ・起動します:

```bash
./scripts/install.sh
```

再起動:

```bash
launchctl bootout gui/$(id -u)/com.user.claude-slack-bridge
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.claude-slack-bridge.plist
```

`NOTIFICATION_CHANNEL` を設定している場合、起動通知が届きます:
> 🚀 **Claude Code Bridge が起動しました**

---

### 8. 初回タスク実行

1. botをチャンネルに招待
2. 作業ディレクトリを指定してタスクを送信:
   ```
   @bot in /path/to/your/project Hello! What files are in this project?
   ```
3. botがスレッドを作成します。スレッドに返信すれば自動で続行します（`@bot` 不要）。

---

← [Back to README / READMEに戻る](../README.md)
