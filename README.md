# Claude Code ⇔ Slack Bridge

A bridge that lets you control Claude Code (CLI) running on your Mac from Slack. It supports concurrent task execution and uses Slack's Socket Mode, so no public URL or port forwarding is required.

Invite the bot to a Slack channel, bind the channel to a project directory, and send tasks with `@bot` mentions. Thread replies to active sessions are automatically forwarded to the CLI without needing a mention. Each thread corresponds to a session, and replying to a completed task automatically resumes the session with `--resume`.

[日本語版はこちら / Japanese below](#日本語)

## Features

- Check progress and send new instructions from your phone while away from your desk
- Receive completion notifications for long-running tasks on mobile
- Submit tasks on the go when an idea strikes
- Share a single Mac's Claude Code with team members

## Security Notice

This tool **remotely operates Claude Code (a CLI capable of shell execution) on your local Mac** via Slack. Please understand the following before using it:

- **Admin**: The user set in `ADMIN_SLACK_USER_ID` in `.env` always has access
- **Access control**: Only users/channels allowed by `SLACK_ALLOWED_USERS` / `SLACK_ALLOWED_CHANNELS` can interact with the bot. If you set `*` (allow all), be aware of the risk that the bot may be invited to unintended channels
- **Token management**: If `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` are leaked, third parties could operate Claude Code through the bot. Set proper file permissions on `.env` and never commit it to Git
- **Allowed tools**: `DEFAULT_ALLOWED_TOOLS` controls which tools Claude Code auto-approves. Setting `Bash(*)` allows arbitrary shell command execution. We recommend allowing only the minimum necessary tools
- **Personal Mac only**: This tool is not designed for shared servers or CI environments

## Architecture

```
┌──────────┐   Socket Mode    ┌───────────────┐    subprocess ×N   ┌────────────┐
│   You    │ ─────────────► │               │ ──────────────► │ Claude Code│
│ (Phone)  │    Channel      │    Bridge     │                  │   (CLI)    │
└──────────┘                  │  (on your Mac) │ ──────────────► ├────────────┤
                              │               │                  │ Claude Code│
                              └───────────────┘                  │   (CLI)    │
                                      │                          └────────────┘
```

**Key point:** Uses Socket Mode — no public URL or port forwarding needed.

## Setup

### 1. Create Slack App

1. Go to [Slack API](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Choose an app name (e.g. `Claude Code Bridge`) and select your workspace

#### Enable Socket Mode
1. Left menu **Socket Mode** → **Enable Socket Mode**
2. Enter `claude-bridge` as the Token Name → **Generate**
3. Copy the displayed `xapp-...` token → set it as `SLACK_APP_TOKEN` in `.env`

#### Add Bot Token Scopes
1. Left menu **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**, add the following:
   - `chat:write` — Send messages
   - `channels:history` — Read public channel messages
   - `groups:history` — Read private channel messages
   - `files:write` — Send files (for large results)

#### Configure Event Subscriptions
1. Left menu **Event Subscriptions** → **Enable Events**
2. Under **Subscribe to bot events**, add:
   - `message.channels` — Public channel messages
   - `message.groups` — Private channel messages

#### Install App
1. Left menu **Install App** → **Install to Workspace** → Authorize
2. Copy the **Bot User OAuth Token** (`xoxb-...`) → set it as `SLACK_BOT_TOKEN` in `.env`

### 2. Project Setup

```bash
cd /path/to/claude-slack-bridge

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Prepare configuration file
cp .env.example .env
```

### 3. Configure `.env`

```bash
# Required
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_APP_TOKEN=xapp-1-xxxxxxxxxxxx-xxxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Admin Slack user ID (required)
# Your profile → … → Copy member ID
ADMIN_SLACK_USER_ID=U0123456789

# Access control
SLACK_ALLOWED_USERS=U1111111111,U2222222222   # Specific users only ("*" to allow all)
SLACK_ALLOWED_CHANNELS=C3333333333             # Specific channels only ("*" to allow all)

# Notification channel (for startup/shutdown notifications, optional)
# You can specify a user ID (U...) instead of a channel ID to receive DM notifications
# NOTIFICATION_CHANNEL=C1234567890

# Default working directory for Claude Code
# Can be overridden per channel with the bind command
WORKING_DIR=/Users/yourname/projects/my-project

# Auto-approved tools (adjust for your project)
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite
```

**How to find your user ID:** Open your Slack profile → "..." → "Copy member ID"

### 4. Run

```bash
python bridge.py
```

If `NOTIFICATION_CHANNEL` is configured, you'll receive a startup notification in that channel:
> :rocket: **Claude Code Bridge has started**
> Default working directory: `/Users/yourname/projects/my-project`

> For a detailed step-by-step guide with screenshots, see [Setup Guide](docs/setup-guide.md).

## Usage

Invite the bot to a channel and send commands with `@bot` mention. Thread replies to active sessions don't need mentions.

### Command Reference

| Command | Description |
|---------|-------------|
| `@bot <task>` | Start new session + task (bind required) |
| (thread reply) `<instruction>` | Auto-resume with `--resume` (no mention needed) |
| `@bot in src/subdir task` | Run in specified directory (relative to project root) |
| `@bot status` | Show task status in project |
| `@bot sessions` | List sessions in project |
| `@bot cancel #2` | Cancel a task |
| `@bot cancel all` | Cancel all tasks in project |
| `@bot bind` | Show usage + bindable process list |
| `@bot bind -d /path/to/project` | Bind channel to project root |
| `@bot bind -p [PID]` | Fork a running claude CLI process |
| `@bot unbind` | Unbind project root |
| (in thread) `tools <tool1,...>` | Set allowed tools for next task (per session) |
| `@bot help` | Show help |

### Channel-Project Binding

Use the `bind` command to associate a channel with a project root directory. All tasks run from that channel will execute in that directory. The binding is persisted in `channel_projects.json` and survives bridge restarts.

```
@bot bind -d /Users/yourname/projects/my-api
→ Project root set for this channel: /Users/yourname/projects/my-api

@bot Improve error handling
→ Runs in /Users/yourname/projects/my-api

@bot in src/tests Write tests
→ Runs in /Users/yourname/projects/my-api/src/tests (relative path resolved)

@bot unbind
→ Project root binding removed
```

### Typical Workflow

Each task corresponds to a single Slack thread (session = thread). After a task completes, replying to the same thread automatically resumes the session with `--resume`.

```
You: @bot Improve error handling

━━ Channel ━━━━━━━━━━━━━━━━━━━━━━━━
  🔵 #1  Task started
  📂 my-api
  └─ Thread
     ├─ 🔵 #1  ⏳ Running... Tools: Read → Edit
     ├─ 🔵 #1  ✅ Task completed (45s)
     │     Session: abc123def456...
     │     Reply to this thread to auto-resume
     │
     │  ← Just reply to the thread to --resume ──
     │
     You: Write tests too. Use jest
     │
     ├─ 🔵 #3  ▶️ Session resumed
     │     Write tests too. Use jest
     ├─ 🔵 #3  ⏳ Running... Tools: Read → Write
     └─ 🔵 #3  ✅ Task completed (30s)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### bind -p: Fork a Running CLI Process

`bind -p` lets you integrate a running Claude CLI process into a channel's Project+Session model.

```
@bot bind -p
→ Forkable claude CLI instances:
   1 — PID 12345  📂 /Users/you/project-a  🕐 02:15:30
   2 — PID 67890  📂 /Users/you/project-b  🕐 00:30:10
  Enter a number to select, or cancel to abort

1
→ ✅ Forked PID 12345
```

You can also specify the PID directly:
```
@bot bind -p 12345
```

After forking:
- **While the process is alive**: Thread replies are forwarded to the CLI (I/O forwarding)
- **After the process exits**: Thread replies start auto-resume with `--resume`
- The project root is automatically set to the CLI process's working directory

## Allowed Tools Configuration

Example `DEFAULT_ALLOWED_TOOLS` settings:

```bash
# Conservative (read-only focus)
DEFAULT_ALLOWED_TOOLS=Read,TodoWrite

# Standard (file editing + git operations)
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite

# Aggressive (auto-approve nearly everything)
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(*),TodoWrite,WebSearch,WebFetch
```

You can also temporarily override tools per session using the `tools` command (effective for the next task only):
```
(in thread) tools Read,Write,Edit,Bash(*)
(in thread) Check the result of npm run build and fix the errors
```

## Troubleshooting

### Bridge won't start
- Verify `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are correct
- Verify Socket Mode is enabled
- Verify packages are installed with `pip install -r requirements.txt`

### Bot doesn't respond
- Verify `SLACK_ALLOWED_USERS` and `SLACK_ALLOWED_CHANNELS` are configured
- Verify `message.channels` event is subscribed
- Verify the bot is invited to the channel
- Verify you're including the `@bot` mention
- Check the terminal for errors

### Claude Code errors
- Verify the claude command path with `which claude`
- Verify `WORKING_DIR` exists
- Verify `claude -p "hello" --output-format json` works in your terminal
- Verify Claude Code authentication is valid (try launching `claude` directly)

### Japanese text garbled
- Try adding `LANG=en_US.UTF-8` to `.env`
- Verify you're using Python 3.9 or later

## Auto-start (macOS)

To start the bridge automatically when your Mac boots, use a LaunchAgent:

```bash
cat << 'EOF' > ~/Library/LaunchAgents/com.claude-slack-bridge.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-slack-bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/python</string>
        <string>/path/to/claude-slack-bridge/bridge.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/claude-slack-bridge</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-slack-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-slack-bridge.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.claude-slack-bridge.plist
```

---

## 日本語

Mac上で動くClaude Codeを、Slackから監視・操作するブリッジ。
複数タスクの同時実行に対応。

チャンネルで `@bot` メンションしてClaude Codeを操作します。チャンネルとプロジェクトルートを紐付けることで、ホストのディレクトリ構造を意識せず相対パスだけで操作できます。

### こんなとき便利

- トイレや休憩で席を離れるとき → スマホで進捗確認＆新しい指示
- 長時間タスクの完了通知をスマホで受け取りたい
- 移動中にふと思いついたタスクをすぐ投入したい
- チームメンバーと1台のMac上のClaude Codeを共有して使いたい

### セキュリティに関する注意

このツールはSlack経由で **ローカルMac上のClaude Code（=シェル実行が可能なCLI）をリモート操作** します。以下の点を理解した上でご利用ください。

- **管理者**: `.env` の `ADMIN_SLACK_USER_ID` に設定したユーザーは常にアクセスが許可されます
- **アクセス制御**: `SLACK_ALLOWED_USERS` / `SLACK_ALLOWED_CHANNELS` で許可されたユーザー・チャンネルのみ応答します。`*`（全許可）を設定する場合は、botが意図しないチャンネルに招待されるリスクに注意してください
- **トークン管理**: `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` が漏洩すると、第三者がBotを通じてClaude Codeを操作できる可能性があります。`.env` ファイルの権限を適切に設定し、Gitにコミットしないでください
- **許可ツールの設定**: `DEFAULT_ALLOWED_TOOLS` はClaude Codeが自動承認するツールを制御します。`Bash(*)` を設定すると任意のシェルコマンドが自動実行されます。必要最小限のツールのみ許可することを推奨します
- **自分のMac専用**: 共有サーバーやCI環境での利用は想定していません

### 仕組み

アーキテクチャ図は[上記の Architecture セクション](#architecture)を参照してください。

### セットアップ

#### 1. Slack App を作成

1. [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From scratch**
2. アプリ名（例: `Claude Code Bridge`）とワークスペースを選択

##### Socket Mode を有効化
1. 左メニュー **Socket Mode** → **Enable Socket Mode**
2. Token Name に `claude-bridge` と入力 → **Generate**
3. 表示される `xapp-...` トークンをコピー → `.env` の `SLACK_APP_TOKEN` に設定

##### Bot Token Scopes を追加
1. 左メニュー **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** に以下を追加:
   - `chat:write` — メッセージ送信
   - `channels:history` — パブリックチャンネルのメッセージ読み取り
   - `groups:history` — プライベートチャンネルのメッセージ読み取り
   - `files:write` — ファイル送信（結果が大きい場合用）

##### Event Subscriptions を設定
1. 左メニュー **Event Subscriptions** → **Enable Events**
2. **Subscribe to bot events** に以下を追加:
   - `message.channels` — パブリックチャンネルのメッセージ
   - `message.groups` — プライベートチャンネルのメッセージ

##### Install App
1. 左メニュー **Install App** → **Install to Workspace** → 許可
2. **Bot User OAuth Token** (`xoxb-...`) をコピー → `.env` の `SLACK_BOT_TOKEN` に設定

#### 2. プロジェクトのセットアップ

```bash
cd /path/to/claude-slack-bridge

# 仮想環境を作成（推奨）
python3 -m venv venv
source venv/bin/activate

# 依存パッケージをインストール
pip install -r requirements.txt

# 設定ファイルを準備
cp .env.example .env
```

#### 3. `.env` を編集

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

# 通知チャンネル（起動/停止通知の送信先、任意）
# チャンネルIDの代わりにユーザーID（U...）を指定するとDMで通知されます
# NOTIFICATION_CHANNEL=C1234567890

# Claude Code のデフォルト作業ディレクトリ
# チャンネルごとに bind コマンドで上書き可能
WORKING_DIR=/Users/yourname/projects/my-project

# 自動承認ツール（プロジェクトに合わせて調整）
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite
```

**ユーザーIDの確認方法:** Slackで自分のプロフィールを開く → 「...」→ 「メンバーIDをコピー」

#### 4. 起動

```bash
python bridge.py
```

`NOTIFICATION_CHANNEL` を設定している場合、そのチャンネルに起動通知が届きます:
> :rocket: **Claude Code Bridge が起動しました**
> デフォルト作業ディレクトリ: `/Users/yourname/projects/my-project`

> スクリーンショット付きの詳しい手順は [セットアップガイド](docs/setup-guide.md) を参照してください。

### 使い方

botをチャンネルに招待し、`@bot` メンション付きでコマンドを送信します。タスクのSlackスレッドへの返信はメンション不要でCLIに転送されます。

#### コマンド一覧

| コマンド | 説明 |
|---------|------|
| `@bot <タスク内容>` | 新しいセッション＋タスクを実行（bind必須） |
| (スレッド返信) `<指示>` | 同セッションで `--resume` 自動続行（メンション不要） |
| `@bot in src/subdir タスク` | 指定ディレクトリで実行（相対パスはプロジェクトルート基準） |
| `@bot status` | プロジェクト内タスクの状態一覧 |
| `@bot sessions` | プロジェクト内セッション一覧 |
| `@bot cancel #2` | タスクをキャンセル |
| `@bot cancel all` | プロジェクト内全タスクをキャンセル |
| `@bot bind` | usage表示＋バインド可能なプロセスリスト |
| `@bot bind -d /path/to/project` | チャンネルにプロジェクトルートを紐付け |
| `@bot bind -p [PID]` | 実行中のclaude CLIプロセスをフォーク |
| `@bot unbind` | プロジェクトルートの紐付けを解除 |
| (スレッド内) `tools <tool1,...>` | 次回タスクの許可ツール設定（セッション単位） |
| `@bot help` | ヘルプ表示 |

#### チャンネルとプロジェクトの紐付け

`bind` コマンドでチャンネルにプロジェクトルートを紐付けると、そのチャンネルから実行するタスクは常にそのディレクトリで動きます。紐付けは `channel_projects.json` に永続化され、Bridge再起動後も維持されます。

```
@bot bind -d /Users/yourname/projects/my-api
→ このチャンネルのプロジェクトルートを設定しました: /Users/yourname/projects/my-api

@bot エラーハンドリングを改善して
→ /Users/yourname/projects/my-api で実行

@bot in src/tests テスト書いて
→ /Users/yourname/projects/my-api/src/tests で実行（相対パス解決）

@bot unbind
→ プロジェクトルートの紐付けを解除しました
```

#### 典型的なワークフロー

1つのタスクは1つのSlackスレッドに対応します（セッション = スレッド）。
タスク完了後、同じスレッドに返信すると `--resume` で自動的にセッションが続行されます。

```
あなた: @bot エラーハンドリングを改善して

━━ チャンネル ━━━━━━━━━━━━━━━━━━━━━━━━
  🔵 #1  タスク開始
  📂 my-api
  └─ スレッド
     ├─ 🔵 #1  ⏳ 実行中... ツール: Read → Edit
     ├─ 🔵 #1  ✅ タスク完了 (45秒)
     │     Session: abc123def456...
     │     このスレッドに返信すると自動で続行します
     │
     │  ← スレッドに返信するだけで --resume 続行 ──
     │
     あなた: テストも書いて。jest を使って
     │
     ├─ 🔵 #3  ▶️ セッション続行
     │     テストも書いて。jest を使って
     ├─ 🔵 #3  ⏳ 実行中... ツール: Read → Write
     └─ 🔵 #3  ✅ タスク完了 (30秒)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

#### bind -p: 実行中のCLIプロセスをチャンネルにフォーク

`bind -p` を使うと、実行中のClaude CLIプロセスを **チャンネルのProject+Session** として統合できます。

```
@bot bind -p
→ フォーク可能なclaude CLIインスタンス:
   1 — PID 12345  📂 /Users/you/project-a  🕐 02:15:30
   2 — PID 67890  📂 /Users/you/project-b  🕐 00:30:10
  番号を入力して選択、または cancel でキャンセル

1
→ ✅ PID 12345 をフォークしました
```

PIDを直接指定することもできます:
```
@bot bind -p 12345
```

フォーク後の動作:
- **プロセス生存中**: スレッドへの返信がCLIに転送されます（I/O転送）
- **プロセス終了後**: スレッドへの返信で `--resume` による自動続行が始まります
- プロジェクトルートはCLIプロセスの作業ディレクトリに自動設定されます

### 許可ツールの設定

`DEFAULT_ALLOWED_TOOLS` の設定例:

```bash
# 保守的（読み取り中心）
DEFAULT_ALLOWED_TOOLS=Read,TodoWrite

# 標準的（ファイル編集 + git操作）
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite

# 積極的（ほぼ全操作を自動承認）
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(*),TodoWrite,WebSearch,WebFetch
```

`tools` コマンドでセッション単位で一時的に変更も可能（次の1タスクのみ有効）:
```
(スレッド内) tools Read,Write,Edit,Bash(*)
(スレッド内) npm run build の結果を見てエラーを修正して
```

### トラブルシューティング

#### Bridge が起動しない
- `SLACK_BOT_TOKEN` と `SLACK_APP_TOKEN` が正しいか確認
- Socket Mode が有効になっているか確認
- `pip install -r requirements.txt` でパッケージがインストール済みか確認

#### Bot が反応しない
- `SLACK_ALLOWED_USERS` と `SLACK_ALLOWED_CHANNELS` が設定されているか確認
- `message.channels` イベントが購読されているか確認
- botがチャンネルに招待されているか確認
- `@bot` メンションを付けているか確認
- ターミナルにエラーが出ていないか確認

#### Claude Code がエラーになる
- `which claude` でclaude コマンドのパスを確認
- `WORKING_DIR` が存在するか確認
- ターミナルで `claude -p "hello" --output-format json` が動くか確認
- Claude Code の認証が有効か確認（`claude` を直接起動して確認）

#### 日本語が文字化けする
- `.env` に `LANG=en_US.UTF-8` を追加してみる
- Python 3.9 以上を使用しているか確認

### 自動起動 (macOS)

Mac起動時に自動で立ち上げたい場合、LaunchAgent を使えます:

```bash
cat << 'EOF' > ~/Library/LaunchAgents/com.claude-slack-bridge.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-slack-bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/venv/bin/python</string>
        <string>/path/to/claude-slack-bridge/bridge.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/claude-slack-bridge</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-slack-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-slack-bridge.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.claude-slack-bridge.plist
```

## ライセンス / License

MIT
