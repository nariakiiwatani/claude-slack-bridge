# 複数マシン運用ガイド / Multi-Machine Setup

Mac と Windows(WSL) など、**複数台のマシンで Claude Code ⇔ Slack Bridge を使い分ける**ための手順です。

[English below](#english)

---

## なぜ専用アプリ・専用チャンネルが必要か

1 つの Slack App（同じ `SLACK_APP_TOKEN`）を 2 台で**同時起動することはできません**。
Socket Mode は複数接続するとイベントを各クライアントに振り分け（ロードバランス）するため、
メッセージが片方にしか届かない・取りこぼす、という挙動になります。

そこで **マシンごとに別の Slack App を作り、専用チャンネルを割り当てます。**
別アプリ（別トークン）なら取り合いが起きないため、両方を常時起動したままにでき、
チャンネルを見れば「今どちらのマシンを操作しているか」が一目で分かります。

| | アプリ | チャンネル | `.env` |
|---|---|---|---|
| Mac | Claude Code (Mac) | `#claude-mac` | Mac ローカル |
| Windows(WSL) | Claude Code (Win) | `#claude-win` | WSL ローカル |

> **コードと manifest はマシン共通です。** `bridge.py` は manifest を読まず、
> 実行時に効くのは `.env` のトークンとチャンネル設定だけです。
> したがって**マシンごとに違うのは `.env` だけ**です（`.env` は `.gitignore` 済み・各マシンローカル）。

---

## セットアップ手順

### 1. 1 台目（Mac）をセットアップ

通常どおり [setup-guide.md](setup-guide.md) に従ってセットアップします。
1 つ目の Slack App を作り、`.env` にトークンを設定します。

### 2. 2 台目（Windows/WSL）用の Slack App を作る

[Slack API](https://api.slack.com/apps) で **もう 1 つアプリを作成**します。
manifest は 1 台目と同じ [`slack-app-manifest.yml`](../slack-app-manifest.yml) を流用できます。

1. **Create New App** → **From an app manifest** → ワークスペースを選択
2. `slack-app-manifest.yml` の内容を貼り付けて **Create**
3. 作成後、見分けがつくよう**表示名を変更**:
   **Settings > Basic Information > Display Information** で
   App name を例えば `Claude Code (Win)` に変更 → **Save Changes**
   （1 台目も `Claude Code (Mac)` などに変えておくと分かりやすい）
4. トークンを取得（[setup-guide.md](setup-guide.md) の手順 2 と同じ）:
   - App-Level Token（`xapp-...`）= `SLACK_APP_TOKEN`
   - Bot User OAuth Token（`xoxb-...`）= `SLACK_BOT_TOKEN`

### 3. 専用チャンネルを作り、各 bot を招待

Slack で 2 つのチャンネルを作成（例: `#claude-mac` / `#claude-win`）。

**それぞれのチャンネルに、対応する bot を招待します。**
bot は招待されたチャンネルのメッセージしか受け取れません（Slack の仕様）。

```
#claude-mac で:   /invite @Claude Code (Mac)
#claude-win で:   /invite @Claude Code (Win)
```

各チャンネルの ID は、チャンネル名をクリック → 最下部の **チャンネル ID**（`C...`）で確認できます。

### 4. 各マシンの `.env` を設定

マシンごとに、**自分のアプリのトークン**と**自分のチャンネル**を設定します。

**Mac の `.env`:**
```bash
SLACK_BOT_TOKEN=xoxb-...(Mac アプリのもの)
SLACK_APP_TOKEN=xapp-...(Mac アプリのもの)
SLACK_ALLOWED_CHANNELS=C_mac   # #claude-mac の ID
```

**Windows(WSL) の `.env`:**
```bash
SLACK_BOT_TOKEN=xoxb-...(Win アプリのもの)
SLACK_APP_TOKEN=xapp-...(Win アプリのもの)
SLACK_ALLOWED_CHANNELS=C_win   # #claude-win の ID
```

`ADMIN_SLACK_USER_ID` など他の項目は共通で構いません。

### 5. 起動

両マシンとも別アプリなので、**同時に起動したままで OK** です。
Windows(WSL) 側の起動・停止は次のとおり（macOS の LaunchAgent は使いません）:

```bash
# 起動（フォアグラウンド・Ctrl+C で停止）
cd ~/claude-slack-bridge && source venv/bin/activate && python bridge.py

# バックグラウンド起動
cd ~/claude-slack-bridge && source venv/bin/activate && nohup python bridge.py > bridge.log 2>&1 &

# 停止（バックグラウンドの場合）
pkill -f bridge.py
```

Mac 側は通常どおり `scripts/start.sh` / `scripts/stop.sh` / `scripts/restart.sh` を使います。

---

## 使い方と注意点

- **操作したいマシンのチャンネルで話しかける** だけです。`#claude-mac` なら Mac、`#claude-win` なら WSL の Claude が動きます。
- **会話の続き（`--resume`）はマシンをまたげません。** `claude_session_id` と作業ディレクトリのパスは各マシンローカルのため、別マシンで同じセッションを再開することはできません。マシンを切り替えたら新しいタスクとして始めてください。
- **うっかり別マシンのチャンネルに、その bot が招待されていない／停止中の場合** はメッセージが届かず無反応になるだけで、実害はありません。

---
---

<a name="english"></a>

# Multi-Machine Setup (English)

Use Claude Code ⇔ Slack Bridge across **multiple machines** (e.g. a Mac and Windows/WSL).

## Why a dedicated app & channel per machine

You **cannot run the same Slack App (same `SLACK_APP_TOKEN`) on two machines at once.**
Socket Mode load-balances events across connections, so messages would reach only one
machine or be dropped.

The fix is **one Slack App and one dedicated channel per machine.** Separate apps
(separate tokens) never contend, so both can stay running, and the channel tells you at a
glance which machine you are driving.

| | App | Channel | `.env` |
|---|---|---|---|
| Mac | Claude Code (Mac) | `#claude-mac` | local to Mac |
| Windows(WSL) | Claude Code (Win) | `#claude-win` | local to WSL |

> **Code and manifest are shared across machines.** `bridge.py` never reads the manifest;
> only the tokens and channel settings in `.env` matter at runtime. So **the only thing
> that differs per machine is `.env`** (which is gitignored and machine-local).

## Steps

1. **Set up machine #1 (Mac)** normally via [setup-guide.md](setup-guide.md).
2. **Create a second Slack App** for machine #2 from the same
   [`slack-app-manifest.yml`](../slack-app-manifest.yml). After creating it, rename it under
   **Basic Information > Display Information** (e.g. `Claude Code (Win)`) so the two apps are
   distinguishable, then grab its App-Level and Bot tokens.
3. **Create two channels** (e.g. `#claude-mac`, `#claude-win`) and **invite the matching bot
   into each** — a bot only receives messages from channels it has joined:
   ```
   in #claude-mac:  /invite @Claude Code (Mac)
   in #claude-win:  /invite @Claude Code (Win)
   ```
4. **Configure each machine's `.env`** with its own app tokens and its own
   `SLACK_ALLOWED_CHANNELS` (the matching channel ID).
5. **Run both** — separate apps, so they can stay up simultaneously. On WSL there is no
   LaunchAgent; start/stop directly:
   ```bash
   # foreground (Ctrl+C to stop)
   cd ~/claude-slack-bridge && source venv/bin/activate && python bridge.py
   # background
   cd ~/claude-slack-bridge && source venv/bin/activate && nohup python bridge.py > bridge.log 2>&1 &
   # stop a backgrounded instance
   pkill -f bridge.py
   ```

## Usage notes

- Talk in the channel of the machine you want to drive.
- **Sessions cannot resume across machines** — `claude_session_id` and working-dir paths are
  machine-local. Switching machines means starting a new task.
- Replying in the *other* machine's channel while its bot is offline (or not invited) simply
  does nothing — no harm done.

---

← [Back to README](../README.md) · [Setup Guide](setup-guide.md)
