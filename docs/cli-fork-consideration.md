# CLI検出 → フォーク機能の将来検討

## 背景

bridge.pyには既存のClaude CLIプロセスを検出し、Slackスレッドから操作する機能がある。
DM廃止に伴い、検出インスタンスの通知先が `NOTIFICATION_CHANNEL` に変更された。

## 現存するCLI検出関連コード

- `detect_running_claude_instances()` — `ps` コマンドでclaude CLIプロセスを検出（PID, CWD, TTY, 経過時間）
- `_get_process_etime()` / `_get_process_cwd()` — プロセス情報の取得
- `_register_instance()` — 検出インスタンスをSlackスレッドに登録、JSONL/ターミナル監視を開始
- `_monitor_terminal_output()` — Terminal.appのAppleScript APIでターミナル出力を監視
- `_monitor_session_jsonl()` — JONLファイルをポーリングして進捗をSlackに投稿
- `_handle_instance_input()` — スレッド返信をPTY書き込みまたはAppleScript経由でCLIに転送
- `_load_instance_state()` / `_save_instance_state()` — インスタンス状態の永続化（`.instance_state.json`）

## 将来検討: ワンショットモードでのフォーク

検出したCLIセッションから、ワンショットモード（`claude -p`）でフォークする機能。

### ユースケース
- ターミナルで対話的に作業中のセッションのコンテキストを引き継いで、Slackから別タスクを並行実行
- `--resume <session_id>` を使い、検出セッションのJSONLからsession_idを取得してフォーク

### 検討事項
- 対話セッションのsession_idを安全に取得する方法（JONLファイルからの逆引き）
- フォーク後のセッション分岐管理（元セッションとの干渉回避）
- ユーザーへの分かりやすいUI（どのセッションからフォークするか選択）

## DM廃止に伴う変更点

- `_register_instance()` の通知先が `dm_channel` → `NOTIFICATION_CHANNEL` に変更
- `NOTIFICATION_CHANNEL` 未設定時はインスタンス検出のSlack投稿はスキップ（ログのみ）
- インスタンス監視スレッドの `dm_channel` 引数も `NOTIFICATION_CHANNEL` に変更
