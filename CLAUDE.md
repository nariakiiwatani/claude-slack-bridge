# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A bridge that lets a single user control Claude Code (CLI) from Slack DM on their Mac. Multiple tasks can run concurrently. Uses Slack Socket Mode (no public URL required).

## Running

```bash
source venv/bin/activate
pip install -r requirements.txt
python bridge.py
```

Configuration is in `.env` (copy from `.env.example`). Required: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_USER_ID`. Optional: `WORKING_DIR`, `CLAUDE_CMD`, `DEFAULT_ALLOWED_TOOLS`, `MACOS_NOTIFICATION`.

## Architecture

Everything is in a single file: `bridge.py`. No tests exist.

### Key Components

- **`ClaudeCodeRunner`** — Core task manager. Tracks active tasks (`active_tasks` dict) and history (`task_history` list). Each task spawns a Claude Code subprocess via `subprocess.Popen` with PTY. Tasks run in daemon threads (`_execute` method). Monitors Claude Code's JSONL output file for progress tracking.

- **`Task` dataclass** — Represents one Claude Code invocation. Holds subprocess handle, PTY master fd, session ID (for continue/resume), Slack thread_ts (for thread grouping), and tool call history.

- **`UserSettings`** — Module-level volatile settings instance (working directory, one-shot tool overrides). Resets on bridge restart.

- **Slack event handler** — `handle_dm` is the main command dispatcher. Only processes DMs from the configured `SLACK_USER_ID`. Commands are parsed directly from message text (no mention prefix needed). `handle_mention` exists but is a no-op (DM-only design).

- **Instance detection** — `detect_running_claude_instances()` finds existing `claude` CLI processes on the Mac. Detected instances get a Slack thread; replies to that thread are forwarded to the CLI via TTY. Instance state is persisted to `.instance_state.json` across restarts.

### Data Flow

1. DM event arrives via Socket Mode → `handle_dm` filters by `SLACK_USER_ID` and parses command
2. Command creates a `Task` with user's settings → `runner.run_task()` starts a daemon thread
3. Thread spawns `claude -p --verbose` subprocess with PTY, pipes prompt via stdin
4. JSONL output file is monitored for progress; Slack thread is updated periodically
5. On completion/failure, posts final result to Slack thread and optionally sends macOS notification

### Session Continuity

`continue` and `resume` commands reuse Claude Code's `--continue` / `--resume` flags with stored `session_id`. When continuing, the new task inherits the original task's Slack thread, color label, and working directory.

## Language

The codebase, comments, Slack messages, and README are all in Japanese. Maintain Japanese for user-facing Slack messages and code comments.
