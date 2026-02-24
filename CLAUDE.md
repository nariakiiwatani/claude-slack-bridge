# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A bridge that lets users control Claude Code (CLI) from Slack on their Mac. Multiple tasks can run concurrently. Uses Slack Socket Mode (no public URL required).

Channel mode only: Whitelisted users/channels (`SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`). Requires `@bot` mention for top-level messages. Thread replies to tracked sessions are forwarded without mention.

Channels must be bound to project roots via `bind`/`unbind` commands before tasks can run. Unbound channels cannot execute tasks.

## Running

```bash
source venv/bin/activate
pip install -r requirements.txt
python bridge.py
```

Configuration is in `.env` (copy from `.env.example`). Required: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `ADMIN_SLACK_USER_ID`. Optional: `SLACK_ALLOWED_USERS`, `SLACK_ALLOWED_CHANNELS`, `NOTIFICATION_CHANNEL`, `WORKING_DIR`, `CLAUDE_CMD`, `DEFAULT_ALLOWED_TOOLS`, `LOG_LEVEL`.

## Architecture

Everything is in a single file: `bridge.py`. No tests exist.

### Data Model (3-Layer Hierarchy)

- **`Project`** (= Slack Channel) — Created by `bind`, requires `root_dir` (absolute path). Contains sessions. `unbind` removes the project. `ClaudeCodeRunner.projects` dict maps `channel_id → Project`.

- **`Session`** (= Slack Thread) — Created automatically when a task starts. Contains a serial chain of tasks. Holds `claude_session_id` (for `--resume`), label (emoji + name), optional `working_dir` override, and `next_tools` (one-shot tool overrides). Sessions are volatile (not persisted).

- **`Task`** (= Single Instruction → Completion) — One Claude Code subprocess invocation. Holds process handle, PTY master fd, tool call history, and `user_id`. Tasks within a session run serially. Thread replies automatically create new tasks with `--resume`.

### Key Components

- **`ClaudeCodeRunner`** — Core manager. `projects` dict replaces the old `active_tasks`/`task_history`. Handles project bind/unbind, task execution, and persistence. `run_task(project, session, task)` starts a daemon thread. `_execute(project, session, task)` derives channel_id, thread_ts, and cwd from the project/session hierarchy.

- **Access control** — `_is_user_allowed()` and `_is_channel_allowed()` check whitelists. `ADMIN_SLACK_USER_ID` is always allowed. `*` means allow all.

- **Slack event handler** — `handle_message` is the main event handler. Routes channel events (whitelisted, mention required for top-level). Thread reply routing: (1) `instance_threads` with active PTY → forward to CLI, (2) session exists with no active task → auto-resume via new task, (3) session exists with active task → forward to PTY. `_dispatch_command` is the command parser. `handle_mention` (`app_mention` event) is a no-op to avoid duplicate processing.

- **Notifications** — `NOTIFICATION_CHANNEL` (optional) receives startup/shutdown notifications and instance detection results. If unset, these are logged only.

- **Instance detection** — `detect_running_claude_instances()` finds existing `claude` CLI processes on the Mac. Detected instances get a Slack thread in `NOTIFICATION_CHANNEL`; replies to that thread are forwarded to the CLI via TTY. Instance state is persisted to `.instance_state.json` across restarts.

### Data Flow

1. Message event arrives via Socket Mode → `handle_message` routes by `channel_type`
2. Channel/user whitelist check → mention detection / thread reply routing → command parsing
3. `_dispatch_command` parses the command. For new tasks: gets/creates Project + Session, creates Task → `runner.run_task(project, session, task)` starts a daemon thread
4. Thread spawns `claude -p --verbose` subprocess with PTY, pipes prompt via stdin. Thread replies to existing sessions automatically use `--resume <session.claude_session_id>`
5. JSONL output file is monitored for progress; session's `claude_session_id` is updated from JSONL entries
6. On completion/failure, posts final result to Slack thread

### Session Continuity

Thread replies to a session's Slack thread automatically create new tasks with `--resume <session.claude_session_id>`. The old `continue` and `resume` commands are deprecated. Each session maintains its own `claude_session_id`, label, and working directory.

### Persistence

- `channel_projects.json` — Project `channel_id → root_dir` mapping only. Format unchanged from the old `_channel_projects` dict.
- `.instance_state.json` — External CLI instance tracking (PID → thread_ts).
- Sessions and Tasks are volatile (in-memory only, lost on bridge restart).

## Language

The codebase, comments, Slack messages, and README are all in Japanese. Maintain Japanese for user-facing Slack messages and code comments.
