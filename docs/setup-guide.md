# Setup Guide

Step-by-step guide for setting up Claude Code ⇔ Slack Bridge.

[日本語版はこちら / Japanese below](#セットアップガイド)

---

## Option A: Quick Setup (Manifest)

The fastest way to get started. Uses the app manifest to create the Slack App in one step.

### 1. Create Slack App from Manifest

1. Go to [Slack API](https://api.slack.com/apps) → **Create New App** → **From an app manifest**
2. Select your workspace
3. Paste the contents of [`slack-app-manifest.yml`](../slack-app-manifest.yml) (select the **YAML** tab)
4. Review the configuration and click **Create**

### 2. Generate Tokens

After the app is created, you need two tokens:

**App-Level Token (`SLACK_APP_TOKEN`):**
1. Go to **Settings > Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**
2. Name: `claude-bridge`, Scope: `connections:write` → **Generate**
3. Copy the `xapp-...` token

**Bot Token (`SLACK_BOT_TOKEN`):**
1. Go to **OAuth & Permissions** → **Install to Workspace** → Authorize
2. Copy the **Bot User OAuth Token** (`xoxb-...`)

### 3. Run Install Script

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
./scripts/install.sh
```

The script will prompt you for the tokens. Done!

---

## Option B: Manual Setup

If you prefer to configure the Slack App manually.

### 1. Create Slack App

1. Go to [Slack API](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Enter app name (e.g., `Claude Code Bridge`) and select your workspace

### 2. Enable Socket Mode

1. Left menu: **Socket Mode** → **Enable Socket Mode**
2. Enter Token Name: `claude-bridge` → **Generate**
3. Copy the `xapp-...` token → Set as `SLACK_APP_TOKEN` in `.env`

### 3. Add Bot Token Scopes

1. Left menu: **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**
2. Add the following scopes:
   - `chat:write` — Send messages
   - `channels:history` — Read public channel messages
   - `groups:history` — Read private channel messages
   - `files:read` — Read files (for attachment downloads)
   - `files:write` — Upload files (for large results)

### 4. Configure Event Subscriptions

1. Left menu: **Event Subscriptions** → **Enable Events**
2. Under **Subscribe to bot events**, add:
   - `message.channels` — Public channel messages
   - `message.groups` — Private channel messages

### 5. Install App

1. Left menu: **Install App** → **Install to Workspace** → Authorize
2. Copy **Bot User OAuth Token** (`xoxb-...`) → Set as `SLACK_BOT_TOKEN` in `.env`

### 6. Run Install Script

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
./scripts/install.sh
```

---

## `.env` Configuration Reference

The install script creates `.env` from `.env.example` and prompts for required values. Here's the full reference:

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes | Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | App-Level Token (`xapp-...`) |
| `ADMIN_SLACK_USER_ID` | Yes | Admin's Slack User ID (`U...`) — always allowed |
| `SLACK_ALLOWED_USERS` | No | Comma-separated user IDs, or `*` for all |
| `SLACK_ALLOWED_CHANNELS` | No | Comma-separated channel IDs, or `*` for all |
| `NOTIFICATION_CHANNEL` | No | Channel/User ID for startup/shutdown notifications |
| `CLAUDE_CMD` | No | Path to claude CLI (default: `claude`) |
| `DEFAULT_ALLOWED_TOOLS` | No | Auto-approved tools (comma-separated) |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |
| `SLACK_LANGUAGE` | No | `ja` or `en` (default: `ja`) |

**Finding your User ID:** Open your Slack profile → "..." → "Copy member ID"

---

## First Task

1. Invite the bot to a channel
2. Send your first task with a working directory:
   ```
   @bot in /path/to/your/project Hello! What files are in this project?
   ```
3. The bot creates a thread for the session. Reply in the thread to continue (no `@bot` needed).

---

## Service Management

```bash
./scripts/stop.sh       # Stop
./scripts/start.sh      # Start
./scripts/restart.sh    # Restart
./scripts/uninstall.sh  # Uninstall
tail -f ~/Library/Logs/claude-slack-bridge/stderr.log  # View logs
```

---

## Troubleshooting

### Bridge won't start
- Check logs: `tail -f ~/Library/Logs/claude-slack-bridge/stderr.log`
- Verify `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are correct
- Verify Socket Mode is enabled
- Run `./scripts/install.sh` to re-check prerequisites

### Bot doesn't respond
- Verify `SLACK_ALLOWED_USERS` and `SLACK_ALLOWED_CHANNELS` are configured
- Verify `message.channels` event is subscribed
- Verify the bot is invited to the channel
- Verify you're including the `@bot` mention

### Claude Code errors
- Verify: `which claude` shows the CLI path
- Verify: `claude -p "hello" --output-format json` works
- Verify Claude Code authentication is valid

---
---

# セットアップガイド

Claude Code ⇔ Slack Bridge のセットアップ手順です。

---

## 方法A: クイックセットアップ（Manifest使用）

最速の方法です。App Manifestを使ってSlack Appを一括作成します。

### 1. ManifestからSlack Appを作成

1. [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From an app manifest**
2. ワークスペースを選択
3. [`slack-app-manifest.yml`](../slack-app-manifest.yml) の内容を貼り付け（**YAML**タブを選択）
4. 設定を確認して **Create** をクリック

### 2. トークンを生成

アプリ作成後、2つのトークンが必要です:

**App-Level Token (`SLACK_APP_TOKEN`):**
1. **Settings > Basic Information** → **App-Level Tokens** → **Generate Token and Scopes**
2. Name: `claude-bridge`, Scope: `connections:write` → **Generate**
3. `xapp-...` トークンをコピー

**Bot Token (`SLACK_BOT_TOKEN`):**
1. **OAuth & Permissions** → **Install to Workspace** → 許可
2. **Bot User OAuth Token** (`xoxb-...`) をコピー

### 3. インストールスクリプトを実行

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
./scripts/install.sh
```

スクリプトがトークンの入力を案内します。これで完了です！

---

## 方法B: 手動セットアップ

Slack Appを手動で設定したい場合。

### 1. Slack App を作成

1. [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From scratch**
2. アプリ名（例: `Claude Code Bridge`）とワークスペースを選択

### 2. Socket Mode を有効化

1. 左メニュー **Socket Mode** → **Enable Socket Mode**
2. Token Name に `claude-bridge` と入力 → **Generate**
3. 表示される `xapp-...` トークンをコピー → `.env` の `SLACK_APP_TOKEN` に設定

### 3. Bot Token Scopes を追加

1. 左メニュー **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**
2. 以下のスコープを追加:
   - `chat:write` — メッセージ送信
   - `channels:history` — パブリックチャンネルのメッセージ読み取り
   - `groups:history` — プライベートチャンネルのメッセージ読み取り
   - `files:read` — ファイル読み取り（添付ファイルダウンロード用）
   - `files:write` — ファイル送信（結果が大きい場合用）

### 4. Event Subscriptions を設定

1. 左メニュー **Event Subscriptions** → **Enable Events**
2. **Subscribe to bot events** に以下を追加:
   - `message.channels` — パブリックチャンネルのメッセージ
   - `message.groups` — プライベートチャンネルのメッセージ

### 5. アプリをインストール

1. 左メニュー **Install App** → **Install to Workspace** → 許可
2. **Bot User OAuth Token** (`xoxb-...`) をコピー → `.env` の `SLACK_BOT_TOKEN` に設定

### 6. インストールスクリプトを実行

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
./scripts/install.sh
```

---

## `.env` 設定リファレンス

インストールスクリプトが `.env.example` から `.env` を作成し、必須値を案内します。全設定の一覧:

| 変数 | 必須 | 説明 |
|------|------|------|
| `SLACK_BOT_TOKEN` | Yes | Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | App-Level Token (`xapp-...`) |
| `ADMIN_SLACK_USER_ID` | Yes | 管理者の Slack User ID (`U...`) — 常にアクセス許可 |
| `SLACK_ALLOWED_USERS` | No | カンマ区切りのユーザーID、`*` で全許可 |
| `SLACK_ALLOWED_CHANNELS` | No | カンマ区切りのチャンネルID、`*` で全許可 |
| `NOTIFICATION_CHANNEL` | No | 起動/停止通知の送信先チャンネル/ユーザーID |
| `CLAUDE_CMD` | No | claude CLIのパス (デフォルト: `claude`) |
| `DEFAULT_ALLOWED_TOOLS` | No | 自動承認ツール (カンマ区切り) |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (デフォルト: `INFO`) |
| `SLACK_LANGUAGE` | No | `ja` または `en` (デフォルト: `ja`) |

**ユーザーIDの確認方法:** Slackで自分のプロフィールを開く → 「…」→ 「メンバーIDをコピー」

---

## 初回タスク実行

1. botをチャンネルに招待
2. 作業ディレクトリを指定してタスクを送信:
   ```
   @bot in /path/to/your/project Hello! What files are in this project?
   ```
3. botがスレッドを作成します。スレッドに返信すれば自動で続行します（`@bot` 不要）。

---

## サービス管理

```bash
./scripts/stop.sh       # 停止
./scripts/start.sh      # 起動
./scripts/restart.sh    # 再起動
./scripts/uninstall.sh  # アンインストール
tail -f ~/Library/Logs/claude-slack-bridge/stderr.log  # ログ確認
```

---

## トラブルシューティング

### Bridge が起動しない
- ログを確認: `tail -f ~/Library/Logs/claude-slack-bridge/stderr.log`
- `SLACK_BOT_TOKEN` と `SLACK_APP_TOKEN` が正しいか確認
- Socket Mode が有効になっているか確認
- `./scripts/install.sh` を再実行して前提条件を確認

### Bot が反応しない
- `SLACK_ALLOWED_USERS` と `SLACK_ALLOWED_CHANNELS` が設定されているか確認
- `message.channels` イベントが購読されているか確認
- botがチャンネルに招待されているか確認
- `@bot` メンションを付けているか確認

### Claude Code がエラーになる
- `which claude` でclaude コマンドのパスを確認
- `claude -p "hello" --output-format json` がターミナルで動くか確認
- Claude Code の認証が有効か確認

---

← [Back to README / READMEに戻る](../README.md)
