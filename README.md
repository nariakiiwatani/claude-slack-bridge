# Claude Code ⇔ Slack Bridge

Mac上で動くClaude Codeを、Slackから監視・操作するブリッジ。
複数タスクの同時実行に対応。

チャンネルで `@bot` メンションしてClaude Codeを操作します。チャンネルとプロジェクトルートを紐付けることで、ホストのディレクトリ構造を意識せず相対パスだけで操作できます。

## こんなとき便利

- トイレや休憩で席を離れるとき → スマホで進捗確認＆新しい指示
- 長時間タスクの完了通知をスマホで受け取りたい
- 移動中にふと思いついたタスクをすぐ投入したい
- チームメンバーと1台のMac上のClaude Codeを共有して使いたい

## セキュリティに関する注意

このツールはSlack経由で **ローカルMac上のClaude Code（=シェル実行が可能なCLI）をリモート操作** します。以下の点を理解した上でご利用ください。

- **管理者**: `.env` の `ADMIN_SLACK_USER_ID` に設定したユーザーは常にアクセスが許可されます
- **アクセス制御**: `SLACK_ALLOWED_USERS` / `SLACK_ALLOWED_CHANNELS` で許可されたユーザー・チャンネルのみ応答します。`*`（全許可）を設定する場合は、botが意図しないチャンネルに招待されるリスクに注意してください
- **トークン管理**: `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` が漏洩すると、第三者がBotを通じてClaude Codeを操作できる可能性があります。`.env` ファイルの権限を適切に設定し、Gitにコミットしないでください
- **許可ツールの設定**: `DEFAULT_ALLOWED_TOOLS` はClaude Codeが自動承認するツールを制御します。`Bash(*)` を設定すると任意のシェルコマンドが自動実行されます。必要最小限のツールのみ許可することを推奨します
- **自分のMac専用**: 共有サーバーやCI環境での利用は想定していません

## 仕組み

```
┌──────────┐   Socket Mode    ┌───────────────┐    subprocess ×N   ┌────────────┐
│  あなた   │ ─────────────► │               │ ──────────────► │ Claude Code│
│ (スマホ)  │    Channel      │    Bridge     │                  │   (CLI)    │
└──────────┘                  │  (Mac上で動作)  │ ──────────────► ├────────────┤
                              │               │                  │ Claude Code│
                              └───────────────┘                  │   (CLI)    │
                                      │                          └────────────┘
```

**ポイント:** Socket Modeを使うので、公開URL・ポートフォワード一切不要。

## セットアップ

### 1. Slack App を作成

1. [Slack API](https://api.slack.com/apps) にアクセス → **Create New App** → **From scratch**
2. アプリ名（例: `Claude Code Bridge`）とワークスペースを選択

#### Socket Mode を有効化
1. 左メニュー **Socket Mode** → **Enable Socket Mode**
2. Token Name に `claude-bridge` と入力 → **Generate**
3. 表示される `xapp-...` トークンをコピー → `.env` の `SLACK_APP_TOKEN` に設定

#### Bot Token Scopes を追加
1. 左メニュー **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** に以下を追加:
   - `chat:write` — メッセージ送信
   - `channels:history` — パブリックチャンネルのメッセージ読み取り
   - `groups:history` — プライベートチャンネルのメッセージ読み取り
   - `files:write` — ファイル送信（結果が大きい場合用）

#### Event Subscriptions を設定
1. 左メニュー **Event Subscriptions** → **Enable Events**
2. **Subscribe to bot events** に以下を追加:
   - `message.channels` — パブリックチャンネルのメッセージ
   - `message.groups` — プライベートチャンネルのメッセージ

#### Install App
1. 左メニュー **Install App** → **Install to Workspace** → 許可
2. **Bot User OAuth Token** (`xoxb-...`) をコピー → `.env` の `SLACK_BOT_TOKEN` に設定

### 2. プロジェクトのセットアップ

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

### 3. `.env` を編集

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

**ユーザーIDの確認方法:** Slackで自分のプロフィールを開く → 「…」→ 「メンバーIDをコピー」

### 4. 起動

```bash
python bridge.py
```

`NOTIFICATION_CHANNEL` を設定している場合、そのチャンネルに起動通知が届きます:
> :rocket: **Claude Code Bridge が起動しました**
> デフォルト作業ディレクトリ: `/Users/yourname/projects/my-project`

## 使い方

botをチャンネルに招待し、`@bot` メンション付きでコマンドを送信します。タスクのSlackスレッドへの返信はメンション不要でCLIに転送されます。

### コマンド一覧

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

### チャンネルとプロジェクトの紐付け

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

### 典型的なワークフロー

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

### bind -p: 実行中のCLIプロセスをチャンネルにフォーク

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

## 許可ツールの設定

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

## トラブルシューティング

### Bridge が起動しない
- `SLACK_BOT_TOKEN` と `SLACK_APP_TOKEN` が正しいか確認
- Socket Mode が有効になっているか確認
- `pip install -r requirements.txt` でパッケージがインストール済みか確認

### Bot が反応しない
- `SLACK_ALLOWED_USERS` と `SLACK_ALLOWED_CHANNELS` が設定されているか確認
- `message.channels` イベントが購読されているか確認
- botがチャンネルに招待されているか確認
- `@bot` メンションを付けているか確認
- ターミナルにエラーが出ていないか確認

### Claude Code がエラーになる
- `which claude` でclaude コマンドのパスを確認
- `WORKING_DIR` が存在するか確認
- ターミナルで `claude -p "hello" --output-format json` が動くか確認
- Claude Code の認証が有効か確認（`claude` を直接起動して確認）

### 日本語が文字化けする
- `.env` に `LANG=en_US.UTF-8` を追加してみる
- Python 3.9 以上を使用しているか確認

## 自動起動 (macOS)

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

## ライセンス

MIT
