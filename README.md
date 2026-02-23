# Claude Code ⇔ Slack Bridge 🔗

Mac上で動くClaude Codeを、スマホのSlackアプリから監視・操作するブリッジシステム。
複数タスクの同時実行＆チームでの共有利用に対応。

## こんなとき便利

- トイレや休憩で席を離れるとき → スマホで進捗確認＆新しい指示
- 長時間タスクの完了通知をスマホで受け取りたい
- 移動中にふと思いついたタスクをすぐ投入したい
- チームの共有Macで複数人がそれぞれのタスクを投げたい

## 仕組み

```
┌──────────┐                       ┌───────────────┐    subprocess ×N   ┌────────────┐
│  Alice   │                       │               │ ──────────────► │ Claude Code│
│ (スマホ)  │──┐  Socket Mode  ┌──│    Bridge     │                  │   (CLI)    │
└──────────┘  ├──────────────►│  │  (Mac上で動作)  │ ──────────────► ├────────────┤
┌──────────┐  │   双方向通信    │  │               │  stream-json     │ Claude Code│
│   Bob    │──┘               └──│  Per-user設定  │                  │   (CLI)    │
│ (スマホ)  │                       └───────────────┘                  └────────────┘
└──────────┘                              │
                                          │  macOS通知 (オプション)
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
   - `chat:write.public` — 未参加チャンネルにも投稿可
   - `app_mentions:read` — メンション検知
   - `channels:history` — チャンネル履歴読み取り
   - `groups:history` — プライベートチャンネル履歴
   - `im:history` — DM履歴（DM対応する場合）
   - `reactions:write` — リアクション
   - `files:write` — ファイル送信（結果が大きい場合用）

#### Event Subscriptions を設定
1. 左メニュー **Event Subscriptions** → **Enable Events**
2. **Subscribe to bot events** に以下を追加:
   - `app_mention`
   - `message.channels`
   - `message.im`（DM対応する場合）

#### Install App
1. 左メニュー **Install App** → **Install to Workspace** → 許可
2. **Bot User OAuth Token** (`xoxb-...`) をコピー → `.env` の `SLACK_BOT_TOKEN` に設定

### 2. プロジェクトのセットアップ

```bash
# クローンまたはコピー
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

# 通知チャンネル（Bot をチャンネルに招待するのを忘れずに！）
SLACK_CHANNEL_ID=C0123456789

# Claude Code の作業ディレクトリ
WORKING_DIR=/Users/yourname/projects/my-project

# 自動承認ツール（プロジェクトに合わせて調整）
DEFAULT_ALLOWED_TOOLS=Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite
```

**チャンネルIDの確認方法:** Slackでチャンネル名を右クリック → 「リンクをコピー」→ URLの末尾部分

### 4. Bot をチャンネルに招待

Slackの指定チャンネルで:
```
/invite @Claude Code Bridge
```

### 5. 起動

```bash
python bridge.py
```

起動するとSlackに通知が届きます:
> 🚀 **Claude Code Bridge が起動しました**
> 作業ディレクトリ: `/Users/yourname/projects/my-project`

## 使い方

### 基本操作（Slackから）

| コマンド | 説明 |
|---------|------|
| `@bot このリポジトリのREADMEを書いて` | 新しいタスクを実行 |
| `@bot in ~/other-project テスト書いて` | 指定ディレクトリで実行 |
| `@bot continue テストも追加して` | 自分の直前セッションを続行 |
| `@bot continue #2 エラーを修正して` | 指定タスクのセッションを続行 |
| `@bot resume abc123 エラーを修正して` | 指定セッションを再開 |
| `@bot status` | 全員のタスク一覧 |
| `@bot my` | 自分のタスクだけ表示 |
| `@bot cancel #2` | 自分のタスクをキャンセル |
| `@bot cancel all` | 自分の全タスクをキャンセル |
| `@bot cd /path/to/other/project` | 自分の作業ディレクトリを変更 |
| `@bot tools Read,Write,Bash(*)` | 次タスクの許可ツールを設定 |
| `@bot sessions` | 自分のセッション履歴 |
| `@bot admin cancel all` | 全員の全タスクを強制停止 *(admin)* |

### 典型的なワークフロー

各Claude Codeセッションは1つのSlackスレッドに対応します。
`continue` するとスレッド内に続行メッセージが追加され、会話の流れが一目でわかります。

```
あなた: @bot in ~/api  エラーハンドリングを改善して

━━ Slackチャンネル ━━━━━━━━━━━━━━━━━━━━
  🔵 #1  タスク開始 (by @あなた)
  📂 api
  └─ スレッド (3件)
     ├─ 🔵 #1  ⏳ 実行中... ツール: Read → Edit
     ├─ 🔵 #1  ✅ タスク完了 (45秒)
     │     `continue #1 <指示>` で続行（自動的に api/ で実行）
     │
     │  ← ここから continue #1 の続き ─────────
     │
     ├─ 🔵 #3  ▶️ セッション続行 (by @あなた)
     │     テストも書いて。jest を使って
     ├─ 🔵 #3  ⏳ 実行中... ツール: Read → Write
     └─ 🔵 #3  ✅ タスク完了 (30秒)

  🟢 #2  タスク開始 (by @あなた)     ← 別プロジェクトは別スレッド
  📂 web
  └─ スレッド (2件)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**ポイント:** `continue #1` すると #1 と同じスレッド・同じ色ラベルで続行されるので、
プロジェクト別の作業がスレッドごとにまとまります。

### マルチユーザーでの利用

チームで1台のMacを共有して使う場合、各自のタスクが独立して管理されます:

```
Alice:   @bot in ~/projects/api  認証のバグを修正して
Bob:     @bot in ~/projects/web  ダッシュボードのグラフを追加して

Bot:  🔵 #1  タスク開始 (by @Alice)  📂 api
Bot:  🟢 #2  タスク開始 (by @Bob)    📂 web

（Aliceの status 表示:）
⚙️ 実行中のタスク (2件)
🔵 #1 @Alice (自分)  📂 api (30秒, ツール5回)
🟢 #2 @Bob            📂 web (15秒, ツール2回)

（Bob が cancel all すると、#2 だけキャンセルされる）
（Alice の #1 は影響なし）
```

**ポイント:**
- `cd` は各ユーザーごとに独立（他人の作業ディレクトリには影響しない）
- `cancel all` は自分のタスクだけ停止
- `sessions` / `continue` は自分の履歴だけ参照
- `status` では全員のタスクが見える（誰が何をしているか一目瞭然）
- `admin cancel all` で管理者が全タスクを強制停止できる

### DMサポート

Botに直接DMすることもできます。その場合 `@bot` のメンションは不要です:

```
あなた: テストを全部実行して、失敗したものを修正して
Bot:    🔵 #3 タスク開始...
Bot:    🔵 #3 ✅ タスク完了 (28秒)
```

**DMとチャンネルの違い:**
- **チャンネル** → 進捗・結果はチャンネルのスレッドに投稿（チーム全員が見える）
- **DM** → 進捗・結果はDM内に返る（自分だけに見える）

`SLACK_CHANNEL_ID` はチャンネル経由で使う場合の投稿先です。DMのみで使う場合でも起動通知の送信先として必要なので、設定は省略できません。

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

`tools` コマンドで一時的に変更も可能:
```
@bot tools Read,Write,Edit,Bash(*)
@bot npm run build の結果を見てエラーを修正して
```

## トラブルシューティング

### Bridge が起動しない
- `SLACK_BOT_TOKEN` と `SLACK_APP_TOKEN` が正しいか確認
- Socket Mode が有効になっているか確認
- `pip install -r requirements.txt` でパッケージがインストール済みか確認

### Bot が反応しない
- Bot がチャンネルに招待されているか確認（`/invite @bot名`）
- Event Subscriptions で `app_mention` が設定されているか確認
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
# ~/Library/LaunchAgents/com.claude-slack-bridge.plist を作成
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

# 登録 & 起動
launchctl load ~/Library/LaunchAgents/com.claude-slack-bridge.plist
```

## ライセンス

MIT