# Claude Code ⇔ Slack Bridge

A bridge that lets you control Claude Code (CLI) running on your Mac from Slack. It supports concurrent task execution and uses Slack's Socket Mode, so no public URL or port forwarding is required.

Invite the bot to a Slack channel and send tasks with `@bot` mentions, specifying a working directory. Thread replies without mention are forwarded to the CLI or create resume tasks. Commands (like `cancel`, `status`, `tools`) always require `@bot` mention, even in threads. Each thread corresponds to a session, and replying to a completed task automatically resumes the session with `--resume`.

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

## Quick Setup

### 1. Create Slack App (using manifest)

1. Go to [Slack API](https://api.slack.com/apps) → **Create New App** → **From an app manifest**
2. Select your workspace
3. Paste the contents of [`slack-app-manifest.yml`](slack-app-manifest.yml) (select the YAML tab)
4. Click **Create**
5. Copy tokens to `.env` (the install script will prompt you):
   - **Settings > Basic Information > App-Level Tokens** → Generate (scope: `connections:write`) → `SLACK_APP_TOKEN` (`xapp-...`)
   - **OAuth & Permissions > Bot User OAuth Token** → `SLACK_BOT_TOKEN` (`xoxb-...`)

> If you prefer to create the app manually, see [Setup Guide](docs/setup-guide.md) for step-by-step instructions.

### 2. Install & Run

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
./scripts/install.sh
```

The install script handles everything:
- Checks prerequisites (Python 3.10+, Claude CLI)
- Creates virtual environment and installs dependencies
- Prompts for Slack tokens and admin user ID → writes `.env`
- Registers as a macOS LaunchAgent (auto-start on login)

### 3. Service management

```bash
./scripts/stop.sh       # Stop
./scripts/start.sh      # Start
./scripts/restart.sh    # Restart
tail -f ~/Library/Logs/claude-slack-bridge/stderr.log  # View logs
```

> For a detailed step-by-step guide, see [Setup Guide](docs/setup-guide.md).

## Usage

Invite the bot to a channel and send commands with `@bot` mention. Thread replies without mention are forwarded to the CLI or resume the session. Use `@bot` mention in threads for commands like `cancel`, `status`, `tools`.

### Command Reference

| Command | Description |
|---------|-------------|
| `@bot in <path> <task>` | Run task in specified directory (absolute path, `~` expansion supported) |
| `@bot <task>` | Select working directory from fork candidates / recent history, then run |
| (thread reply) `<instruction>` | Auto-resume with `--resume` (no `@bot` mention needed) |
| `@bot fork <PID> [<task>]` | Fork a running claude CLI process |
| `@bot fork` | List forkable claude CLI processes |
| `@bot bind <PID>` | Live-connect to a running terminal Claude CLI process |
| `@bot bind` | List bindable processes |
| `@bot status` | Show task status in project |
| `@bot sessions` | List sessions in project |
| `@bot cancel #2` | Cancel a task |
| `@bot cancel all` | Cancel all tasks in project |
| `@bot root <path>` | Set channel root directory (bare tasks run here directly) |
| `@bot root` | Show current root directory |
| `@bot root clear` | Clear root directory |
| `@bot tools <tool1,...>` | Set allowed tools for next task (per session, in thread) |
| `@bot help` | Show help |

### Specifying a Working Directory

Each task requires a working directory. There are three ways to specify one:

**1. `in <path>` — specify directly (absolute path required)**
```
@bot in /Users/yourname/projects/my-api Improve error handling
→ Runs in /Users/yourname/projects/my-api

@bot in ~/projects/my-api Write tests
→ ~ is expanded to your home directory
```

**2. Bare task — select from recent history (or use root directory)**

If you send `@bot <task>` without `in`, the bot shows a selection UI with forkable processes and recently used directories:
```
@bot Improve error handling
→ 📁 Select a working directory:
   🖥️ Forkable processes:
     1 — 🍴 PID 12345  📂 /Users/you/project-a  🕐 02:15:30
   🕐 Recent directories:
     2 — 📂 /Users/you/project-b
     3 — 📂 /Users/you/project-c
   Enter a number, an absolute path, or cancel
```

If a root directory is set for the channel (via `@bot root <path>`), bare tasks run immediately in that directory without the selection UI:
```
@bot root ~/projects/my-api
→ ✅ Root directory set: /Users/you/projects/my-api

@bot Improve error handling
→ Runs immediately in /Users/you/projects/my-api
```

**3. `fork` — take over a running CLI process**

See [Fork a Running CLI Process](#fork-a-running-cli-process) below.

### Typical Workflow

Each task corresponds to a single Slack thread (session = thread). After a task completes, replying to the same thread automatically resumes the session with `--resume`.

```
You: @bot in ~/projects/my-api Improve error handling

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

### Fork a Running CLI Process

`fork` lets you integrate a running Claude CLI process into the bridge.

```
@bot fork
→ Forkable claude CLI instances:
   1 — PID 12345  📂 /Users/you/project-a  🕐 02:15:30
   2 — PID 67890  📂 /Users/you/project-b  🕐 00:30:10
  Enter a number to select, or cancel to abort

1
→ ✅ Forked PID 12345
```

You can also specify the PID directly, optionally with an initial task:
```
@bot fork 12345
@bot fork 12345 Write tests for the changes you just made
```

After forking, the session continues in the same thread:
- Thread replies create new tasks with `--resume`, continuing the forked session's context
- The working directory is automatically set to the CLI process's working directory

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

You can also temporarily override tools per session using the `@bot tools` command in a thread (effective for the next task only):
```
@bot tools Read,Write,Edit,Bash(*)
Check the result of npm run build and fix the errors
```

## Troubleshooting

See [Setup Guide](docs/setup-guide.md#troubleshooting) for detailed troubleshooting steps.

View logs: `tail -f ~/Library/Logs/claude-slack-bridge/stderr.log`

## Auto-start (macOS)

`scripts/install.sh` sets up a LaunchAgent (`com.user.claude-slack-bridge`) that starts automatically on login.

```bash
./scripts/stop.sh       # Stop
./scripts/start.sh      # Start
./scripts/restart.sh    # Restart
tail -f ~/Library/Logs/claude-slack-bridge/stderr.log  # Logs
./scripts/uninstall.sh  # Uninstall
```

---

## 日本語

Mac上で動くClaude Codeを、Slackから監視・操作するブリッジ。
複数タスクの同時実行に対応。

チャンネルで `@bot` メンションしてClaude Codeを操作します。作業ディレクトリを指定してタスクを実行し、スレッドにメンションなしで返信すると同じセッションで自動続行します。コマンド（`cancel`・`status`・`tools`）はスレッド内でも `@bot` メンションが必要です。

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

### クイックセットアップ

#### 1. Slack App を作成（manifest使用）

1. [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From an app manifest**
2. ワークスペースを選択
3. [`slack-app-manifest.yml`](slack-app-manifest.yml) の内容を貼り付け（YAMLタブを選択）
4. **Create** をクリック
5. トークンを `.env` にコピー（インストールスクリプトが案内します）:
   - **Settings > Basic Information > App-Level Tokens** → Generate (scope: `connections:write`) → `SLACK_APP_TOKEN` (`xapp-...`)
   - **OAuth & Permissions > Bot User OAuth Token** → `SLACK_BOT_TOKEN` (`xoxb-...`)

> 手動でアプリを作成したい場合は [セットアップガイド](docs/setup-guide.md) を参照してください。

#### 2. インストール＆起動

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
./scripts/install.sh
```

インストールスクリプトがすべて処理します:
- 前提条件チェック（Python 3.10+、Claude CLI）
- 仮想環境の作成と依存パッケージのインストール
- Slackトークンと管理者IDの入力 → `.env` に書き込み
- macOS LaunchAgentとして登録（ログイン時に自動起動）

#### 3. サービス管理

```bash
./scripts/stop.sh       # 停止
./scripts/start.sh      # 起動
./scripts/restart.sh    # 再起動
tail -f ~/Library/Logs/claude-slack-bridge/stderr.log  # ログ確認
```

> 詳しい手順は [セットアップガイド](docs/setup-guide.md) を参照してください。

### 使い方

botをチャンネルに招待し、`@bot` メンション付きでコマンドを送信します。スレッド内のメンションなし返信はCLIに転送またはセッション続行になります。`cancel`・`status`・`tools` などのコマンドはスレッド内でも `@bot` メンションが必要です。

#### コマンド一覧

| コマンド | 説明 |
|---------|------|
| `@bot in <path> <タスク>` | 指定ディレクトリでタスクを実行（絶対パス、`~` 展開対応） |
| `@bot <タスク>` | フォーク候補・最近のディレクトリから選択して実行 |
| (スレッド返信) `<指示>` | 同セッションで `--resume` 自動続行（`@bot` メンション不要） |
| `@bot fork <PID> [<タスク>]` | 実行中のclaude CLIプロセスをフォーク |
| `@bot fork` | フォーク可能なプロセス一覧 |
| `@bot bind <PID>` | 実行中のターミナルClaude CLIプロセスにライブ接続 |
| `@bot bind` | バインド可能なプロセス一覧 |
| `@bot status` | タスクの状態一覧 |
| `@bot sessions` | セッション一覧 |
| `@bot cancel #2` | タスクをキャンセル |
| `@bot cancel all` | 全タスクをキャンセル |
| `@bot root <path>` | チャンネルのルートディレクトリを設定（ベアタスクが即実行される） |
| `@bot root` | 現在のルートディレクトリを表示 |
| `@bot root clear` | ルートディレクトリを解除 |
| `@bot tools <tool1,...>` | 次回タスクの許可ツール設定（セッション単位、スレッド内） |
| `@bot help` | ヘルプ表示 |

#### 作業ディレクトリの指定

タスクの実行には作業ディレクトリが必要です。3つの方法で指定できます:

**1. `in <path>` — 直接指定（絶対パス必須）**
```
@bot in /Users/yourname/projects/my-api エラーハンドリングを改善して
→ /Users/yourname/projects/my-api で実行

@bot in ~/projects/my-api テスト書いて
→ ~ はホームディレクトリに展開されます
```

**2. ベアタスク — 履歴から選択（またはルートディレクトリを使用）**

`in` なしで `@bot <タスク>` を送ると、フォーク候補と最近使ったディレクトリの選択UIが表示されます:
```
@bot エラーハンドリングを改善して
→ 📁 作業ディレクトリを選択してください:
   🖥️ フォーク可能なプロセス:
     1 — 🍴 PID 12345  📂 /Users/you/project-a  🕐 02:15:30
   🕐 最近のディレクトリ:
     2 — 📂 /Users/you/project-b
     3 — 📂 /Users/you/project-c
   番号で選択、絶対パスを入力、または cancel でキャンセル
```

チャンネルにルートディレクトリが設定されている場合（`@bot root <path>`）、ベアタスクは選択UIなしでそのディレクトリで即実行されます:
```
@bot root ~/projects/my-api
→ ✅ ルートディレクトリを設定しました: /Users/you/projects/my-api

@bot エラーハンドリングを改善して
→ /Users/you/projects/my-api で即座に実行
```

**3. `fork` — 実行中のCLIプロセスを引き継ぐ**

[実行中のCLIプロセスをフォーク](#実行中のcliプロセスをフォーク) を参照してください。

#### 典型的なワークフロー

1つのタスクは1つのSlackスレッドに対応します（セッション = スレッド）。
タスク完了後、同じスレッドに返信すると `--resume` で自動的にセッションが続行されます。

```
あなた: @bot in ~/projects/my-api エラーハンドリングを改善して

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

#### 実行中のCLIプロセスをフォーク

`fork` を使うと、Mac上で実行中のClaude CLIプロセスをBridgeに統合できます。

```
@bot fork
→ フォーク可能なclaude CLIインスタンス:
   1 — PID 12345  📂 /Users/you/project-a  🕐 02:15:30
   2 — PID 67890  📂 /Users/you/project-b  🕐 00:30:10
  番号を入力して選択、または cancel でキャンセル

1
→ ✅ PID 12345 をフォークしました
```

PIDを直接指定することもできます（タスクも同時に渡せます）:
```
@bot fork 12345
@bot fork 12345 さっきの変更にテストを書いて
```

フォーク後の動作:
- スレッドへの返信で `--resume` による自動続行が始まります
- 作業ディレクトリはCLIプロセスの作業ディレクトリに自動設定されます

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

`@bot tools` コマンドでセッション単位で一時的に変更も可能（スレッド内、次の1タスクのみ有効）:
```
@bot tools Read,Write,Edit,Bash(*)
npm run build の結果を見てエラーを修正して
```

### トラブルシューティング

問題が発生した場合は [セットアップガイド](docs/setup-guide.md#トラブルシューティング) を参照してください。

ログ確認: `tail -f ~/Library/Logs/claude-slack-bridge/stderr.log`

## ライセンス / License

MIT
