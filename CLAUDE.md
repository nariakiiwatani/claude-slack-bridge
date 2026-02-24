# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A bridge that lets users control Claude Code (CLI) from Slack on their Mac. Multiple tasks can run concurrently. Uses Slack Socket Mode (no public URL required).

Channel mode only: Whitelisted users/channels (`SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`). Requires `@bot` mention. Thread replies to tracked tasks are forwarded without mention.

Channels can be bound to project roots via `bind`/`unbind` commands, allowing users to work with relative paths without knowing the host directory structure.

## Running

```bash
source venv/bin/activate
pip install -r requirements.txt
python bridge.py
```

Configuration is in `.env` (copy from `.env.example`). Required: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `ADMIN_SLACK_USER_ID`. Optional: `SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`, `NOTIFICATION_CHANNEL`, `WORKING_DIR`, `CLAUDE_CMD`, `DEFAULT_ALLOWED_TOOLS`, `LOG_LEVEL`.

## Architecture

Everything is in a single file: `bridge.py`. No tests exist.

### Key Components

- **`ClaudeCodeRunner`** — Core task manager. Tracks active tasks (`active_tasks` dict) and history (`task_history` list). Each task spawns a Claude Code subprocess via `subprocess.Popen` with PTY. Tasks run in daemon threads (`_execute` method). Monitors Claude Code's JSONL output file for progress tracking.

- **`Task` dataclass** — Represents one Claude Code invocation. Holds subprocess handle, PTY master fd, session ID (for continue/resume), Slack thread_ts (for thread grouping), tool call history, and `user_id` (who started the task).

- **`UserSettings`** — Module-level volatile settings instance (one-shot tool overrides). Shared across all users. Resets on bridge restart.

- **Channel-project binding** — `_channel_projects` dict maps channel_id to absolute project root path. Persisted to `channel_projects.json`. Commands: `bind <path>` / `unbind`. Working directory resolution: `_get_working_dir_for_channel()` returns the bound root or falls back to `WORKING_DIR`.

- **Access control** — `_is_user_allowed()` and `_is_channel_allowed()` check whitelists. `ADMIN_SLACK_USER_ID` is always allowed. `*` means allow all.

- **Slack event handler** — `handle_message` is the main event handler. Routes channel events (whitelisted, mention required). DM messages are ignored. `_dispatch_command` is the command parser. `handle_mention` (`app_mention` event) is a no-op to avoid duplicate processing.

- **Notifications** — `NOTIFICATION_CHANNEL` (optional) receives startup/shutdown notifications and instance detection results. If unset, these are logged only.

- **Instance detection** — `detect_running_claude_instances()` finds existing `claude` CLI processes on the Mac. Detected instances get a Slack thread in `NOTIFICATION_CHANNEL`; replies to that thread are forwarded to the CLI via TTY. Instance state is persisted to `.instance_state.json` across restarts.

### Data Flow

1. Message event arrives via Socket Mode → `handle_message` routes by `channel_type`
2. Channel/user whitelist check → mention detection → command parsing
3. `_dispatch_command` parses the command and creates a `Task` → `runner.run_task()` starts a daemon thread
4. Thread spawns `claude -p --verbose` subprocess with PTY, pipes prompt via stdin
5. JSONL output file is monitored for progress; Slack thread is updated periodically
6. On completion/failure, posts final result to Slack thread

### Session Continuity

`continue` and `resume` commands reuse Claude Code's `--continue` / `--resume` flags with stored `session_id`. When continuing, the new task inherits the original task's Slack thread, color label, and working directory.

## Language

The codebase, comments, Slack messages, and README are all in Japanese. Maintain Japanese for user-facing Slack messages and code comments.
