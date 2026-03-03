# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A bridge that lets users control Claude Code (CLI) from Slack on their Mac. Multiple tasks can run concurrently. Uses Slack Socket Mode (no public URL required).

Channel mode only: Whitelisted users/channels (`SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`). Requires `@bot` mention for all commands (top-level and in-thread). Thread replies without mention are forwarded to CLI or create resume tasks.

Tasks are started with a directory specified at invocation time via three methods:
- `@bot in <path> <task>` — run in specified directory
- `@bot fork <PID> [<task>]` — fork a running Claude CLI process
- `@bot <task>` — select from fork candidates / directory history in a thread

Terminal-Slack bidirectional sync:
- `@bot bind <PID>` — live-connect to a running terminal Claude CLI process (JSONL monitoring + input forwarding via AppleScript)
- `@bot bind` — list bindable processes
- Auto-takeover: when a terminal user `claude --resume`s a bridge-spawned session, the bridge detects it and switches to bind mode automatically. The user can find the session via `claude --resume` (no args) which opens an interactive picker listing recent sessions.

## Running

The bridge runs as a macOS LaunchAgent (`com.user.claude-slack-bridge`). Use `scripts/install.sh` for initial setup.

```bash
# Restart (stop + start via launchctl)
launchctl bootout gui/$(id -u)/com.user.claude-slack-bridge
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.claude-slack-bridge.plist

# Status
launchctl print gui/$(id -u)/com.user.claude-slack-bridge

# Logs
tail -f ~/Library/Logs/claude-slack-bridge/stderr.log
```

Do NOT start bridge.py directly with `python bridge.py` — always use launchctl.

Configuration is in `.env` (copy from `.env.example`). Required: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `ADMIN_SLACK_USER_ID`. Optional: `SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`, `NOTIFICATION_CHANNEL`, `CLAUDE_CMD`, `DEFAULT_ALLOWED_TOOLS`, `LOG_LEVEL`, `SLACK_LANGUAGE`.

## Architecture

Main logic is in `bridge.py`. Internationalization is in `i18n.py` (bilingual ja/en message table, controlled by `SLACK_LANGUAGE` env var). Tests are in `tests/`.

### Data Model (3-Layer Hierarchy)

- **`Project`** (= Slack Channel) — Session container. Created automatically when a task starts. `ClaudeCodeRunner.projects` dict maps `channel_id → Project`.

- **`Session`** (= Slack Thread) — Created automatically when a task starts. Contains a serial chain of tasks. Holds `claude_session_id` (for `--resume`), label (emoji + name), `working_dir` (required, set at task creation), and `next_tools` (one-shot tool overrides). Sessions are persisted to `sessions.json` for `--resume` restoration after bridge restart.

- **`Task`** (= Single Instruction → Completion) — One Claude Code subprocess invocation. Holds process handle, PTY master fd, tool call history, and `user_id`. Tasks within a session run serially. Thread replies automatically create new tasks with `--resume`.

### Key Components

- **`ClaudeCodeRunner`** — Core manager. `projects` dict maps channels to projects. `get_or_create_project(channel_id)` creates projects on demand. Manages directory history per channel. `run_task(project, session, task)` starts a daemon thread. `_execute(project, session, task)` uses `session.working_dir` as cwd.

- **Access control** — `_is_user_allowed()` and `_is_channel_allowed()` check whitelists. `ADMIN_SLACK_USER_ID` is always allowed. `*` means allow all.

- **Slack event handler** — `handle_message` is the main event handler. Routes channel events (whitelisted, mention required for top-level). Thread reply routing: (1) `instance_threads` registered → mention=command (`cancel`/`status`/`tools`), otherwise forward to CLI stdin, (1.5) `pending_directory_requests` / `pending_fork_selections` / `pending_bind_selections` → selection handling, (2) session exists → mention=command, otherwise auto-resume via new task, (3) fallback to command (mention required). `_dispatch_command` is the command parser. `handle_mention` (`app_mention` event) is a no-op to avoid duplicate processing.

- **Notifications** — `NOTIFICATION_CHANNEL` (optional) receives startup/shutdown notifications. If unset, these are logged only.

- **fork** — `detect_running_claude_instances()` finds existing `claude` CLI processes on the Mac. `fork <PID>` integrates a running process into the Project+Session model with I/O forwarding while alive and `--resume` continuation after death.

- **bind** — Live bidirectional connection to a terminal Claude CLI process. `bind <PID>` creates a Slack thread that mirrors the terminal session. Output is monitored via JSONL. Slack thread replies are forwarded to the terminal via AppleScript clipboard+paste. When the bound process dies, the session transitions to `--resume` mode. Selection state is stored in `pending_bind_selections`.

- **root** — `root <path>` sets a channel's default working directory (persisted in `channel_roots.json`). When set, bare tasks (`@bot <task>`) run immediately in that directory without the selection UI. `root` shows the current setting, `root clear` removes it. Handled by `_handle_root`.

- **External takeover** — When a bridge-spawned task is running and an external `claude --resume` process is detected with the same `session_id`, the bridge kills its subprocess, switches the `inst` to the external process, and continues monitoring. Active task takeover is checked every ~15s inside `_monitor_session_jsonl` (via `_TAKEOVER_CHECK_INTERVAL`). Idle session takeover (for sessions with no active task) is detected by a separate thread `_idle_takeover_monitor_loop` (via `_IDLE_TAKEOVER_INTERVAL`). Note: Slack thread displays a truncated session ID (first 12 chars) for reference only — to resume from terminal, use `claude --resume` without args to open the interactive session picker.

- **Bare task** — When `@bot <task>` is sent without `in` or `fork`, `_handle_bare_task` shows fork candidates and directory history for selection. Selection state is stored in `pending_directory_requests`.

### Data Flow

1. Message event arrives via Socket Mode → `handle_message` routes by `channel_type`
2. Channel/user whitelist check → mention detection / thread reply routing → command parsing
3. `_dispatch_command` parses the command. `in` → `_handle_in_dir` → `_start_task_in_dir`. `fork` → `_handle_fork` / `_handle_fork_list`. `bind` → `_handle_bind` / `_handle_bind_list` → `_execute_bind`. `root` → `_handle_root` (persists to `channel_roots.json`). Bare task → `_handle_bare_task` → directory selection → `_start_task_in_dir` or `_execute_fork`.
4. Thread spawns `claude -p --verbose` subprocess with PTY, pipes prompt via stdin. Thread replies to existing sessions automatically use `--resume <session.claude_session_id>`
5. JSONL output file is monitored for progress (including subagent JSONL files under `subagents/`); session's `claude_session_id` is updated from JSONL entries
6. On completion/failure, posts final result to Slack thread

### Session Continuity

Thread replies to a session's Slack thread automatically create new tasks with `--resume <session.claude_session_id>`. The old `continue` and `resume` commands are deprecated. Each session maintains its own `claude_session_id`, label, and working directory.

### Persistence

- `directory_history.json` — Per-channel directory usage history (channel_id → [dir_path, ...]). Max 10 entries per channel.
- `sessions.json` — Per-channel session data (claude_session_id, working_dir, label, etc.). Restored on bridge restart for `--resume` continuity. Unloaded channels' data is preserved across saves. Expired sessions (>7 days) are pruned. Max 50 sessions per channel.
- `channel_roots.json` — Per-channel root directory settings (channel_id → path). Used by `root` command. Bare tasks in channels with a root set run immediately in that directory.
- Tasks are volatile (in-memory only, lost on bridge restart).

## Language

The codebase, comments, and Slack messages are in Japanese. Documentation (README, setup guide) is bilingual (English + Japanese). Maintain Japanese for user-facing Slack messages and code comments. Tests exist in `tests/`.
