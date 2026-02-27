"""
i18n — 環境変数 SLACK_LANGUAGE による日本語/英語切り替え。
デフォルトは "ja"。未知のキーは ja → キーそのまま でフォールバック。
"""

import os

MESSAGES: dict[str, dict[str, str]] = {
    "ja": {
        # ── help ──
        "help_text": (
            ":robot_face: *Claude Code Bridge* — 使い方:\n"
            "*基本操作:*\n"
            "• `@bot in <path> <タスク>` → 指定ディレクトリでタスクを実行\n"
            "• `@bot fork <PID> [<タスク>]` → 実行中のclaude CLIプロセスをフォーク\n"
            "• `@bot fork` → フォーク可能なプロセス一覧\n"
            "• `@bot <タスク>` → ディレクトリ選択画面から実行\n"
            "• (スレッド返信) `<指示>` → 同セッションで自動続行（メンション不要）\n"
            "*管理:*\n"
            "• `@bot status` → タスクの状態一覧\n"
            "• `@bot sessions` → セッション一覧\n"
            "• `@bot cancel #2` → タスクをキャンセル\n"
            "• `@bot cancel all` → 全タスクをキャンセル\n"
            "*設定:*\n"
            "• `@bot root <絶対パス>` → チャンネルのルートディレクトリを設定\n"
            "• `@bot root` → 現在のルートディレクトリを表示\n"
            "• `@bot root clear` → ルートディレクトリを解除\n"
            "• (スレッド内) `tools <tool1,...>` → 次回の許可ツール設定"
        ),

        # ── status ──
        "status_thinking": ":thought_balloon: _思考中..._ ({chars}文字)",
        "status_running": ":hourglass_flowing_sand: _実行中..._",
        "status_thinking_progress": ":hourglass_flowing_sand: _思考中..._",
        "status_cli_output_pending": ":hourglass: _CLIからの出力（確認中）_",
        "status_no_tasks": ":zzz: このチャンネルにはまだタスクがありません",
        "status_no_running_tasks": ":zzz: 実行中のタスクはありません",
        "status_no_running_with_recent": ":zzz: 実行中のタスクはありません\n*直近の完了タスク:*",
        "status_running_tasks_header": ":gear: *実行中のタスク ({count}セッション)*",
        "status_elapsed_seconds": "({elapsed:.0f}秒)",
        "status_elapsed_tools": "({elapsed:.0f}秒, ツール{tool_count}回)",
        "status_recent_tools": "最近: {tools}",
        "status_command_completed": "（コマンド実行完了）",
        "status_continued": "...(続き)",

        # ── task ──
        "task_start_header": "*タスク開始*",
        "task_resume_header": ":arrow_forward: *セッション続行*",
        "task_complete": ":white_check_mark: *タスク完了* ({elapsed:.0f}秒)",
        "task_cancelled": ":stop_sign: キャンセルされました",
        "task_failed": ":x: 失敗 (exit {code})",
        "task_error": ":x: エラー: {error}",
        "task_tools_used": "使用ツール ({count}回): {tools}",
        "task_tools_more": " 他{count}件",
        "task_see_full_file": "\n...(全文はファイルを参照)",
        "task_full_text_title": "全文",
        "status_history_title": "ステータス履歴",
        "task_reply_to_continue": "_このスレッドに返信すると自動で続行します_",
        "task_working_dir_not_set": "作業ディレクトリが未設定です",
        "task_cancel_count": ":stop_sign: {count}件のタスクをキャンセルしました",
        "task_cancel_request_sent": ":stop_sign: タスク #{task_id} のキャンセルリクエストを送信しました",
        "task_not_running": "タスク #{task_id} は実行中ではありません",
        "task_timeout_header": ":warning: PID {pid} _(タイムアウト — 部分的な応答)_",

        # ── error ──
        "error_cancel_specify": ":warning: キャンセルするタスクを指定してください: `cancel #2` or `cancel all`",
        "error_tools_thread_only": ":warning: `tools` コマンドはスレッド内でのみ有効です。タスクのスレッドに返信してください。",
        "error_continue_deprecated": ":warning: `continue` / `resume` は廃止されました。タスクのスレッドに返信すると自動で続行します。",
        "error_absolute_path_required": ":warning: 絶対パスで指定してください: `{path}`",
        "error_dir_not_found": ":warning: ディレクトリが見つかりません: `{path}`",
        "error_in_usage": ":warning: 使い方: `in <path> タスク内容`\n絶対パスで指定してください（`~` 展開あり）",
        "error_absolute_path_with_root_hint": (
            ":warning: 絶対パスで指定してください: `{path}`\n"
            "\U0001f4a1 `root <絶対パス>` でルートディレクトリを設定すると相対パスが使えます"
        ),
        "error_pid_not_number": ":warning: PIDは数字で指定してください: `fork <PID> [<task>]`",
        "error_enter_number_range": ":warning: 1〜{max} の番号を入力してください",
        "error_pid_no_tty": ":warning: PID {pid} にはTTYが接続されていません",
        "error_input_send_failed": ":x: 入力送信エラー: {error}",
        "error_need_working_dir": (
            ":warning: 作業ディレクトリを指定してください\n"
            "• `in <path> <タスク>` — 指定ディレクトリで実行\n"
            "• `fork <PID>` — 実行中のプロセスをフォーク"
        ),
        "error_enter_number_path_cancel": ":warning: 番号、絶対パス、または `cancel` を入力してください",

        # ── fork ──
        "fork_no_instances": ":mag: 実行中のclaude CLIインスタンスが見つかりません",
        "fork_pid_already_tracked": ":warning: PID {pid} は既に追跡中です",
        "fork_pid_not_found": ":warning: PID {pid} が見つかりません",
        "fork_no_forkable": ":mag: フォーク可能なclaude CLIインスタンスはありません",
        "fork_list_header": ":computer: *フォーク可能なclaude CLIインスタンス:*",
        "fork_select_or_cancel": "\n番号を入力して選択、または `cancel` でキャンセル",
        "fork_cancelled": ":x: フォーク選択をキャンセルしました",
        "fork_pid_exited": ":warning: PID {pid} は既に終了しています",
        "fork_session_id_not_found": (
            ":x: PID {pid} の session_id を取得できませんでした\n"
            ":file_folder: `{cwd}`\n"
            "JSONLファイルが見つからないか、session_id が含まれていません"
        ),
        "fork_success": (
            ":fork_and_knife: PID {pid} の文脈を引き継ぎました\n"
            ":file_folder: `{cwd}`{sid_info}\n"
            "_このスレッドに返信すると同じ文脈で新しいタスクを実行します_"
        ),

        # ── session ──
        "session_no_history": "セッション履歴はまだありません",
        "session_list_header": ":clipboard: *セッション一覧*",
        "session_task_count": "({count}タスク)",
        "session_reply_to_continue": "\n_タスクのスレッドに返信すると自動で続行します_",
        "session_pid_exited": ":stop_button: PID {pid} が終了しました",

        # ── dir (ディレクトリ選択) ──
        "dir_select_header": ":file_folder: *作業ディレクトリを選択してください:*",
        "dir_forkable_header": "\n:computer: *フォーク可能なプロセス:*",
        "dir_recent_header": "\n:clock1: *最近のディレクトリ:*",
        "dir_select_prompt": "\n番号で選択、絶対パスを入力、または `cancel` でキャンセル",
        "dir_cancelled": ":x: キャンセルしました",

        # ── input (入力転送) ──
        "input_sent": ":arrow_right: PID {pid} に入力を送信しました :white_check_mark:",
        "input_sent_waiting": ":arrow_right: PID {pid} に入力を送信しました :hourglass_flowing_sand:",
        "input_answer_sent": ":arrow_right: PID {pid} に回答を送信: {label} :white_check_mark:",
        "input_selected_option": "選択肢 {num} ({label}) を選択",

        # ── notify (起動/停止通知) ──
        "notify_startup": (
            ":rocket: *Claude Code Bridge が起動しました*\n"
            "チャンネルで `@bot in <path> <タスク>` を送信してください"
        ),
        "notify_shutdown": ":wave: Claude Code Bridge を停止しました",

        # ── question (CLI質問表示) ──
        "question_cli_header": ":question: *CLIからの質問*",
        "question_reply_with_number": "_番号を返信してください（テキストでOther回答も可）_",
        "question_multi_select_unsupported": "_複数選択が必要ですが、現在は未対応です。テキストで回答してください。_",
        "question_plan_approval_required": ":clipboard: *プランの承認が必要です*",
        "question_plan_truncated": "...(省略)",
        "question_approve_execute": "承認して実行",
        "question_reject_feedback": "却下・フィードバック",
        "question_reply_with_feedback": "_番号を返信してください（テキストでフィードバックも可）_",
        "question_allowed_prompts": "*許可プロンプト:*",
        "question_input_required": "入力が必要です",

        # ── root (ルートディレクトリ) ──
        "root_current": ":file_folder: このチャンネルのルートディレクトリ: `{path}`",
        "root_not_set": ":file_folder: このチャンネルにルートディレクトリは設定されていません\n`root <絶対パス>` で設定できます",
        "root_cleared": ":wastebasket: ルートディレクトリを解除しました（旧: `{old}`）",
        "root_already_not_set": ":file_folder: ルートディレクトリは設定されていません",
        "root_set": ":white_check_mark: ルートディレクトリを設定しました: `{path}`\n以降 `@bot <タスク>` で即座に実行されます",
        "root_dir_not_found": (
            ":warning: ルートディレクトリが見つかりません: `{path}`\n"
            "`root <絶対パス>` で再設定するか `root clear` で解除してください"
        ),

        # ── tools ──
        "tools_set": ":wrench: このセッションの次のタスクの許可ツール: `{tools}`\n続けてタスクを送信してください",

        # ── prompt (Claude向けプロンプト) ──
        "prompt_attached_files": "添付ファイル:",
        "prompt_answer_to_question": "以下の質問への回答です:\n質問: {question}",
        "prompt_answer_to_prev": "前の質問への回答です:",
        "prompt_options_label": "選択肢:",
        "prompt_answer_numbered": "回答: {num}. {label}",
        "prompt_answer_text": "回答: {label}",
        "prompt_system_append": (
            "ユーザーに質問や確認が必要な場合は、AskUserQuestionツールを使わず、"
            "テキストで質問を出力してください。"
            "質問を出力したら、その場で応答を終了してください。"
            "ユーザーは次のメッセージで回答します。"
            "質問の回答を待たずに処理を進めないでください。"
            "プランモードを終了する場合は、ExitPlanModeツールを使わず、"
            "プラン内容をテキストで出力してください。"
            "ユーザーがプランを承認した後、次のメッセージで実行を開始します。"
        ),
    },
    "en": {
        # ── help ──
        "help_text": (
            ":robot_face: *Claude Code Bridge* — Usage:\n"
            "*Basic:*\n"
            "• `@bot in <path> <task>` → Run a task in the specified directory\n"
            "• `@bot fork <PID> [<task>]` → Fork a running Claude CLI process\n"
            "• `@bot fork` → List forkable processes\n"
            "• `@bot <task>` → Run from directory selection\n"
            "• (thread reply) `<instruction>` → Continue in same session (no mention needed)\n"
            "*Management:*\n"
            "• `@bot status` → Show task status\n"
            "• `@bot sessions` → Show session list\n"
            "• `@bot cancel #2` → Cancel a task\n"
            "• `@bot cancel all` → Cancel all tasks\n"
            "*Settings:*\n"
            "• `@bot root <absolute-path>` → Set channel root directory\n"
            "• `@bot root` → Show current root directory\n"
            "• `@bot root clear` → Clear root directory\n"
            "• (in thread) `tools <tool1,...>` → Set allowed tools for next task"
        ),

        # ── status ──
        "status_thinking": ":thought_balloon: _Thinking..._ ({chars} chars)",
        "status_running": ":hourglass_flowing_sand: _Running..._",
        "status_thinking_progress": ":hourglass_flowing_sand: _Thinking..._",
        "status_cli_output_pending": ":hourglass: _CLI output (pending confirmation)_",
        "status_no_tasks": ":zzz: No tasks in this channel yet",
        "status_no_running_tasks": ":zzz: No running tasks",
        "status_no_running_with_recent": ":zzz: No running tasks\n*Recent completed tasks:*",
        "status_running_tasks_header": ":gear: *Running tasks ({count} sessions)*",
        "status_elapsed_seconds": "({elapsed:.0f}s)",
        "status_elapsed_tools": "({elapsed:.0f}s, {tool_count} tool calls)",
        "status_recent_tools": "Recent: {tools}",
        "status_command_completed": "(command completed)",
        "status_continued": "...(continued)",

        # ── task ──
        "task_start_header": "*Task started*",
        "task_resume_header": ":arrow_forward: *Session resumed*",
        "task_complete": ":white_check_mark: *Task completed* ({elapsed:.0f}s)",
        "task_cancelled": ":stop_sign: Cancelled",
        "task_failed": ":x: Failed (exit {code})",
        "task_error": ":x: Error: {error}",
        "task_tools_used": "Tools used ({count}): {tools}",
        "task_tools_more": " +{count} more",
        "task_see_full_file": "\n...(see full file)",
        "task_full_text_title": "Full text",
        "status_history_title": "Status history",
        "task_reply_to_continue": "_Reply to this thread to continue automatically_",
        "task_working_dir_not_set": "Working directory not set",
        "task_cancel_count": ":stop_sign: Cancelled {count} task(s)",
        "task_cancel_request_sent": ":stop_sign: Cancel request sent for task #{task_id}",
        "task_not_running": "Task #{task_id} is not running",
        "task_timeout_header": ":warning: PID {pid} _(timeout — partial response)_",

        # ── error ──
        "error_cancel_specify": ":warning: Please specify a task to cancel: `cancel #2` or `cancel all`",
        "error_tools_thread_only": ":warning: The `tools` command is only available in threads. Reply in the task's thread.",
        "error_continue_deprecated": ":warning: `continue` / `resume` are deprecated. Reply to the task's thread to continue automatically.",
        "error_absolute_path_required": ":warning: Please use an absolute path: `{path}`",
        "error_dir_not_found": ":warning: Directory not found: `{path}`",
        "error_in_usage": ":warning: Usage: `in <path> task description`\nPlease use an absolute path (`~` expansion supported)",
        "error_absolute_path_with_root_hint": (
            ":warning: Please use an absolute path: `{path}`\n"
            "\U0001f4a1 Set a root directory with `root <absolute-path>` to use relative paths"
        ),
        "error_pid_not_number": ":warning: PID must be a number: `fork <PID> [<task>]`",
        "error_enter_number_range": ":warning: Please enter a number from 1 to {max}",
        "error_pid_no_tty": ":warning: PID {pid} has no TTY attached",
        "error_input_send_failed": ":x: Input send error: {error}",
        "error_need_working_dir": (
            ":warning: Please specify a working directory\n"
            "• `in <path> <task>` — Run in specified directory\n"
            "• `fork <PID>` — Fork a running process"
        ),
        "error_enter_number_path_cancel": ":warning: Please enter a number, absolute path, or `cancel`",

        # ── fork ──
        "fork_no_instances": ":mag: No running Claude CLI instances found",
        "fork_pid_already_tracked": ":warning: PID {pid} is already being tracked",
        "fork_pid_not_found": ":warning: PID {pid} not found",
        "fork_no_forkable": ":mag: No forkable Claude CLI instances available",
        "fork_list_header": ":computer: *Forkable Claude CLI instances:*",
        "fork_select_or_cancel": "\nEnter a number to select, or `cancel` to abort",
        "fork_cancelled": ":x: Fork selection cancelled",
        "fork_pid_exited": ":warning: PID {pid} has already exited",
        "fork_session_id_not_found": (
            ":x: Could not retrieve session_id for PID {pid}\n"
            ":file_folder: `{cwd}`\n"
            "JSONL file not found or does not contain session_id"
        ),
        "fork_success": (
            ":fork_and_knife: Inherited context from PID {pid}\n"
            ":file_folder: `{cwd}`{sid_info}\n"
            "_Reply to this thread to run new tasks in the same context_"
        ),

        # ── session ──
        "session_no_history": "No session history yet",
        "session_list_header": ":clipboard: *Session list*",
        "session_task_count": "({count} tasks)",
        "session_reply_to_continue": "\n_Reply to a task's thread to continue automatically_",
        "session_pid_exited": ":stop_button: PID {pid} has exited",

        # ── dir ──
        "dir_select_header": ":file_folder: *Select a working directory:*",
        "dir_forkable_header": "\n:computer: *Forkable processes:*",
        "dir_recent_header": "\n:clock1: *Recent directories:*",
        "dir_select_prompt": "\nSelect by number, enter an absolute path, or `cancel` to abort",
        "dir_cancelled": ":x: Cancelled",

        # ── input ──
        "input_sent": ":arrow_right: Input sent to PID {pid} :white_check_mark:",
        "input_sent_waiting": ":arrow_right: Input sent to PID {pid} :hourglass_flowing_sand:",
        "input_answer_sent": ":arrow_right: Answer sent to PID {pid}: {label} :white_check_mark:",
        "input_selected_option": "Option {num} ({label}) selected",

        # ── notify ──
        "notify_startup": (
            ":rocket: *Claude Code Bridge started*\n"
            "Send `@bot in <path> <task>` in a channel to begin"
        ),
        "notify_shutdown": ":wave: Claude Code Bridge stopped",

        # ── question ──
        "question_cli_header": ":question: *Question from CLI*",
        "question_reply_with_number": "_Reply with a number (or text for Other)_",
        "question_multi_select_unsupported": "_Multiple selection is required but not yet supported. Please reply with text._",
        "question_plan_approval_required": ":clipboard: *Plan approval required*",
        "question_plan_truncated": "...(truncated)",
        "question_approve_execute": "Approve and execute",
        "question_reject_feedback": "Reject / Feedback",
        "question_reply_with_feedback": "_Reply with a number (or text for feedback)_",
        "question_allowed_prompts": "*Allowed prompts:*",
        "question_input_required": "Input required",

        # ── root ──
        "root_current": ":file_folder: Channel root directory: `{path}`",
        "root_not_set": ":file_folder: No root directory set for this channel\nSet one with `root <absolute-path>`",
        "root_cleared": ":wastebasket: Root directory cleared (was: `{old}`)",
        "root_already_not_set": ":file_folder: No root directory is set",
        "root_set": ":white_check_mark: Root directory set: `{path}`\nTasks via `@bot <task>` will run here immediately",
        "root_dir_not_found": (
            ":warning: Root directory not found: `{path}`\n"
            "Set a new one with `root <absolute-path>` or clear with `root clear`"
        ),

        # ── tools ──
        "tools_set": ":wrench: Allowed tools for next task in this session: `{tools}`\nSend a task to continue",

        # ── prompt (Claude向け — 英語圏でも理解可能) ──
        "prompt_attached_files": "Attached files:",
        "prompt_answer_to_question": "Answer to the following question:\nQuestion: {question}",
        "prompt_answer_to_prev": "Answer to the previous question:",
        "prompt_options_label": "Options:",
        "prompt_answer_numbered": "Answer: {num}. {label}",
        "prompt_answer_text": "Answer: {label}",
        "prompt_system_append": (
            "When you need to ask the user a question or confirmation, "
            "do not use the AskUserQuestion tool. Instead, output your question as text. "
            "After outputting the question, end your response immediately. "
            "The user will answer in the next message. "
            "Do not proceed without waiting for the answer. "
            "When exiting plan mode, do not use the ExitPlanMode tool. "
            "Instead, output your plan as text. "
            "The user will approve the plan, and execution will begin in the next message."
        ),
    },
}


def t(key: str, **kwargs) -> str:
    """翻訳キーからメッセージを取得。kwargs で format 変数を埋める。
    言語は毎回 os.getenv で取得する（load_dotenv() 後でも正しく動作するため）。"""
    lang = os.getenv("SLACK_LANGUAGE", "ja")
    msg = MESSAGES.get(lang, MESSAGES["ja"]).get(key)
    if msg is None:
        # フォールバック: ja → キーそのまま
        msg = MESSAGES["ja"].get(key, key)
    return msg.format(**kwargs) if kwargs else msg
