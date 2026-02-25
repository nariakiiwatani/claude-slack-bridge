# Setup Guide / セットアップガイド

Step-by-step guide for setting up Claude Code ⇔ Slack Bridge.

Claude Code ⇔ Slack Bridge のセットアップ手順です。

---

## 1. Create Slack App / Slack App を作成

1. Go to [Slack API](https://api.slack.com/apps) → **Create New App** → **From scratch**
   [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From scratch**

2. Enter app name (e.g., `Claude Code Bridge`) and select your workspace
   アプリ名（例: `Claude Code Bridge`）とワークスペースを選択

---

## 2. Enable Socket Mode / Socket Mode を有効化

1. Left menu: **Socket Mode** → **Enable Socket Mode**
   左メニュー **Socket Mode** → **Enable Socket Mode**

2. Enter Token Name: `claude-bridge` → **Generate**
   Token Name に `claude-bridge` と入力 → **Generate**

3. Copy the `xapp-...` token → Set as `SLACK_APP_TOKEN` in `.env`
   表示される `xapp-...` トークンをコピー → `.env` の `SLACK_APP_TOKEN` に設定

---

## 3. Add Bot Token Scopes / Bot Token Scopes を追加

1. Left menu: **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**
   左メニュー **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**

2. Add the following scopes / 以下のスコープを追加:
   - `chat:write` — Send messages / メッセージ送信
   - `channels:history` — Read public channel messages / パブリックチャンネルのメッセージ読み取り
   - `groups:history` — Read private channel messages / プライベートチャンネルのメッセージ読み取り
   - `files:write` — Upload files (for large results) / ファイル送信（結果が大きい場合用）

---

## 4. Configure Event Subscriptions / Event Subscriptions を設定

1. Left menu: **Event Subscriptions** → **Enable Events**
   左メニュー **Event Subscriptions** → **Enable Events**

2. Under **Subscribe to bot events**, add / **Subscribe to bot events** に以下を追加:
   - `message.channels` — Public channel messages / パブリックチャンネルのメッセージ
   - `message.groups` — Private channel messages / プライベートチャンネルのメッセージ

---

## 5. Install App / アプリをインストール

1. Left menu: **Install App** → **Install to Workspace** → Authorize
   左メニュー **Install App** → **Install to Workspace** → 許可

2. Copy **Bot User OAuth Token** (`xoxb-...`) → Set as `SLACK_BOT_TOKEN` in `.env`
   **Bot User OAuth Token** (`xoxb-...`) をコピー → `.env` の `SLACK_BOT_TOKEN` に設定

---

## 6. Configure `.env` / `.env` を設定

```bash
# Clone and set up / クローンとセットアップ
git clone <repository-url>
cd claude-slack-bridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your tokens / `.env` にトークンを設定:

```bash
# Required / 必須
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_APP_TOKEN=xapp-1-xxxxxxxxxxxx-xxxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Admin user ID (required) / 管理者のSlackユーザーID（必須）
# Profile → … → Copy member ID
ADMIN_SLACK_USER_ID=U0123456789

# Access control / アクセス制御
SLACK_ALLOWED_USERS=U1111111111,U2222222222   # Specific users ("*" for all) / 特定ユーザー（"*" で全許可）
SLACK_ALLOWED_CHANNELS=C3333333333             # Specific channels ("*" for all) / 特定チャンネル（"*" で全許可）

# Auto-approved tools / 自動承認ツール
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite
```

**Finding your User ID / ユーザーIDの確認方法:**

Open your Slack profile → "..." → "Copy member ID"
Slackで自分のプロフィールを開く → 「…」→ 「メンバーIDをコピー」

---

## 7. Start the Bridge / Bridge を起動

```bash
source venv/bin/activate
python bridge.py
```

If `NOTIFICATION_CHANNEL` is set, you'll see a startup notification:
`NOTIFICATION_CHANNEL` を設定している場合、起動通知が届きます:

> 🚀 **Claude Code Bridge が起動しました**

---

## 8. First Task / 初回タスク実行

1. Invite the bot to a channel / botをチャンネルに招待

2. Bind a project root / プロジェクトルートを紐付け:
   ```
   @bot bind -d /path/to/your/project
   ```

3. Send your first task / 最初のタスクを送信:
   ```
   @bot Hello! What files are in this project?
   ```

4. The bot creates a thread for the session. Reply in the thread to continue (no `@bot` needed).
   botがスレッドを作成します。スレッドに返信すれば自動で続行します（`@bot` 不要）。

---

← [Back to README / READMEに戻る](../README.md)
