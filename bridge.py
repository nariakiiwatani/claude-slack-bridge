#!/usr/bin/env python3
"""
Claude Code ⇔ Slack Bridge
===========================
Mac上で動くClaude CodeをSlackから操作するブリッジ。
複数タスクの同時実行に対応。チャンネルモードのみ（@bot メンション必須）。

チャンネルとプロジェクトルートを紐付けることで、ユーザーはホストマシンの
ディレクトリ構造を意識せず、チャンネル内で相対パスだけで操作可能。

コマンド:
  <タスク内容>              → 新しいタスクを実行
  in <path> <タスク>        → 指定ディレクトリでタスクを実行（相対パスはプロジェクトルート基準）
  continue [#id] <指示>     → セッションを続行（#id省略で直前タスク）
  status                    → 全タスクの状態一覧
  cancel #id                → タスクをキャンセル
  cancel all                → 全タスクをキャンセル
  bind <path>               → チャンネルにプロジェクトルートを紐付け
  unbind                    → プロジェクトルートの紐付けを解除
  tools <list>              → 次回タスクの許可ツール設定
  sessions                  → セッション履歴
  resume <session_id> <指示> → 指定セッションを再開
  detect                    → 実行中のclaude CLIインスタンスを検出・接続
"""

import fcntl
import json
import logging
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
# 管理者ユーザーID（必須）。アクセス制御に使用。
ADMIN_SLACK_USER_ID = os.environ["ADMIN_SLACK_USER_ID"]
# 許可ユーザー（カンマ区切り or "*" で全許可）
SLACK_ALLOWED_USERS = os.getenv("SLACK_ALLOWED_USERS", "")
# 許可チャンネル（カンマ区切り or "*" で全許可）
SLACK_ALLOWED_CHANNELS = os.getenv("SLACK_ALLOWED_CHANNELS", "")
# 通知チャンネル（起動/停止通知の送信先。未設定ならログのみ）
NOTIFICATION_CHANNEL = os.getenv("NOTIFICATION_CHANNEL", "")
CLAUDE_CMD = os.getenv("CLAUDE_CMD", "claude")
WORKING_DIR = os.getenv("WORKING_DIR", os.getcwd())
DEFAULT_ALLOWED_TOOLS = os.getenv(
    "DEFAULT_ALLOWED_TOOLS",
    "Read,Write,Edit,MultiEdit,Bash(git *),TodoWrite",
)

MAX_SLACK_MSG_LENGTH = 3000
INSTANCE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".instance_state.json")
CHANNEL_PROJECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_projects.json")

# チャンネル→プロジェクトルート紐付け（channel_id → 絶対パス）
_channel_projects: dict[str, str] = {}


def _load_channel_projects():
    """channel_projects.json からチャンネル→プロジェクトルート紐付けを読み込み"""
    global _channel_projects
    try:
        with open(CHANNEL_PROJECTS_FILE, "r") as f:
            _channel_projects = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _channel_projects = {}
    except Exception as e:
        logger.warning("チャンネルプロジェクト設定の読み込みに失敗: %s", e)
        _channel_projects = {}


def _save_channel_projects():
    """チャンネル→プロジェクトルート紐付けをファイルに永続化"""
    try:
        with open(CHANNEL_PROJECTS_FILE, "w") as f:
            json.dump(_channel_projects, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("チャンネルプロジェクト設定の保存に失敗: %s", e)


def _get_channel_project_root(channel_id: str) -> str | None:
    """チャンネルに紐付けられたプロジェクトルートを返す。未設定ならNone"""
    return _channel_projects.get(channel_id)


def _is_user_allowed(user_id: str) -> bool:
    """ユーザーが操作を許可されているか（チャンネルモード用）"""
    if user_id == ADMIN_SLACK_USER_ID:
        return True  # 管理者は常に許可
    if SLACK_ALLOWED_USERS == "*":
        return True
    allowed = {u.strip() for u in SLACK_ALLOWED_USERS.split(",") if u.strip()}
    return user_id in allowed


def _is_channel_allowed(channel_id: str) -> bool:
    """チャンネルが許可されているか"""
    if SLACK_ALLOWED_CHANNELS == "*":
        return True
    allowed = {c.strip() for c in SLACK_ALLOWED_CHANNELS.split(",") if c.strip()}
    return channel_id in allowed


TASK_LABELS = [
    ("🔵", "blue"),
    ("🟢", "green"),
    ("🟡", "yellow"),
    ("🟣", "purple"),
    ("🟠", "orange"),
]


# ── データ構造 ────────────────────────────────────────────
class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: int
    prompt: str
    channel_id: str = ""  # DMチャンネルID
    label_emoji: str = ""
    label_name: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    session_id: Optional[str] = None
    thread_ts: Optional[str] = None
    process: Optional[subprocess.Popen] = None
    result: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    resume_session: Optional[str] = None
    continue_last: bool = False
    continue_session_id: Optional[str] = None
    allowed_tools: Optional[str] = None
    working_dir: Optional[str] = None
    tool_calls: list = field(default_factory=list)
    error: Optional[str] = None
    master_fd: Optional[int] = None
    user_id: Optional[str] = None  # タスク実行者のSlack User ID

    @property
    def short_id(self) -> str:
        return f"#{self.id}"

    @property
    def display_label(self) -> str:
        return f"{self.label_emoji} {self.short_id}"


# ── ユーザー設定（モジュールレベル） ──────────────────────
@dataclass
class UserSettings:
    """個別設定（揮発性：Bridge再起動でリセット）"""
    next_tools: Optional[str] = None

    def consume_tools(self) -> Optional[str]:
        """next_tools を取り出してリセット"""
        tools = self.next_tools
        self.next_tools = None
        return tools


def _get_working_dir_for_channel(channel_id: str) -> str:
    """チャンネルに紐付けられたプロジェクトルート、またはデフォルト作業ディレクトリを返す"""
    return _get_channel_project_root(channel_id) or WORKING_DIR


# ── 実行中のclaude CLIプロセス検出 ───────────────────────
def detect_running_claude_instances() -> list[dict]:
    """Mac上で動作中のclaude CLIプロセスを検出し、PID・CWD・TTY・経過時間を返す"""
    instances = []
    try:
        # ps でコマンドが "claude" のプロセスを検出（PID, TTY, COMM）
        result = subprocess.run(
            ["ps", "-eo", "pid,tty,comm"],
            capture_output=True, text=True, timeout=5,
        )
        pids = []
        tty_map = {}
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and os.path.basename(parts[2]) == "claude":
                pid = int(parts[0])
                tty = parts[1]  # e.g., "ttys021"
                pids.append(pid)
                tty_map[pid] = tty
        if not pids:
            return []

        for pid in pids:
            etime = _get_process_etime(pid)
            cwd = _get_process_cwd(pid)
            tty = tty_map.get(pid, "??")
            instances.append({
                "pid": pid,
                "cwd": cwd or "(unknown)",
                "etime": etime or "?",
                "tty": tty,
            })
    except Exception as e:
        logger.warning("claudeプロセス検出エラー: %s", e)
    return instances


def _get_process_etime(pid: int) -> Optional[str]:
    """プロセスの経過時間を取得 (例: "02:15:30", "1-03:22:10")"""
    try:
        result = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _get_process_cwd(pid: int) -> Optional[str]:
    try:
        result = subprocess.run(
            ["lsof", "-p", str(pid), "-a", "-d", "cwd", "-F", "n"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("n/"):
                return line[1:]
    except Exception:
        pass
    return None


# ── ターミナル出力モニタリング ─────────────────────────────
TERMINAL_POLL_INTERVAL = 3.0


def _read_terminal_contents(tty_device: str) -> Optional[str]:
    """Terminal.appのAppleScript APIでタブのスクロールバック+表示内容を取得"""
    script = f'''
tell application "Terminal"
    set targetTTY to "{tty_device}"
    repeat with i from 1 to count of windows
        set w to window i
        repeat with j from 1 to count of tabs of w
            if tty of tab j of w is targetTTY then
                return history of tab j of w
            end if
        end repeat
    end repeat
end tell
return ""
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── セッションJSONL監視 ──────────────────────────────────
def _find_session_jsonl(cwd: str, min_mtime: float = 0, min_ctime: float = 0) -> Optional[str]:
    """CWDからclaude CLIのセッションJSONLファイルパスを特定。
    JONLファイル内のcwdフィールドで逆引きマッチする。
    min_ctime: ファイル作成時刻(st_birthtime)がこの値以降のもののみ対象（macOS用）。"""
    if not cwd or cwd == "(unknown)":
        return None
    projects_base = Path.home() / ".claude" / "projects"
    if not projects_base.is_dir():
        return None
    # 全プロジェクトディレクトリ内の最新.jsonlファイルからcwdが一致するものを探す
    best_path: Optional[str] = None
    best_mtime: float = 0
    for proj_dir in projects_base.iterdir():
        if not proj_dir.is_dir():
            continue
        jsonl_files = list(proj_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue
        # min_ctimeが指定されている場合、作成時刻でフィルタ
        if min_ctime:
            jsonl_files = [f for f in jsonl_files
                           if getattr(f.stat(), 'st_birthtime', f.stat().st_mtime) >= min_ctime]
            if not jsonl_files:
                continue
        # 最新のjsonlを候補にする
        latest = max(jsonl_files, key=lambda f: f.stat().st_mtime)
        mtime = latest.stat().st_mtime
        # JONLの先頭数行からcwdを確認（file-history-snapshot等はcwdなし）
        try:
            matched = False
            with open(latest, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 5:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entry_cwd = entry.get("cwd", "")
                    if entry_cwd == cwd:
                        matched = True
                        break
                    elif entry_cwd:
                        break  # 別のcwdなので不一致
            if matched and mtime > best_mtime and mtime >= min_mtime:
                best_mtime = mtime
                best_path = str(latest)
        except OSError:
            continue
    return best_path


def _classify_jsonl_entry(entry: dict) -> list[tuple[str, str, dict | None]]:
    """JSONLエントリを (category, text, metadata) のリストに分類。
    category: "status" (thinking/tool_use), "text" (応答テキスト),
              "question" (AskUserQuestion選択肢), None (スキップ)
    metadata: "question"の場合に質問データを格納、それ以外はNone"""
    entry_type = entry.get("type", "")
    msg = entry.get("message", {})
    if not isinstance(msg, dict):
        return []

    role = msg.get("role", "")
    content = msg.get("content", [])
    if not isinstance(content, list):
        return []

    results = []
    for c in content:
        if not isinstance(c, dict):
            continue
        c_type = c.get("type", "")

        if entry_type == "assistant" and c_type == "thinking":
            text = c.get("thinking", "")
            results.append(("status", f":thought_balloon: _思考中..._ ({len(text)}文字)", None))

        elif entry_type == "assistant" and c_type == "text":
            text = c.get("text", "").strip()
            if text:
                results.append(("text", text, None))

        elif entry_type == "assistant" and c_type == "tool_use":
            tool_name = c.get("name", "?")
            tool_input = c.get("input", {})

            # AskUserQuestion検出
            if tool_name == "AskUserQuestion":
                formatted, meta = _format_ask_user_question(tool_input)
                if formatted:
                    results.append(("question", formatted, meta))
                    continue

            summary = _summarize_input(tool_input) if isinstance(tool_input, dict) else str(tool_input)[:80]
            if summary:
                results.append(("status", f":wrench: `{tool_name}` {summary}", None))
            else:
                results.append(("status", f":wrench: `{tool_name}`", None))

    return results


def _format_ask_user_question(tool_input: dict) -> tuple[str, dict | None]:
    """AskUserQuestionのinputをSlack表示用テキストとメタデータに変換。
    返り値: (formatted_text, metadata) — パース失敗時は ("", None)"""
    questions = tool_input.get("questions", [])
    if not isinstance(questions, list) or not questions:
        return ("", None)

    all_parts = []
    all_questions_meta = []

    for q in questions:
        if not isinstance(q, dict):
            continue
        question_text = q.get("question", "")
        options = q.get("options", [])
        multi_select = q.get("multiSelect", False)

        if not question_text or not isinstance(options, list):
            continue

        lines = [f":question: *{question_text}*"]

        option_items = []
        for i, opt in enumerate(options, 1):
            if not isinstance(opt, dict):
                continue
            label = opt.get("label", "")
            desc = opt.get("description", "")
            if desc:
                lines.append(f"  {i}. {label} — {desc}")
            else:
                lines.append(f"  {i}. {label}")
            option_items.append({"label": label, "description": desc})

        if multi_select:
            lines.append("_複数選択が必要ですが、現在は未対応です。テキストで回答してください。_")
        else:
            lines.append("_番号を返信してください（テキストでOther回答も可）_")

        all_parts.append("\n".join(lines))
        all_questions_meta.append({
            "options": option_items,
            "multi_select": multi_select,
        })

    if not all_parts:
        return ("", None)

    formatted = "\n\n".join(all_parts)
    metadata = {"questions": all_questions_meta}
    return (formatted, metadata)


def _read_new_jsonl_entries(jsonl_path: str, file_offset: int) -> tuple[list[dict], int]:
    """JONLファイルからfile_offset以降の新しいエントリを読み取る。
    (entries, new_offset) を返す。"""
    entries = []
    try:
        current_size = os.path.getsize(jsonl_path)
    except OSError:
        return entries, file_offset
    if current_size <= file_offset:
        return entries, file_offset
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            f.seek(file_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            file_offset = f.tell()
    except OSError:
        pass
    return entries, file_offset


def _monitor_session_jsonl(inst: dict, thread_ts: str, channel: str, client: WebClient):
    """セッションJSONLファイルをポーリングし、新しいエントリをSlackに投稿。
    - thinking/tool_use → 1つのステータスメッセージをchat_updateで更新（:hourglass:付き）
    - text → 新しいメッセージとして投稿（応答テキスト）
    """
    pid = inst["pid"]
    jsonl_path = inst["jsonl_path"]
    task_ref = inst.get("task")  # bridge起動タスクの場合、Taskオブジェクト参照
    display_prefix = inst.get("display_prefix", f"PID {pid}")
    skip_exit = inst.get("skip_exit_message", False)
    fixed_jsonl = inst.get("fixed_jsonl", False)
    file_offset = 0

    # 既存の内容をスキップ（外部インスタンス用、bridge起動タスクは先頭から読む）
    if not inst.get("start_from_beginning"):
        try:
            file_offset = os.path.getsize(jsonl_path)
        except OSError:
            pass

    status_msg_ts: Optional[str] = None  # ステータスメッセージ（thinking/tool_use）
    status_lines: list[str] = []          # ステータス行の蓄積
    last_posted_text: Optional[str] = None  # 重複投稿防止用

    def _extract_task_info(entry: dict):
        """エントリからtask情報（session_id, result, tool_calls）を抽出"""
        if not task_ref:
            return
        entry_type = entry.get("type", "")
        msg = entry.get("message", {})
        # session_id
        if isinstance(msg, dict):
            sid = msg.get("session_id") or entry.get("session_id")
        else:
            sid = entry.get("session_id")
        if sid:
            task_ref.session_id = sid
        # result type
        if entry_type == "result":
            result_data = entry.get("result", {})
            result_text = ""
            if isinstance(result_data, dict):
                for content in result_data.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "text":
                        result_text += content["text"]
            elif isinstance(result_data, str):
                result_text = result_data
            if result_text:
                task_ref.result = result_text
        # tool_calls and text from assistant messages
        if entry_type == "assistant" and isinstance(msg, dict):
            for c in msg.get("content", []):
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    tool_name = c.get("name", "?")
                    tool_input = c.get("input", {})
                    task_ref.tool_calls.append({
                        "name": tool_name,
                        "input": _summarize_input(tool_input) if isinstance(tool_input, dict) else str(tool_input)[:80],
                    })
                elif c.get("type") == "text":
                    text = c.get("text", "").strip()
                    if text:
                        task_ref.result = text

    def _flush_status(final: bool = False):
        """ステータスメッセージを更新。final=Trueで⏳を除去。"""
        nonlocal status_msg_ts
        if not status_lines:
            return
        text = "\n".join(status_lines)
        if len(text) > MAX_SLACK_MSG_LENGTH:
            text = "...\n" + text[-MAX_SLACK_MSG_LENGTH:]
        if not final:
            text += "\n:hourglass_flowing_sand: _実行中..._"
        try:
            if status_msg_ts:
                client.chat_update(
                    channel=channel, ts=status_msg_ts, text=text,
                )
            else:
                resp = client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts, text=text,
                )
                status_msg_ts = resp["ts"]
        except Exception as e:
            logger.error("JSONL状態更新エラー PID %d: %s", pid, e)

    def _finalize_status():
        """ステータスメッセージを確定（⏳除去）。メッセージは保持して次回も同じメッセージに追記。"""
        if status_msg_ts and status_lines:
            _flush_status(final=True)

    def _post_text(text: str):
        """応答テキストを新しいメッセージとして投稿。同一テキストの重複投稿を防止。"""
        nonlocal last_posted_text
        if text == last_posted_text:
            return  # 同一テキストの重複投稿を防止
        last_posted_text = text
        # PTYペンディングメッセージを確定（JSONL経由で正式な応答が来たため）
        _finalize_pty_pending(inst, channel, client)
        display = text
        if len(display) > MAX_SLACK_MSG_LENGTH:
            display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
        try:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f":speech_balloon: {display_prefix}\n{display}",
            )
        except Exception as e:
            logger.error("JSONL応答投稿エラー %s: %s", display_prefix, e)

    def _post_question(text: str, metadata: dict | None):
        """AskUserQuestion の選択肢をスレッドに投稿し、pending_questionを設定。"""
        # PTYペンディングメッセージを確定（JSONL経由で正式な質問が来たため）
        _finalize_pty_pending(inst, channel, client)
        try:
            client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=text,
            )
        except Exception as e:
            logger.error("JSONL質問投稿エラー PID %d: %s", pid, e)
        # pending_question を設定（最初の質問のみ対応）
        if metadata and metadata.get("questions"):
            q = metadata["questions"][0]
            inst["pending_question"] = {
                "options": q["options"],
                "multi_select": q["multi_select"],
            }

    while _is_process_alive(pid):
        time.sleep(TERMINAL_POLL_INTERVAL)

        # 新しい入力があればステータスメッセージを入力メッセージに切り替え
        if "input_msg_ts" in inst:
            _finalize_status()
            status_msg_ts = inst.pop("input_msg_ts")
            input_text = inst.pop("input_msg_text", "")
            status_lines = [input_text] if input_text else []

        # JONLファイルが変わった可能性をチェック（外部インスタンス用）
        if not fixed_jsonl:
            cwd = inst.get("cwd", "")
            new_path = _find_session_jsonl(cwd)
            if new_path and new_path != jsonl_path:
                _finalize_status()
                jsonl_path = new_path
                inst["jsonl_path"] = jsonl_path
                file_offset = 0

        new_entries, file_offset = _read_new_jsonl_entries(jsonl_path, file_offset)
        if not new_entries:
            # JONLに新しいデータなし → CLIが入力待ちの可能性
            # Terminal内容を読んで許可プロンプトを検出（ttyがある場合のみ）
            tty = inst.get("tty")
            if tty and not inst.get("pending_question"):
                terminal_content = _read_terminal_contents(f"/dev/{tty}")
                if terminal_content:
                    clean = _strip_ansi(terminal_content)
                    # 末尾30行のみチェック（古い内容の誤検出を防止）
                    recent = "\n".join(clean.split("\n")[-30:])
                    prompt_info = _detect_permission_prompt(recent)
                    if prompt_info:
                        _post_permission_prompt(prompt_info, thread_ts, channel, client, inst)
            continue

        # エントリを分類して処理
        text_parts: list[str] = []
        has_status = False

        for entry in new_entries:
            _extract_task_info(entry)
            for category, text, metadata in _classify_jsonl_entry(entry):
                if category == "status":
                    # テキストが溜まっていたら先にフラッシュ
                    if text_parts:
                        _finalize_status()
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    status_lines.append(text)
                    has_status = True
                elif category == "text":
                    text_parts.append(text)
                elif category == "question":
                    # 先にステータスとテキストを確定
                    if text_parts:
                        _finalize_status()
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    _finalize_status()
                    has_status = False
                    # 選択肢をスレッドに投稿
                    _post_question(text, metadata)

        # テキスト応答があれば投稿
        if text_parts:
            _finalize_status()
            _post_text("\n".join(text_parts))

        # ステータス更新
        if has_status and not text_parts:
            _flush_status()

    # プロセス終了 → 残りのエントリを処理
    new_entries, file_offset = _read_new_jsonl_entries(jsonl_path, file_offset)
    if new_entries:
        text_parts = []
        for entry in new_entries:
            _extract_task_info(entry)
            for category, text, metadata in _classify_jsonl_entry(entry):
                if category == "status":
                    if text_parts:
                        _finalize_status()
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    status_lines.append(text)
                elif category == "text":
                    text_parts.append(text)
                elif category == "question":
                    if text_parts:
                        _finalize_status()
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    _finalize_status()
                    _post_question(text, metadata)
        if text_parts:
            _finalize_status()
            _post_text("\n".join(text_parts))

    # 残ったステータスを確定
    _finalize_status()

    # プロセス終了通知（外部インスタンス用）
    if not skip_exit:
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f":stop_button: PID {pid} が終了しました",
            )
        except Exception:
            pass


def _extract_session_info_from_jsonl(task, jsonl_path: str):
    """JONLファイルからsession_idとresultを抽出（フォールバック用）"""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_type = entry.get("type", "")
                msg = entry.get("message", {})
                # session_id
                if isinstance(msg, dict):
                    sid = msg.get("session_id") or entry.get("session_id")
                else:
                    sid = entry.get("session_id")
                if sid:
                    task.session_id = sid
                # result
                if entry_type == "result":
                    result_data = entry.get("result", {})
                    result_text = ""
                    if isinstance(result_data, dict):
                        for content in result_data.get("content", []):
                            if isinstance(content, dict) and content.get("type") == "text":
                                result_text += content["text"]
                    elif isinstance(result_data, str):
                        result_text = result_data
                    if result_text:
                        task.result = result_text
                # text from assistant (最後のtext応答をresultとして使用)
                if entry_type == "assistant" and isinstance(msg, dict):
                    for c in msg.get("content", []):
                        if isinstance(c, dict) and c.get("type") == "text":
                            text = c.get("text", "").strip()
                            if text:
                                task.result = text
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            task.tool_calls.append({
                                "name": c.get("name", "?"),
                                "input": _summarize_input(c.get("input", {})) if isinstance(c.get("input"), dict) else str(c.get("input", ""))[:80],
                            })
    except OSError:
        pass


def _strip_ansi(text: str) -> str:
    """ANSIエスケープシーケンスを除去"""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b[()][A-Z0-9]', '', text)
    return text


def _filter_terminal_ui(text: str) -> str:
    """ターミナルのUI要素（セパレーター、プロンプト、ステータスバー等）を除外"""
    text = _strip_ansi(text)

    lines = text.split('\n')
    filtered = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # セパレーター行（─━═-のみで構成）
        if re.match(r'^[─━═\-\s]+$', s):
            continue
        # プロンプト行（❯ で始まる）
        if s.startswith('❯'):
            continue
        # ステータスバー（⏵を含む）
        if '⏵' in s:
            continue
        # 思考スピナー（✶ · ✳ ✢ ✻ 等 + "…" を含む行）
        if re.match(r'^[^\w\s].*…', s):
            continue
        # Claude Code UIヒント（ctrl+キー操作案内）
        if 'to run in background' in s or re.search(r'ctrl\+\w', s):
            continue
        # タイマー表示 (5s), (28s) 等
        if re.match(r'^\(\d+s\)$', s):
            continue
        # 折りたたみ出力ヒント
        if re.match(r'^\d+\s+lines?\s*\(', s):
            continue
        # シェル環境変数出力（EMSDK等のプロファイル出力）
        if re.match(r'^(Setting up EMSDK|Setting environment variables|[A-Z_]+ =\s*/)', s):
            continue
        # PATHフラグメント（コロン区切りパスの断片）
        if re.match(r'^[/\w]*:[/\w]', s) and '/' in s and len(s) < 50:
            continue
        filtered.append(s)
    return '\n'.join(filtered)


def _set_pty_size(fd: int, cols: int = 120, rows: int = 40):
    """疑似ターミナルのウィンドウサイズを設定"""
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        pass


def _detect_permission_prompt(text: str) -> dict | None:
    """テキストから番号付き選択肢パターン（許可プロンプト等）を検出。
    検出時: {"description": str, "options": [{"label": str}, ...]} を返す。
    未検出時: None
    複数行にまたがる選択肢テキスト（行折り返し）にも対応。"""
    lines = text.strip().split("\n")

    # 番号付き選択肢を探す（例: "1. Yes", "  2. No"）
    option_lines = []
    option_start_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if option_lines:
                break  # 空行で選択肢終了
            continue

        m = re.match(r'^[>❯\s]*(\d+)\.\s+(.+)$', stripped)
        if m:
            num = int(m.group(1))
            label = m.group(2).strip()
            if num == len(option_lines) + 1:  # 連番チェック
                if not option_lines:
                    option_start_idx = i
                option_lines.append({"label": label})
            elif option_lines:
                break  # 連番が崩れたので選択肢終了
        elif option_lines:
            # 番号なし非空行 → 前の選択肢の折り返し継続行
            if re.match(r'^[❯>]', stripped):
                break  # プロンプト文字は選択肢終了
            option_lines[-1]["label"] += " " + stripped

    if len(option_lines) < 2:
        return None

    # 選択肢より前の行から説明文を抽出
    desc_lines = []
    if option_start_idx > 0:
        for i in range(max(0, option_start_idx - 5), option_start_idx):
            line = lines[i].strip()
            if line:
                desc_lines.append(line)

    description = "\n".join(desc_lines) if desc_lines else "入力が必要です"

    return {
        "description": description,
        "options": option_lines,
    }


def _post_permission_prompt(prompt_info: dict, thread_ts: str, channel_id: str,
                            client: WebClient, inst: dict):
    """許可プロンプトをSlackに投稿し、pending_questionを設定"""
    description = prompt_info["description"]
    options = prompt_info["options"]

    lines = [":question: *CLIからの質問*", f"{description}"]
    for i, opt in enumerate(options, 1):
        lines.append(f"  {i}. {opt['label']}")
    lines.append("_番号を返信してください（テキストでOther回答も可）_")

    text = "\n".join(lines)
    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
        )
    except Exception as e:
        logger.error("許可プロンプト投稿エラー: %s", e)

    inst["pending_question"] = {
        "options": options,
        "multi_select": False,
    }


def _monitor_pty_output(pid: int, master_fd: int, thread_ts: str, channel_id: str,
                        client: WebClient, inst: dict | None):
    """PTYのmaster_fd出力を監視し、idle検出でペンディング応答をSlackに投稿。
    出力が一定時間停止した場合、バッファ内容をSlackスレッドに投稿する。
    追加出力があればメッセージを上書き更新する。"""
    buf = b""
    MAX_BUF = 32 * 1024  # 32KBバッファ上限
    IDLE_THRESHOLD = 3   # 連続idle回数（× 1秒）でペンディング投稿
    idle_count = 0
    buf_changed = False   # 前回投稿後にバッファに新データがあるか

    while _is_process_alive(pid):
        try:
            rlist, _, _ = select.select([master_fd], [], [], 1.0)
        except OSError:
            break

        if rlist:
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                buf += data
                if len(buf) > MAX_BUF:
                    buf = buf[-MAX_BUF:]
                idle_count = 0
                buf_changed = True
            except OSError:
                break
        else:
            # select timeout — 出力なし
            if not buf or not buf_changed:
                continue
            idle_count += 1
            if idle_count >= IDLE_THRESHOLD:
                _update_pty_pending(buf, inst, thread_ts, channel_id, client, pid)
                buf_changed = False
                idle_count = 0

    # プロセス終了 → 未投稿データがあれば最終投稿、ペンディングメッセージを確定
    if buf_changed and inst:
        _update_pty_pending(buf, inst, thread_ts, channel_id, client, pid)
    if inst:
        _finalize_pty_pending(inst, channel_id, client)


def _update_pty_pending(buf: bytes, inst: dict | None, thread_ts: str,
                        channel_id: str, client: WebClient, pid: int):
    """PTY idle検出時: バッファ内容を解析してSlackにペンディング応答として投稿/更新。
    番号付き選択肢が検出された場合は質問形式で投稿し、pending_questionを設定する。
    パターン不一致の場合はフィルタ済みコンテンツをそのまま表示する。
    いずれも⏳インジケータ付きで、追加出力があれば上書き更新される。"""
    if not inst:
        return
    if inst.get("pending_question"):
        return  # 既に質問が投稿済み（JSONL経由等）

    text = buf.decode("utf-8", errors="replace")
    clean = _strip_ansi(text)
    recent = "\n".join(clean.split("\n")[-30:])

    # 番号付き選択肢パターンの検出
    prompt_info = _detect_permission_prompt(recent)
    if prompt_info:
        description = prompt_info["description"]
        options = prompt_info["options"]

        parts = [":question: *CLIからの質問*", description]
        for i, opt in enumerate(options, 1):
            parts.append(f"  {i}. {opt['label']}")
        parts.append("_番号を返信してください（テキストでOther回答も可）_")
        display_content = "\n".join(parts)

        # pending_question を設定（回答ルーティング用）
        inst["pending_question"] = {
            "options": options,
            "multi_select": False,
        }
    else:
        # パターン不一致 → フィルタ済みコンテンツをそのまま表示
        filtered = _filter_terminal_ui(text).strip()
        if not filtered:
            return
        display_content = filtered
        if len(display_content) > MAX_SLACK_MSG_LENGTH:
            display_content = "...\n" + display_content[-MAX_SLACK_MSG_LENGTH:]

    # 確定前テキストを保存（finalize用）
    inst["pty_pending_text"] = display_content

    # ペンディングインジケータ付きで表示
    display = f":hourglass: _CLIからの出力（確認中）_\n{display_content}"

    pending_ts = inst.get("pty_pending_msg_ts")
    try:
        if pending_ts:
            client.chat_update(
                channel=channel_id, ts=pending_ts, text=display,
            )
        else:
            resp = client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts, text=display,
            )
            inst["pty_pending_msg_ts"] = resp["ts"]
    except Exception as e:
        logger.error("PTY pending投稿エラー PID %d: %s", pid, e)


def _finalize_pty_pending(inst: dict, channel_id: str, client: WebClient):
    """PTYのペンディングメッセージを確定（⏳インジケータ除去）。
    JSONL経由で正式な応答が投稿された場合や、プロセス終了時に呼ばれる。"""
    pending_ts = inst.pop("pty_pending_msg_ts", None)
    pending_text = inst.pop("pty_pending_text", None)
    if not pending_ts or not pending_text:
        return
    try:
        client.chat_update(
            channel=channel_id, ts=pending_ts, text=pending_text,
        )
    except Exception:
        pass


def _update_thinking_message(
    client: WebClient, channel: str, msg_ts: str,
    text: str, pid: int,
):
    """思考中の進捗を元メッセージに上書き（chat_update）— ベストエフォート"""
    display = text
    if len(display) > MAX_SLACK_MSG_LENGTH:
        display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
    try:
        client.chat_update(
            channel=channel,
            ts=msg_ts,
            text=f"```\n{display}\n```\n:hourglass_flowing_sand: _思考中..._",
        )
    except Exception as e:
        logger.error("思考中更新エラー PID %d: %s", pid, e)


def _post_final_response(
    client: WebClient, channel: str, thread_ts: str,
    msg_ts: str, text: str, pid: int, timeout: bool = False,
):
    """応答完了時に新しいメッセージとして投稿（Slack通知が届く）+ 元メッセージの⏳を除去"""
    display = text
    if len(display) > MAX_SLACK_MSG_LENGTH:
        display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
    if timeout:
        header = f":warning: PID {pid} _(タイムアウト — 部分的な応答)_"
    else:
        header = f":speech_balloon: PID {pid}"
    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"{header}\n```\n{display}\n```",
        )
    except Exception as e:
        logger.error("応答投稿エラー PID %d: %s", pid, e)
    # 元の「送信しました」メッセージから⏳を除去
    try:
        client.chat_update(
            channel=channel,
            ts=msg_ts,
            text=f":arrow_right: PID {pid} に入力を送信しました :white_check_mark:",
        )
    except Exception:
        pass


def _monitor_terminal_output(inst: dict, thread_ts: str, channel: str, client: WebClient):
    """バックグラウンドでターミナル出力を監視し、入力送信後の応答をSlackメッセージに反映"""
    pid = inst["pid"]
    tty = inst["tty"]
    tty_device = f"/dev/{tty}"

    STABLE_THRESHOLD = 2   # 安定判定に必要な連続同一回数（× TERMINAL_POLL_INTERVAL秒）
    PROGRESS_INTERVAL = 2  # 思考中の進捗更新間隔（ポール回数）

    last_filtered = ""
    stable_count = 0
    no_output_count = 0
    poll_count = 0
    last_progress_text = ""
    baseline_content: Optional[str] = None  # ベースライン時点のターミナル内容

    # パッシブ監視用状態（ユーザー入力なしでのターミナル変化追跡）
    passive_last_filtered = ""
    passive_stable_count = 0
    passive_msg_ts: Optional[str] = None
    passive_poll_count = 0

    def _reset_state():
        nonlocal last_filtered, stable_count, no_output_count, poll_count
        nonlocal last_progress_text, baseline_content
        nonlocal passive_last_filtered, passive_stable_count, passive_msg_ts, passive_poll_count
        inst.pop("response_msg_ts", None)
        inst.pop("input_baseline_len", None)
        inst.pop("input_baseline_marker", None)
        last_filtered = ""
        stable_count = 0
        no_output_count = 0
        poll_count = 0
        last_progress_text = ""
        baseline_content = None
        # パッシブ監視のベースラインを現在の内容に更新
        cur = _read_terminal_contents(tty_device)
        if cur:
            inst["passive_baseline_len"] = len(cur)
            inst["passive_baseline_marker"] = cur[-500:] if len(cur) >= 500 else cur
        # パッシブ進捗メッセージを確定（⏳除去）
        if passive_msg_ts and passive_last_filtered:
            display = passive_last_filtered
            if len(display) > MAX_SLACK_MSG_LENGTH:
                display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
            try:
                client.chat_update(
                    channel=channel, ts=passive_msg_ts,
                    text=f":speech_balloon: PID {pid}\n```\n{display}\n```",
                )
            except Exception:
                pass
        passive_last_filtered = ""
        passive_stable_count = 0
        passive_msg_ts = None
        passive_poll_count = 0

    while _is_process_alive(pid):
        time.sleep(TERMINAL_POLL_INTERVAL)

        response_msg_ts = inst.get("response_msg_ts")
        baseline_len = inst.get("input_baseline_len")

        # アクティブ監視（ユーザー入力後の応答追跡）がなければパッシブ監視
        if response_msg_ts is None or baseline_len is None:
            # パッシブ監視: ターミナル変化を自動的にスレッドに投稿
            p_baseline_len = inst.get("passive_baseline_len")
            p_marker = inst.get("passive_baseline_marker", "")
            if p_baseline_len is None:
                continue

            current = _read_terminal_contents(tty_device)
            if current is None:
                continue

            # マーカーで差分位置を特定
            if p_marker:
                marker_pos = current.find(p_marker)
                if marker_pos >= 0:
                    new_start = marker_pos + len(p_marker)
                else:
                    new_start = p_baseline_len
            else:
                new_start = p_baseline_len

            # 行境界にアラインメント
            if 0 < new_start < len(current) and current[new_start - 1] != '\n':
                next_nl = current.find('\n', new_start)
                if next_nl >= 0:
                    new_start = next_nl + 1

            new_text = current[new_start:]
            if not new_text.strip():
                continue

            # 許可プロンプト検出（フィルタ前に実施。フィルタは❯行を除去するため）
            if not inst.get("pending_question"):
                clean_text = _strip_ansi(new_text)
                recent_lines = "\n".join(clean_text.split("\n")[-30:])
                prompt_info = _detect_permission_prompt(recent_lines)
                if prompt_info:
                    _post_permission_prompt(prompt_info, thread_ts, channel, client, inst)
                    continue  # プロンプト投稿後は通常のパッシブ投稿をスキップ

            filtered = _filter_terminal_ui(new_text).strip()
            if not filtered:
                continue

            passive_poll_count += 1

            # 安定判定（アクティブ監視と同じロジック）
            len_diff = abs(len(filtered) - len(passive_last_filtered))
            if filtered == passive_last_filtered or (len_diff <= 5 and filtered[:100] == passive_last_filtered[:100]):
                passive_stable_count += 1
            else:
                passive_stable_count = 0
                passive_last_filtered = filtered

            # 安定したら確定メッセージとしてスレッドに投稿し、ベースラインを更新
            if passive_stable_count >= STABLE_THRESHOLD:
                display = filtered
                if len(display) > MAX_SLACK_MSG_LENGTH:
                    display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
                try:
                    if passive_msg_ts:
                        client.chat_update(
                            channel=channel, ts=passive_msg_ts,
                            text=f":speech_balloon: PID {pid}\n```\n{display}\n```",
                        )
                    else:
                        client.chat_postMessage(
                            channel=channel, thread_ts=thread_ts,
                            text=f":speech_balloon: PID {pid}\n```\n{display}\n```",
                        )
                except Exception as e:
                    logger.error("パッシブ監視投稿エラー PID %d: %s", pid, e)
                # ベースライン更新
                inst["passive_baseline_len"] = len(current)
                inst["passive_baseline_marker"] = current[-500:] if len(current) >= 500 else current
                passive_last_filtered = ""
                passive_stable_count = 0
                passive_msg_ts = None
                passive_poll_count = 0
                continue

            # 途中進捗の更新（2ポールごと、または初回）
            if passive_msg_ts is None or passive_poll_count % PROGRESS_INTERVAL == 0:
                display = filtered
                if len(display) > MAX_SLACK_MSG_LENGTH:
                    display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
                try:
                    if passive_msg_ts:
                        client.chat_update(
                            channel=channel, ts=passive_msg_ts,
                            text=f"```\n{display}\n```\n:hourglass_flowing_sand: _実行中..._",
                        )
                    else:
                        resp = client.chat_postMessage(
                            channel=channel, thread_ts=thread_ts,
                            text=f"```\n{display}\n```\n:hourglass_flowing_sand: _実行中..._",
                        )
                        passive_msg_ts = resp["ts"]
                except Exception as e:
                    logger.error("パッシブ進捗更新エラー PID %d: %s", pid, e)
            continue

        poll_count += 1

        current = _read_terminal_contents(tty_device)
        if current is None:
            logger.debug("MON %d poll %d: read failed", pid, poll_count)
            continue

        # ベースライン内容を記録（初回のみ）
        if baseline_content is None:
            baseline_content = current[:baseline_len] if baseline_len <= len(current) else current

        # マーカーを使って差分の開始位置を特定（スクロールバックずれ対策）
        marker = inst.get("input_baseline_marker", "")
        if marker:
            marker_pos = current.find(marker)
            if marker_pos >= 0:
                new_start = marker_pos + len(marker)
            else:
                # マーカーが見つからない（スクロールバックで完全に消えた）→ 長さフォールバック
                new_start = max(0, len(current) - (baseline_len - len(current)) if len(current) < baseline_len else baseline_len)
        else:
            new_start = baseline_len

        # マーカー境界が行途中の場合、最初の不完全行をスキップ
        if new_start > 0 and new_start < len(current) and current[new_start - 1] != '\n':
            next_nl = current.find('\n', new_start)
            if next_nl >= 0:
                new_start = next_nl + 1
        new_text_raw = current[new_start:]
        cur_len = len(current)
        logger.debug("MON %d poll %d: cur_len=%d new_start=%d new_len=%d", pid, poll_count, cur_len, new_start, len(new_text_raw))

        # 新しいテキストがない場合
        if not new_text_raw.strip():
            no_output_count += 1
            if no_output_count >= STABLE_THRESHOLD:
                clean_current = _strip_ansi(current).rstrip()
                if re.search(r'❯\s*$', clean_current) or current != baseline_content:
                    _post_final_response(client, channel, thread_ts, response_msg_ts, "（コマンド実行完了）", pid)
                    _reset_state()
            continue

        new_text = new_text_raw
        filtered = _filter_terminal_ui(new_text).strip()

        # 新しいテキストの末尾で ❯ プロンプト判定（new_textの方がスクロールバック末尾より信頼性が高い）
        clean_new = _strip_ansi(new_text).rstrip()
        prompt_found = bool(re.search(r'❯\s*$', clean_new))

        # デバッグ: フィルタ前のクリーンテキストも表示
        clean_lines = _strip_ansi(new_text).strip().split('\n')
        clean_preview = clean_lines[:5] if clean_lines else []
        logger.debug("MON %d poll %d: filtered_len=%d prompt=%s raw_lines=%r", pid, poll_count, len(filtered), prompt_found, clean_preview)

        if not filtered:
            # フィルタ後にテキストがないが、❯プロンプトが末尾に出現 → 出力なしで完了
            if prompt_found:
                logger.debug("MON %d → コマンド実行完了（filtered empty + prompt）", pid)
                _post_final_response(client, channel, thread_ts, response_msg_ts, "（コマンド実行完了）", pid)
                _reset_state()
            continue

        no_output_count = 0

        # 応答完了判定1: ❯ プロンプトが出現（次の入力待ち状態）
        if prompt_found:
            logger.debug("MON %d → 応答投稿（prompt検出）", pid)
            _post_final_response(client, channel, thread_ts, response_msg_ts, filtered, pid)
            _reset_state()
            continue

        # 応答完了判定2: フィルタ済みテキストが安定（STABLE_THRESHOLD回連続でほぼ同一）
        # ターミナルのカーソル位置等で数文字揺れることがあるため、先頭100文字+長さ差±5で比較
        len_diff = abs(len(filtered) - len(last_filtered))
        if filtered == last_filtered or (len_diff <= 5 and filtered[:100] == last_filtered[:100]):
            stable_count += 1
        else:
            stable_count = 0
            last_filtered = filtered

        if stable_count >= STABLE_THRESHOLD:
            logger.debug("MON %d → 応答投稿（安定検出）", pid)
            _post_final_response(client, channel, thread_ts, response_msg_ts, filtered, pid)
            _reset_state()
            continue

        # 思考中の進捗更新: PROGRESS_INTERVALポールごとに途中経過を表示（ベストエフォート）
        if poll_count % PROGRESS_INTERVAL == 0 and filtered != last_progress_text:
            logger.debug("MON %d → 思考中更新", pid)
            _update_thinking_message(client, channel, response_msg_ts, filtered, pid)
            last_progress_text = filtered

    # プロセス終了 → 未完了の応答があれば最終投稿
    response_msg_ts = inst.get("response_msg_ts")
    baseline_len = inst.get("input_baseline_len")
    if response_msg_ts and baseline_len is not None:
        current = _read_terminal_contents(f"/dev/{tty}")
        if current and len(current) > baseline_len:
            filtered = _filter_terminal_ui(current[baseline_len:]).strip()
            if filtered:
                _post_final_response(client, channel, thread_ts, response_msg_ts, filtered, pid)

    # プロセス終了通知
    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f":stop_button: PID {pid} が終了しました",
        )
    except Exception:
        pass


# ── Claude Code ランナー ──────────────────────────────────
class ClaudeCodeRunner:
    """複数のClaude Codeタスクを並行管理"""

    def __init__(self, slack_client: WebClient):
        self.client = slack_client
        self.active_tasks: dict[int, Task] = {}
        self.task_history: list[Task] = []
        self.lock = threading.Lock()
        self._task_counter = 0

    @property
    def active_count(self) -> int:
        return len(self.active_tasks)

    def _next_id(self) -> int:
        self._task_counter += 1
        return self._task_counter

    def _assign_label(self, task: Task):
        used = {t.label_name for t in self.active_tasks.values()}
        for emoji, name in TASK_LABELS:
            if name not in used:
                task.label_emoji = emoji
                task.label_name = name
                return
        task.label_emoji = "⚪"
        task.label_name = f"task-{task.id}"

    def get_last_completed_task(self) -> Optional[Task]:
        """直近完了タスク（session_id付き）を返す"""
        for task in reversed(self.task_history):
            if task.session_id:
                return task
        return None

    def find_task_by_id(self, task_id: int) -> Optional[Task]:
        if task_id in self.active_tasks:
            return self.active_tasks[task_id]
        for t in reversed(self.task_history):
            if t.id == task_id:
                return t
        return None

    def find_task_by_session(self, session_id: str) -> Optional[Task]:
        """セッションIDからタスクを検索（部分一致対応）"""
        for t in reversed(self.task_history):
            if t.session_id and t.session_id.startswith(session_id):
                return t
        return None

    def build_command(self, task: Task, prompt_as_arg: bool = False) -> list[str]:
        cmd = [CLAUDE_CMD, "-p"]
        cmd.append("--verbose")

        tools = task.allowed_tools or DEFAULT_ALLOWED_TOOLS
        if tools:
            cmd.extend(["--allowedTools", tools])

        if task.continue_last:
            cmd.append("--continue")
        elif task.continue_session_id:
            cmd.extend(["--resume", task.continue_session_id])
        elif task.resume_session:
            cmd.extend(["--resume", task.resume_session])

        if prompt_as_arg:
            cmd.append(task.prompt)

        return cmd

    def run_task(self, task: Task) -> Optional[str]:
        """タスク実行を開始。エラー時はメッセージ文字列を返す"""
        with self.lock:
            task.id = self._next_id()
            if not task.label_emoji:
                self._assign_label(task)
            self.active_tasks[task.id] = task

        thread = threading.Thread(target=self._execute, args=(task,), daemon=True)
        thread.start()
        return None

    def _execute(self, task: Task):
        cwd = task.working_dir or WORKING_DIR
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()

        dir_display = os.path.basename(cwd) or cwd
        is_continuation = task.continue_session_id or task.continue_last or task.resume_session

        if is_continuation and task.thread_ts:
            header = (
                f"{task.display_label}  :arrow_forward: *セッション続行*\n"
                f"```{task.prompt[:500]}```"
            )
        else:
            header = (
                f"{task.display_label}  *タスク開始*\n"
                f":file_folder: `{dir_display}`\n"
                f"```{task.prompt[:500]}```"
            )
        self._post_status(task, header)

        # PTYモード判定: プロンプトが200KB以下ならCLI引数で渡しPTYを使用
        prompt_bytes = task.prompt.encode("utf-8")
        use_pty = len(prompt_bytes) <= 200 * 1024
        master_fd = None
        registered_thread_ts = None

        try:
            # サブプロセス起動前のタイムスタンプを記録（JSONL検出用）
            start_time = time.time()
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"} | {"LANG": "en_US.UTF-8"}

            if use_pty:
                # PTYモード: 疑似ターミナルでサブプロセスを起動
                cmd = self.build_command(task, prompt_as_arg=True)
                master_fd, slave_fd = pty.openpty()
                _set_pty_size(master_fd, 120, 40)
                env["TERM"] = "xterm-256color"

                proc = subprocess.Popen(
                    cmd,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
                os.close(slave_fd)
                task.process = proc
                task.master_fd = master_fd

                # instance_threadsに登録（スレッド返信ルーティング有効化）
                if task.thread_ts:
                    inst = {
                        "pid": proc.pid,
                        "cwd": cwd,
                        "master_fd": master_fd,
                        "task": task,
                        "display_prefix": task.display_label,
                        "skip_exit_message": True,
                    }
                    instance_threads[task.thread_ts] = inst
                    registered_thread_ts = task.thread_ts

                # PTY出力監視スレッドを起動
                pty_thread = threading.Thread(
                    target=_monitor_pty_output,
                    args=(proc.pid, master_fd, task.thread_ts, task.channel_id,
                          self.client, instance_threads.get(task.thread_ts)),
                    daemon=True,
                )
                pty_thread.start()
            else:
                # フォールバック: 従来のstdin方式（長大プロンプト用）
                cmd = self.build_command(task)
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    env=env,
                )
                task.process = proc
                proc.stdin.write(task.prompt)
                proc.stdin.close()

            # JONLファイルが現れるまでポーリング
            # min_ctime で作成時刻をフィルタし、既存セッションのJONLを誤検出しない
            jsonl_path = None
            for _ in range(30):  # 最大30秒待機
                if proc.poll() is not None:
                    break
                found = _find_session_jsonl(cwd, min_ctime=start_time)
                if found:
                    jsonl_path = found
                    break
                time.sleep(1)

            # JSONL監視（プロセスがまだ実行中の場合）
            jsonl_monitored = False
            if jsonl_path and proc.poll() is None:
                if registered_thread_ts and registered_thread_ts in instance_threads:
                    # PTYモード: 既に登録済みのinstにJSONLフィールドを追加
                    inst = instance_threads[registered_thread_ts]
                    inst["jsonl_path"] = jsonl_path
                    inst["fixed_jsonl"] = True
                    inst["start_from_beginning"] = True
                    inst["task"] = task
                else:
                    inst = {
                        "pid": proc.pid,
                        "cwd": cwd,
                        "jsonl_path": jsonl_path,
                        "display_prefix": task.display_label,
                        "skip_exit_message": True,
                        "fixed_jsonl": True,
                        "start_from_beginning": True,
                        "task": task,
                    }
                _monitor_session_jsonl(inst, task.thread_ts, task.channel_id, self.client)
                jsonl_monitored = True

            proc.wait()

            if task.status == TaskStatus.CANCELLED:
                self._post_status(task, f"{task.display_label}  :stop_sign: キャンセルされました")
                return

            # JSONL からsession_idを取得できなかった場合のフォールバック
            if not task.session_id:
                fallback_path = jsonl_path or _find_session_jsonl(cwd, min_ctime=start_time)
                if fallback_path:
                    _extract_session_info_from_jsonl(task, fallback_path)

            if proc.returncode == 0:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now()
                elapsed = (task.completed_at - task.started_at).total_seconds()
                summary = self._format_result(task, elapsed, show_result=not jsonl_monitored)
                self._post_status(task, summary)
            else:
                stderr_raw = proc.stderr.read() if proc.stderr else ""
                stderr_output = stderr_raw.decode("utf-8", errors="replace") if isinstance(stderr_raw, bytes) else stderr_raw
                task.status = TaskStatus.FAILED
                task.error = stderr_output
                task.completed_at = datetime.now()
                self._post_status(
                    task,
                    f"{task.display_label}  :x: 失敗 (exit {proc.returncode})\n```{stderr_output[:1000]}```",
                )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            self._post_status(task, f"{task.display_label}  :x: エラー: {e}")

        finally:
            # master_fdクローズ
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
                task.master_fd = None
            # instance_threads登録解除
            if registered_thread_ts:
                instance_threads.pop(registered_thread_ts, None)
            with self.lock:
                self.active_tasks.pop(task.id, None)
                self.task_history.append(task)

    def _format_result(self, task: Task, elapsed: float, show_result: bool = True) -> str:
        dir_name = os.path.basename(task.working_dir or WORKING_DIR)
        user_info = f"  <@{task.user_id}>" if task.user_id else ""
        parts = [f"{task.display_label}  :white_check_mark: *タスク完了* ({elapsed:.0f}秒)  :file_folder: `{dir_name}`{user_info}"]
        if task.tool_calls:
            tools_summary = ", ".join(f"`{t['name']}`" for t in task.tool_calls[:10])
            if len(task.tool_calls) > 10:
                tools_summary += f" 他{len(task.tool_calls) - 10}件"
            parts.append(f"使用ツール ({len(task.tool_calls)}回): {tools_summary}")
        if show_result and task.result:
            result_text = task.result[:MAX_SLACK_MSG_LENGTH]
            if len(task.result) > MAX_SLACK_MSG_LENGTH:
                result_text += "\n...(省略)"
            parts.append(f"\n{result_text}")
        if task.session_id:
            parts.append(f"\n_Session: `{task.session_id[:12]}...`_")
            parts.append(f"_`continue {task.short_id} <指示>` で続行（自動的に `{dir_name}/` で実行）_")
        return "\n".join(parts)

    def _post_status(self, task: Task, text: str):
        channel = task.channel_id
        try:
            if task.thread_ts:
                self.client.chat_postMessage(
                    channel=channel, thread_ts=task.thread_ts, text=text
                )
            else:
                resp = self.client.chat_postMessage(channel=channel, text=text)
                task.thread_ts = resp["ts"]
        except Exception as e:
            logger.error("Slack投稿エラー: %s", e)

    def cancel_task(self, task_id: int) -> bool:
        task = self.active_tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        task.status = TaskStatus.CANCELLED
        if task.process:
            try:
                task.process.terminate()
                task.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                task.process.kill()
        return True

    def cancel_all(self) -> int:
        cancelled = 0
        for task_id in list(self.active_tasks.keys()):
            if self.cancel_task(task_id):
                cancelled += 1
        return cancelled


def _summarize_input(input_data: dict) -> str:
    if not input_data:
        return ""
    if "file_path" in input_data:
        return input_data["file_path"]
    if "command" in input_data:
        cmd = input_data["command"]
        return cmd[:80] + ("..." if len(cmd) > 80 else "")
    if "pattern" in input_data:
        return input_data["pattern"]
    if "query" in input_data:
        q = input_data["query"]
        return q[:80] + ("..." if len(q) > 80 else "")
    if "url" in input_data:
        return input_data["url"][:80]
    if "notebook_path" in input_data:
        return input_data["notebook_path"]
    return str(input_data)[:80]


# ── Slack App ─────────────────────────────────────────────
app = App(token=SLACK_BOT_TOKEN)
slack_client = WebClient(token=SLACK_BOT_TOKEN)
runner = ClaudeCodeRunner(slack_client)
settings = UserSettings()

# スレッドts → インスタンス情報のマッピング（起動時に検出したclaude CLIプロセス用）
instance_threads: dict[str, dict] = {}


def _save_instance_state():
    """instance_threads の状態をファイルに永続化（bridge起動タスクはttyなしのため除外）"""
    state = {}
    for thread_ts, data in instance_threads.items():
        if not data.get("tty"):
            continue  # bridge起動タスク（ptyのみ、ttyなし）は永続化対象外
        state[thread_ts] = {
            "pid": data["pid"],
            "tty": data.get("tty", ""),
            "cwd": data.get("cwd", ""),
        }
    try:
        with open(INSTANCE_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("インスタンス状態の保存に失敗: %s", e)


def _load_instance_state() -> dict[int, str]:
    """保存された状態からPID → thread_tsマッピングを読み込み。
    生存していないPIDは除外する。"""
    try:
        with open(INSTANCE_STATE_FILE, "r") as f:
            state = json.load(f)
        pid_to_ts: dict[int, str] = {}
        for thread_ts, data in state.items():
            pid = data.get("pid")
            if pid and _is_process_alive(pid):
                pid_to_ts[pid] = thread_ts
        return pid_to_ts
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        logger.warning("インスタンス状態の読み込みに失敗: %s", e)
        return {}

BOT_USER_ID = None


def _register_instance(inst: dict, reuse_thread_ts: str | None = None) -> str | None:
    """claude CLIインスタンスをSlackスレッドに登録し、監視を開始する。
    reuse_thread_ts が指定された場合、新しいスレッドを作成せず既存スレッドを再利用する。
    成功時はthread_tsを返す。NOTIFICATION_CHANNEL未設定時はNone。"""
    if not NOTIFICATION_CHANNEL:
        return None
    # 既に同じPIDが登録済みならスキップ
    for data in instance_threads.values():
        if data["pid"] == inst["pid"]:
            return None

    # JSONL検出を試みる
    jsonl_path = _find_session_jsonl(inst["cwd"])
    monitor_label = "JSONL" if jsonl_path else "Terminal"

    if reuse_thread_ts:
        # 前回のスレッドを再利用（再起動時）
        thread_ts = reuse_thread_ts
        try:
            slack_client.chat_postMessage(
                channel=NOTIFICATION_CHANNEL,
                thread_ts=thread_ts,
                text=f":recycle: Bridge再起動 — PID {inst['pid']} の監視を再開します（{monitor_label}）",
            )
        except Exception as e:
            logger.warning("復元通知の送信に失敗: %s", e)
    else:
        resp = slack_client.chat_postMessage(
            channel=NOTIFICATION_CHANNEL,
            text=(
                f":computer: *実行中のClaude Code* (PID {inst['pid']})\n"
                f":file_folder: `{inst['cwd']}`\n"
                f":clock1: 経過: {inst['etime']}\n"
                f":mag: 監視: {monitor_label}\n"
                "_このスレッドに返信するとclaude CLIに入力を送信します_"
            ),
        )
        thread_ts = resp["ts"]

    thread_data = {
        "pid": inst["pid"],
        "tty": inst["tty"],
        "cwd": inst["cwd"],
    }
    instance_threads[thread_ts] = thread_data

    if jsonl_path:
        # JSONL監視モード
        thread_data["jsonl_path"] = jsonl_path
        monitor_target = _monitor_session_jsonl
    else:
        # 既存のターミナル監視にフォールバック
        tty_device = f"/dev/{inst['tty']}"
        initial_content = _read_terminal_contents(tty_device) or ""
        thread_data["passive_baseline_len"] = len(initial_content)
        thread_data["passive_baseline_marker"] = initial_content[-500:] if len(initial_content) >= 500 else initial_content
        monitor_target = _monitor_terminal_output

    monitor = threading.Thread(
        target=monitor_target,
        args=(thread_data, thread_ts, NOTIFICATION_CHANNEL, slack_client),
        daemon=True,
    )
    monitor.start()
    _save_instance_state()
    return thread_ts


def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID is None:
        resp = slack_client.auth_test()
        BOT_USER_ID = resp["user_id"]
    return BOT_USER_ID


def parse_task_id(s: str) -> Optional[int]:
    m = re.match(r"#?(\d+)", s.strip())
    return int(m.group(1)) if m else None


def _strip_bot_mention(text: str) -> str:
    """テキストから <@BOT_ID> を除去。除去しなかった場合は元のテキストをそのまま返す"""
    bot_id = get_bot_user_id()
    return re.sub(rf'<@{bot_id}>\s*', '', text).strip()


@app.event("message")
def handle_message(event, say):
    channel_type = event.get("channel_type", "")
    user_id = event.get("user", "")

    # botメッセージは常に無視
    if event.get("bot_id") or user_id == get_bot_user_id():
        return

    if channel_type == "im":
        # DMモードは廃止（チャンネルモードのみ対応）
        return

    elif channel_type in ("channel", "group"):
        # ── チャンネルモード ──
        channel_id = event.get("channel", "")

        # チャンネル許可チェック
        if not _is_channel_allowed(channel_id):
            return

        # ユーザー許可チェック
        if not _is_user_allowed(user_id):
            return

        text = event.get("text", "").strip()
        if not text:
            return

        # スレッド返信 → 追跡中スレッドならCLIに転送（メンション不要）
        parent_ts = event.get("thread_ts")
        if parent_ts and parent_ts in instance_threads:
            input_text = _strip_bot_mention(text)
            # "!" プレフィックス: "!clear" → "/clear"
            input_text = "/" + input_text[1:] if input_text.startswith("!") else input_text
            _handle_instance_input(input_text, say, parent_ts, channel_id)
            return

        # トップレベルメッセージ: botメンション必須
        stripped = _strip_bot_mention(text)
        if stripped == text:
            return  # メンションなし → 無視
        text = stripped
        if not text:
            return

        _dispatch_command(text, event, say)


def _dispatch_command(text: str, event: dict, say):
    """コマンド解析・実行"""
    channel_id = event.get("channel", "")
    thread_ts = event.get("ts")
    user_id = event.get("user", "")

    cmd_lower = text.lower()

    # ── help ──
    if cmd_lower in ("help", "?"):
        say(text=_help_text(), thread_ts=thread_ts)
        return

    # ── status ──
    if cmd_lower == "status":
        _handle_status(say, thread_ts)
        return

    # ── cancel ──
    if cmd_lower.startswith("cancel"):
        _handle_cancel(text, say, thread_ts)
        return

    # ── bind <path> ──
    if cmd_lower.startswith("bind "):
        _handle_bind(text, say, thread_ts, channel_id)
        return

    # ── unbind ──
    if cmd_lower == "unbind":
        _handle_unbind(say, thread_ts, channel_id)
        return

    # ── sessions ──
    if cmd_lower == "sessions":
        _handle_sessions(say, thread_ts)
        return

    # ── detect ──
    if cmd_lower == "detect":
        _handle_detect(say, thread_ts)
        return

    # ── tools <list> ──
    if cmd_lower.startswith("tools "):
        tools = text[6:].strip()
        settings.next_tools = tools
        say(
            text=f":wrench: 次のタスクの許可ツール: `{tools}`\n続けてタスクを送信してください",
            thread_ts=thread_ts,
        )
        return

    # ── continue [#id] <指示> ──
    if cmd_lower.startswith("continue"):
        _handle_continue(text, say, thread_ts, channel_id, user_id)
        return

    # ── resume <session_id> <指示> ──
    if cmd_lower.startswith("resume "):
        _handle_resume(text, say, thread_ts, channel_id, user_id)
        return

    # ── in <path> <タスク> ──
    if cmd_lower.startswith("in "):
        _handle_in_dir(text, say, thread_ts, channel_id, user_id)
        return

    # ── 新規タスク ──
    task = Task(
        id=0,
        prompt=text,
        channel_id=channel_id,
        working_dir=_get_working_dir_for_channel(channel_id),
        allowed_tools=settings.consume_tools(),
        user_id=user_id,
    )
    err = runner.run_task(task)
    if err:
        say(text=err, thread_ts=thread_ts)


def _build_terminal_focus_script(tty_device: str) -> str:
    """ターミナルタブにフォーカスするAppleScript部分。
    変数 `found` をセットし、後続スクリプトで参照可能。"""
    return f'''
tell application "Terminal"
    activate
    delay 0.3
    set found to false
    repeat with w in windows
        repeat with t in tabs of w
            if tty of t is "{tty_device}" then
                set frontmost of w to true
                delay 0.1
                set selected of t to true
                set found to true
                exit repeat
            end if
        end repeat
        if found then exit repeat
    end repeat
end tell
'''


def _build_paste_script(tty_device: str) -> str:
    """既存のペースト→Esc→Enter操作のAppleScript"""
    focus = _build_terminal_focus_script(tty_device)
    return focus + '''
if found then
    delay 0.3
    tell application "System Events"
        tell process "Terminal"
            keystroke "v" using command down
            delay 0.3
            key code 53
            delay 0.1
            keystroke return
        end tell
    end tell
end if
'''


def _build_select_option_script(tty_device: str, option_index: int) -> str:
    """選択肢を矢印キーで選択するAppleScript。
    option_index: 0始まりのインデックス（0=1番目=初期選択なのでそのままEnter）"""
    focus = _build_terminal_focus_script(tty_device)
    if option_index == 0:
        # 1番目が初期選択なのでEnterのみ
        return focus + '''
if found then
    delay 0.3
    tell application "System Events"
        tell process "Terminal"
            keystroke return
        end tell
    end tell
end if
'''
    else:
        return focus + f'''
if found then
    delay 0.3
    tell application "System Events"
        tell process "Terminal"
            repeat {option_index} times
                key code 125
                delay 0.05
            end repeat
            delay 0.1
            keystroke return
        end tell
    end tell
end if
'''


def _build_select_other_script(tty_device: str, option_count: int) -> str:
    """Other選択→テキストペースト→EnterのAppleScript。
    option_count回の下矢印でOtherを選択し、Enter→テキストペースト→Enter。"""
    focus = _build_terminal_focus_script(tty_device)
    return focus + f'''
if found then
    delay 0.3
    tell application "System Events"
        tell process "Terminal"
            repeat {option_count} times
                key code 125
                delay 0.05
            end repeat
            delay 0.1
            keystroke return
            delay 0.5
            keystroke "v" using command down
            delay 0.3
            keystroke return
        end tell
    end tell
end if
'''


def _handle_instance_input(text: str, say, parent_ts: str, channel_id: str):
    """インスタンススレッドへの返信をPTY書き込みまたはクリップボード+ペースト経由でターミナルに転送。
    pending_questionがある場合は選択肢回答として処理する。"""
    inst = instance_threads[parent_ts]
    tty = inst.get("tty", "")
    pid = inst["pid"]
    master_fd = inst.get("master_fd")
    is_jsonl_mode = "jsonl_path" in inst
    pending_q = inst.get("pending_question")

    try:
        # ── PTYモード: master_fdに直接書き込み ──
        if master_fd is not None:
            action_label = None

            if pending_q and not pending_q.get("multi_select", False):
                options = pending_q.get("options", [])
                option_count = len(options)

                num_match = re.match(r"^\s*(\d+)\s*$", text)
                if num_match:
                    num = int(num_match.group(1))
                    if 1 <= num <= option_count:
                        # Down矢印で選択肢にナビゲート + Enter
                        pty_input = b"\x1b[B" * (num - 1) + b"\r"
                        os.write(master_fd, pty_input)
                        action_label = f"選択肢 {num} ({options[num - 1].get('label', '')}) を選択"
                    else:
                        say(text=f":warning: 1〜{option_count} の番号を入力してください", thread_ts=parent_ts)
                        return
                else:
                    # テキスト入力 → "Other" を選択してテキスト入力
                    pty_input = b"\x1b[B" * option_count + b"\r"
                    os.write(master_fd, pty_input)
                    time.sleep(0.5)
                    os.write(master_fd, text.encode("utf-8") + b"\r")
                    action_label = f"Other: {text[:50]}"

                inst.pop("pending_question", None)
                _finalize_pty_pending(inst, channel_id, slack_client)
            else:
                # 通常のテキスト入力
                if pending_q and pending_q.get("multi_select", False):
                    inst.pop("pending_question", None)
                    _finalize_pty_pending(inst, channel_id, slack_client)
                os.write(master_fd, text.encode("utf-8") + b"\r")

            # Slack通知
            if action_label:
                msg_text = f":arrow_right: PID {pid} に回答を送信: {action_label} :white_check_mark:"
            else:
                msg_text = f":arrow_right: PID {pid} に入力を送信しました :white_check_mark:"
            resp = slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=parent_ts,
                text=msg_text,
            )
            if is_jsonl_mode:
                inst["input_msg_ts"] = resp["ts"]
                inst["input_msg_text"] = msg_text
            return

        # ── TTYがない場合のエラー ──
        if not tty:
            say(text=f":warning: PID {pid} にはTTYが接続されていません", thread_ts=parent_ts)
            return

        # ── 既存のAppleScript処理（外部検出インスタンス用） ──
        tty_device = f"/dev/{tty}"

        if not is_jsonl_mode:
            # ターミナル監視モード: 入力前のターミナル末尾を記録
            current_contents = _read_terminal_contents(tty_device) or ""
            baseline_len = len(current_contents)
            baseline_marker = current_contents[-500:] if len(current_contents) >= 500 else current_contents

        if pending_q and not pending_q.get("multi_select", False):
            # 選択肢回答モード
            options = pending_q.get("options", [])
            option_count = len(options)

            # 番号入力チェック
            num_match = re.match(r"^\s*(\d+)\s*$", text)
            if num_match:
                num = int(num_match.group(1))
                if 1 <= num <= option_count:
                    # 番号で選択肢を選ぶ（0始まりインデックス）
                    script = _build_select_option_script(tty_device, num - 1)
                    action_label = f"選択肢 {num} ({options[num - 1].get('label', '')}) を選択"
                else:
                    say(text=f":warning: 1〜{option_count} の番号を入力してください", thread_ts=parent_ts)
                    return
            else:
                # テキスト入力 → "Other" を選択してペースト
                subprocess.run(
                    ["pbcopy"],
                    input=text.encode("utf-8"),
                    timeout=5,
                )
                script = _build_select_other_script(tty_device, option_count)
                action_label = f"Other: {text[:50]}"

            # pending_question をクリア
            inst.pop("pending_question", None)
            _finalize_pty_pending(inst, channel_id, slack_client)
        else:
            # 通常のテキスト入力（既存動作）
            # multiSelectの場合もフォールバック
            if pending_q and pending_q.get("multi_select", False):
                inst.pop("pending_question", None)
                _finalize_pty_pending(inst, channel_id, slack_client)

            subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                timeout=5,
            )
            script = _build_paste_script(tty_device)
            action_label = None

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            if action_label:
                msg_text = f":arrow_right: PID {pid} に回答を送信: {action_label} :white_check_mark:"
            else:
                msg_text_done = f":arrow_right: PID {pid} に入力を送信しました :white_check_mark:"
                msg_text_wait = f":arrow_right: PID {pid} に入力を送信しました :hourglass_flowing_sand:"

            if is_jsonl_mode:
                # JSONL監視モード: メッセージtsを保存してモニタがステータスを追記する
                input_text = msg_text if action_label else msg_text_done
                resp = slack_client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=parent_ts,
                    text=input_text,
                )
                inst["input_msg_ts"] = resp["ts"]
                inst["input_msg_text"] = input_text
            else:
                # ターミナル監視モード: tsを保存してモニタースレッドが応答で更新する
                resp = slack_client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=parent_ts,
                    text=msg_text if action_label else msg_text_wait,
                )
                if not action_label:
                    inst["response_msg_ts"] = resp["ts"]
                    inst["input_baseline_len"] = baseline_len
                    inst["input_baseline_marker"] = baseline_marker
        else:
            say(text=f":x: 入力送信エラー: {result.stderr.strip()}", thread_ts=parent_ts)
    except Exception as e:
        say(text=f":x: 入力送信エラー: {e}", thread_ts=parent_ts)


# Mentionイベントは無視（message.channels で処理済み）
@app.event("app_mention")
def handle_mention(event, say):
    pass


def _help_text() -> str:
    return (
        ":robot_face: *Claude Code Bridge* — 使い方:\n"
        "• `@bot <タスク>` → 新しいタスクを実行\n"
        "• `@bot in <path> <タスク>` → 指定ディレクトリで実行（相対パスはプロジェクトルート基準）\n"
        "• `@bot continue <指示>` → 直前セッションを続行\n"
        "• `@bot continue #2 <指示>` → 指定タスクのセッションを続行\n"
        "• `@bot resume <session_id> <指示>` → 指定セッションを再開\n"
        "• `@bot status` → 全タスクの状態一覧\n"
        "• `@bot cancel #2` → タスクをキャンセル\n"
        "• `@bot cancel all` → 全タスクをキャンセル\n"
        "• `@bot bind <path>` → チャンネルにプロジェクトルートを紐付け\n"
        "• `@bot unbind` → プロジェクトルートの紐付けを解除\n"
        "• `@bot tools <tool1,...>` → 次回の許可ツール設定\n"
        "• `@bot sessions` → セッション履歴\n"
        "• `@bot detect` → 実行中のclaude CLIインスタンスを検出・接続"
    )


def _handle_status(say, thread_ts):
    """アクティブタスク一覧"""
    active = runner.active_tasks
    if not active:
        recent = runner.task_history[-5:] if runner.task_history else []
        if recent:
            lines = [":zzz: 実行中のタスクはありません\n*直近の完了タスク:*"]
            for t in reversed(recent):
                emoji = ":white_check_mark:" if t.status == TaskStatus.COMPLETED else ":x:"
                elapsed = ""
                if t.started_at and t.completed_at:
                    elapsed = f" ({(t.completed_at - t.started_at).total_seconds():.0f}秒)"
                lines.append(f"{emoji} {t.short_id} {t.prompt[:40]}{elapsed}")
            say(text="\n".join(lines), thread_ts=thread_ts)
        else:
            say(text=":zzz: 実行中のタスクはありません", thread_ts=thread_ts)
        return

    lines = [f":gear: *実行中のタスク ({len(active)}件)*"]
    for task in active.values():
        elapsed = (datetime.now() - task.started_at).total_seconds() if task.started_at else 0
        dir_name = os.path.basename(task.working_dir or WORKING_DIR)
        prompt_preview = task.prompt[:50] + ("..." if len(task.prompt) > 50 else "")
        tool_count = len(task.tool_calls)

        user_info = f"  <@{task.user_id}>" if task.user_id else ""
        lines.append(
            f"\n{task.display_label}"
            f"  :file_folder: `{dir_name}` ({elapsed:.0f}秒, ツール{tool_count}回){user_info}\n"
            f"> {prompt_preview}"
        )
        if task.tool_calls:
            recent_tools = " -> ".join(f"`{t['name']}`" for t in task.tool_calls[-3:])
            lines.append(f"  最近: {recent_tools}")

    say(text="\n".join(lines), thread_ts=thread_ts)


def _handle_cancel(text: str, say, thread_ts):
    arg = text[6:].strip().lower()

    if arg == "all":
        count = runner.cancel_all()
        say(text=f":stop_sign: {count}件のタスクをキャンセルしました", thread_ts=thread_ts)
        return

    task_id = parse_task_id(arg)
    if task_id is None:
        # アクティブタスクが1つだけならそれをキャンセル
        if len(runner.active_tasks) == 1:
            task_id = next(iter(runner.active_tasks))
        else:
            say(
                text=":warning: キャンセルするタスクを指定してください: `cancel #2` or `cancel all`",
                thread_ts=thread_ts,
            )
            return

    if runner.cancel_task(task_id):
        say(text=f":stop_sign: タスク #{task_id} のキャンセルリクエストを送信しました", thread_ts=thread_ts)
    else:
        say(text=f"タスク #{task_id} は実行中ではありません", thread_ts=thread_ts)


def _handle_bind(text: str, say, thread_ts, channel_id: str):
    """チャンネルにプロジェクトルートを紐付け"""
    dir_path = text[5:].strip()
    expanded = os.path.abspath(os.path.expanduser(dir_path))
    if not os.path.isdir(expanded):
        say(text=f":warning: ディレクトリが見つかりません: `{dir_path}`", thread_ts=thread_ts)
        return
    _channel_projects[channel_id] = expanded
    _save_channel_projects()
    say(
        text=f":link: このチャンネルのプロジェクトルートを設定しました: `{expanded}`",
        thread_ts=thread_ts,
    )


def _handle_unbind(say, thread_ts, channel_id: str):
    """チャンネルのプロジェクトルート紐付けを解除"""
    if channel_id in _channel_projects:
        removed = _channel_projects.pop(channel_id)
        _save_channel_projects()
        say(
            text=f":broken_chain: プロジェクトルートの紐付けを解除しました（旧: `{removed}`）\nデフォルト: `{WORKING_DIR}`",
            thread_ts=thread_ts,
        )
    else:
        say(text=":warning: このチャンネルにはプロジェクトルートが設定されていません", thread_ts=thread_ts)


def _handle_continue(text: str, say, thread_ts, channel_id: str, user_id: str = ""):
    rest = text[8:].strip()
    if not rest:
        say(
            text=":warning: 続行する指示を入力してください\n例: `continue テスト追加して` or `continue #2 修正して`",
            thread_ts=thread_ts,
        )
        return

    original_task: Optional[Task] = None
    target_session = None
    prompt = rest

    m = re.match(r"#(\d+)\s+(.*)", rest, re.DOTALL)
    if m:
        task_id = int(m.group(1))
        prompt = m.group(2).strip()
        original_task = runner.find_task_by_id(task_id)
        if original_task and original_task.session_id:
            target_session = original_task.session_id
        else:
            say(text=f":warning: タスク #{task_id} のセッションが見つかりません", thread_ts=thread_ts)
            return
    else:
        original_task = runner.get_last_completed_task()
        if original_task:
            target_session = original_task.session_id

    if not prompt:
        say(text=":warning: 続行する指示を入力してください", thread_ts=thread_ts)
        return

    # 元タスクの作業ディレクトリ・スレッド・ラベルを引き継ぐ
    inherited_dir = original_task.working_dir if original_task else None
    inherited_thread = original_task.thread_ts if original_task else None
    inherited_emoji = original_task.label_emoji if original_task else ""
    inherited_label = original_task.label_name if original_task else ""

    task = Task(
        id=0,
        prompt=prompt,
        channel_id=original_task.channel_id if original_task and original_task.channel_id else channel_id,
        working_dir=inherited_dir or _get_working_dir_for_channel(channel_id),
        allowed_tools=settings.consume_tools(),
        thread_ts=inherited_thread,
        label_emoji=inherited_emoji,
        label_name=inherited_label,
        user_id=user_id,
    )
    if target_session:
        task.continue_session_id = target_session
    else:
        task.continue_last = True

    err = runner.run_task(task)
    if err:
        say(text=err, thread_ts=thread_ts)


def _handle_resume(text: str, say, thread_ts, channel_id: str, user_id: str = ""):
    parts = text[7:].strip().split(maxsplit=1)
    if len(parts) < 2:
        say(text=":warning: 使い方: `resume <session_id> <指示>`", thread_ts=thread_ts)
        return
    session_id, prompt = parts

    # 元タスクの作業ディレクトリ・スレッド・ラベルを引き継ぐ
    original = runner.find_task_by_session(session_id)
    inherited_dir = original.working_dir if original else None
    inherited_thread = original.thread_ts if original else None
    inherited_emoji = original.label_emoji if original else ""
    inherited_label = original.label_name if original else ""

    task = Task(
        id=0,
        prompt=prompt,
        channel_id=original.channel_id if original and original.channel_id else channel_id,
        resume_session=session_id,
        working_dir=inherited_dir or _get_working_dir_for_channel(channel_id),
        allowed_tools=settings.consume_tools(),
        thread_ts=inherited_thread,
        label_emoji=inherited_emoji,
        label_name=inherited_label,
        user_id=user_id,
    )
    err = runner.run_task(task)
    if err:
        say(text=err, thread_ts=thread_ts)


def _handle_in_dir(text: str, say, thread_ts, channel_id: str, user_id: str = ""):
    rest = text[3:].strip()
    parts = rest.split(maxsplit=1)
    if len(parts) < 2:
        say(text=":warning: 使い方: `in <path> タスク内容`\n相対パスはプロジェクトルート基準で解決されます", thread_ts=thread_ts)
        return

    dir_path, prompt = parts
    dir_path = os.path.expanduser(dir_path)

    # 相対パスをプロジェクトルート基準で解決
    if not os.path.isabs(dir_path):
        root = _get_channel_project_root(channel_id) or WORKING_DIR
        dir_path = os.path.normpath(os.path.join(root, dir_path))
        # セキュリティ: 解決後パスがプロジェクトルート配下であることを検証
        if not dir_path.startswith(root):
            say(text=f":warning: プロジェクトルート外のパスは指定できません: `{dir_path}`", thread_ts=thread_ts)
            return

    if not os.path.isdir(dir_path):
        say(text=f":warning: ディレクトリが見つかりません: `{dir_path}`", thread_ts=thread_ts)
        return

    task = Task(
        id=0,
        prompt=prompt,
        channel_id=channel_id,
        working_dir=dir_path,
        allowed_tools=settings.consume_tools(),
        user_id=user_id,
    )
    err = runner.run_task(task)
    if err:
        say(text=err, thread_ts=thread_ts)


def _handle_sessions(say, thread_ts):
    """セッション履歴を表示"""
    if not runner.task_history:
        say(text="セッション履歴はまだありません", thread_ts=thread_ts)
        return

    lines = [":clipboard: *セッション履歴*"]
    for task in reversed(runner.task_history[-10:]):
        status_emoji = {
            TaskStatus.COMPLETED: ":white_check_mark:",
            TaskStatus.FAILED: ":x:",
            TaskStatus.CANCELLED: ":stop_sign:",
        }.get(task.status, ":grey_question:")

        sid = task.session_id[:16] + "..." if task.session_id else "N/A"
        prompt_preview = task.prompt[:50] + ("..." if len(task.prompt) > 50 else "")
        dir_name = os.path.basename(task.working_dir or WORKING_DIR)
        elapsed = ""
        if task.started_at and task.completed_at:
            secs = (task.completed_at - task.started_at).total_seconds()
            elapsed = f" ({secs:.0f}秒)"

        lines.append(
            f"{status_emoji} {task.short_id} `{sid}` :file_folder:`{dir_name}` {prompt_preview}{elapsed}"
        )

    lines.append("\n_`continue #<id> <指示>` or `resume <session_id> <指示>` で再開_")
    say(text="\n".join(lines), thread_ts=thread_ts)


def _handle_detect(say, thread_ts):
    """実行中のclaude CLIインスタンスを再検出"""
    # 死んだインスタンスをクリーンアップ
    dead = [ts for ts, data in instance_threads.items()
            if not _is_process_alive(data["pid"])]
    for ts in dead:
        del instance_threads[ts]
    if dead:
        _save_instance_state()

    instances = detect_running_claude_instances()
    if not instances:
        say(text=":mag: 実行中のclaude CLIインスタンスが見つかりません", thread_ts=thread_ts)
        return

    # 既に登録済みのPIDを除外
    tracked_pids = {data["pid"] for data in instance_threads.values()}
    new_instances = [i for i in instances if i["pid"] not in tracked_pids]

    if not new_instances:
        say(
            text=f":mag: 新しいインスタンスはありません（{len(tracked_pids)}件追跡中）",
            thread_ts=thread_ts,
        )
        return

    registered = 0
    for inst in new_instances:
        try:
            if _register_instance(inst):
                registered += 1
        except Exception as e:
            logger.warning("インスタンス登録エラー: %s", e)

    say(
        text=f":mag: {registered}件の新しいclaude CLIインスタンスを検出・登録しました",
        thread_ts=thread_ts,
    )


# ── エントリーポイント ────────────────────────────────────
def main():
    logger.info("=" * 55)
    logger.info("  Claude Code ⇔ Slack Bridge")
    logger.info("=" * 55)
    logger.info("  Admin:       %s", ADMIN_SLACK_USER_ID)
    logger.info("  Work Dir:    %s", WORKING_DIR)
    logger.info("  Tools:       %s", DEFAULT_ALLOWED_TOOLS)
    logger.info("  Allowed Users:    %s", SLACK_ALLOWED_USERS or "(none)")
    logger.info("  Allowed Channels: %s", SLACK_ALLOWED_CHANNELS or "(none)")
    logger.info("  Notification:     %s", NOTIFICATION_CHANNEL or "(log only)")
    logger.info("=" * 55)
    logger.info("Ctrl+C で終了")

    # チャンネル→プロジェクトルート紐付けを読み込み
    _load_channel_projects()
    if _channel_projects:
        logger.info("チャンネルプロジェクト紐付け: %d件", len(_channel_projects))

    # 起動通知
    if NOTIFICATION_CHANNEL:
        try:
            slack_client.chat_postMessage(
                channel=NOTIFICATION_CHANNEL,
                text=(
                    ":rocket: *Claude Code Bridge が起動しました*\n"
                    f":file_folder: デフォルト作業ディレクトリ: `{WORKING_DIR}`\n"
                    "チャンネルで `@bot <タスク>` を送信してください"
                ),
            )
        except Exception as e:
            logger.warning("Slack起動通知の送信に失敗: %s", e)

    # 保存された前回のインスタンス状態を読み込み（PID → thread_ts）
    saved_pid_threads = _load_instance_state()
    if saved_pid_threads:
        logger.info("前回の状態: %d件のインスタンスが生存中", len(saved_pid_threads))

    # 実行中のclaude CLIインスタンスを検出（NOTIFICATION_CHANNELがある場合のみSlack投稿）
    instances = detect_running_claude_instances()
    if instances:
        logger.info("検出されたclaude CLIインスタンス: %d件", len(instances))
        if NOTIFICATION_CHANNEL:
            for inst in instances:
                try:
                    reuse_ts = saved_pid_threads.get(inst["pid"])
                    _register_instance(inst, reuse_thread_ts=reuse_ts)
                except Exception as e:
                    logger.warning("インスタンス通知の送信に失敗: %s", e)
    else:
        logger.info("実行中のclaude CLIインスタンス: なし")

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)

    def shutdown(signum, frame):
        logger.info("シャットダウン中...")
        runner.cancel_all()
        if NOTIFICATION_CHANNEL:
            try:
                slack_client.chat_postMessage(
                    channel=NOTIFICATION_CHANNEL,
                    text=":wave: Claude Code Bridge を停止しました",
                )
            except Exception:
                pass
        handler.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    handler.start()


if __name__ == "__main__":
    main()
