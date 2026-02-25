#!/usr/bin/env python3
"""
Claude Code ⇔ Slack Bridge
===========================
Mac上で動くClaude CodeをSlackから操作するブリッジ。
複数タスクの同時実行に対応。チャンネルモードのみ（@bot メンション必須）。

3層データモデル:
  Project (= Slack Channel) — bind でプロジェクトルートを設定
  Session (= Slack Thread)  — Project 内で並列。タスクの直列チェーン
  Task    (= 指示→完了)     — Session 内で直列。スレッド返信で自動 --resume

コマンド:
  <タスク内容>              → 新しいセッション＋タスクを実行（bind必須）
  (スレッド返信) <指示>     → 同セッションで --resume 続行
  in <path> <タスク>        → 指定ディレクトリでセッション作成＋タスクを実行
  status                    → プロジェクト内タスクの状態一覧
  sessions                  → プロジェクト内セッション一覧
  cancel #id                → タスクをキャンセル
  cancel all                → プロジェクト内全タスクをキャンセル
  bind                      → usage表示＋バインド可能なプロセスリスト
  bind -d <path>            → チャンネルにプロジェクトルートを紐付け
  bind -p <PID>             → 実行中のclaude CLIプロセスをフォーク
  unbind                    → プロジェクトルートの紐付けを解除
  tools <list>              → 次回タスクの許可ツール設定（スレッド内のみ）
  help                      → ヘルプ表示
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
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from urllib.parse import urlparse
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

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
MAX_SLACK_FILE_SIZE = 20 * 1024 * 1024  # 20MB
CHANNEL_PROJECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_projects.json")


# ---------------------------------------------------------------------------
# Slack 添付画像ダウンロード・正規化
# ---------------------------------------------------------------------------


class _NoAutoRedirect(urllib.request.HTTPRedirectHandler):
    """リダイレクトを自動追跡しないハンドラ（手動でAuth付きリダイレクトを行うため）"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            newurl, code, msg, headers, fp
        )

_no_redirect_opener = urllib.request.build_opener(_NoAutoRedirect)


def _download_slack_file_content(url: str, token: str) -> bytes | None:
    """SlackのファイルURLからバイナリデータをダウンロードする。

    Python 3.11+ の urllib は異なるホストへのリダイレクト時に Authorization ヘッダーを
    セキュリティ上の理由で自動削除する。Slackのファイル URL は
    files.slack.com → <workspace>.slack.com 等にリダイレクトするため、
    リダイレクトを手動追跡し、各ステップで Authorization を付与する。
    """
    headers = {"Authorization": f"Bearer {token}"}

    for _attempt in range(5):
        req = urllib.request.Request(url, headers=headers)
        try:
            with _no_redirect_opener.open(req) as resp:
                data = resp.read()
                logger.info("Slackファイル取得: status=%d, url=%s", resp.status, url[:80])
                # HTMLログインページが返された場合
                if data[:15] == b"<!DOCTYPE html>" or data[:5] == b"<html":
                    logger.error("Slackがファイルの代わりにHTMLを返却 (%d bytes)。"
                                 "Bot tokenの files:read スコープを確認してください。", len(data))
                    return None
                return data
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                new_url = e.headers.get("Location", "")
                if not new_url:
                    logger.warning("リダイレクトにLocationヘッダーなし")
                    return None
                # 相対URLを絶対URLに変換
                if new_url.startswith("/"):
                    parsed = urlparse(url)
                    new_url = f"{parsed.scheme}://{parsed.netloc}{new_url}"
                logger.info("リダイレクト追跡 (%d): %s → %s", e.code, url[:60], new_url[:60])
                url = new_url
                continue
            logger.warning("Slackファイルダウンロード: HTTP %d", e.code)
            return None

    logger.warning("Slackファイルダウンロード: リダイレクト回数超過")
    return None


# Claude API がサポートする画像形式
_API_SUPPORTED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}
# 画像の長辺ピクセル上限（これを超えるとリサイズ）
_MAX_IMAGE_LONG_EDGE = 8000


def _normalize_image(path: str) -> str | None:
    """画像ファイルを検証し、APIが処理できる形式に正規化する。

    成功時は（変換後の）パスを返す。ファイルが無効な場合は None を返す。
    Pillow 未インストール時はそのまま返す。
    """
    if PILImage is None:
        return path

    try:
        img = PILImage.open(path)
        img.load()  # 実際にデコードして破損を検出
    except Exception:
        # ファイルの先頭バイトをログ出力（HTMLエラーページ等の診断用）
        try:
            with open(path, "rb") as f:
                head = f.read(64)
            logger.error("画像ファイルを開けない: %s (先頭bytes: %r)", path, head)
        except Exception:
            logger.exception("画像ファイルを開けない（読み取りも失敗）: %s", path)
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    try:
        fmt = img.format
        w, h = img.size
        needs_convert = fmt not in _API_SUPPORTED_FORMATS
        needs_resize = max(w, h) > _MAX_IMAGE_LONG_EDGE

        if not needs_convert and not needs_resize:
            img.close()
            return path

        if needs_resize:
            ratio = _MAX_IMAGE_LONG_EDGE / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)

        # 保存形式を決定（透過があればPNG、それ以外はJPEG）
        if img.mode in ("RGBA", "LA", "P"):
            out_fmt, ext = "PNG", ".png"
        else:
            if img.mode != "RGB":
                img = img.convert("RGB")
            out_fmt, ext = "JPEG", ".jpg"

        base = os.path.splitext(path)[0]
        out_path = base + ext
        img.save(out_path, out_fmt, quality=95)
        new_w, new_h = img.size
        img.close()

        if out_path != path:
            try:
                os.remove(path)
            except OSError:
                pass

        logger.info("画像を正規化: %s → %s (%dx%d, %s)",
                    os.path.basename(path), os.path.basename(out_path), new_w, new_h, out_fmt)
        return out_path
    except Exception:
        logger.exception("画像の変換に失敗: %s", path)
        img.close()
        return path  # 変換失敗時は元ファイルをそのまま試す


def _resolve_event_files(event: dict, channel_id: str = "") -> list[dict]:
    """イベントからファイル情報を抽出する。event.filesが空の場合はAPI経由で再取得を試みる。

    タスク作成時にのみ呼ぶこと（毎メッセージでは呼ばない）。
    """
    files = event.get("files", [])
    if files:
        return files

    # event.filesが空の場合、ファイルが添付されている手がかりを探す
    file_ids = event.get("file_ids", [])
    blocks = event.get("blocks", [])
    has_upload = event.get("upload", False)
    subtype = event.get("subtype", "")

    # blocks内のfileブロックからfile_idを抽出
    if not file_ids:
        for block in blocks:
            if block.get("type") == "file":
                fid = block.get("file_id") or block.get("external_id")
                if fid:
                    file_ids.append(fid)

    has_file_hints = file_ids or has_upload or subtype == "file_share"
    if has_file_hints:
        logger.info("event.filesは空だがファイルの手がかりあり (subtype=%s, upload=%s, file_ids=%s, event_keys=%s)",
                    subtype, has_upload, file_ids, sorted(event.keys()))

    # file_idがあればSlack APIでファイル情報を取得
    if file_ids:
        logger.info("file_ids=%sからファイル情報を取得", file_ids)
        resolved = []
        for fid in file_ids:
            try:
                resp = slack_client.files_info(file=fid)
                if resp.get("ok"):
                    resolved.append(resp["file"])
            except Exception:
                logger.exception("files.info失敗: %s", fid)
        if resolved:
            return resolved

    # conversations.historyでメッセージを再取得してファイル情報を確認
    # （新しいSlack APIではevent.filesが空でもAPIからは取得できる場合がある）
    msg_ts = event.get("ts", "")
    if msg_ts and channel_id:
        try:
            resp = slack_client.conversations_history(
                channel=channel_id,
                latest=msg_ts,
                inclusive=True,
                limit=1,
            )
            msgs = resp.get("messages", [])
            if msgs:
                refetched = msgs[0].get("files", [])
                if refetched:
                    logger.info("conversations.historyからfiles=%d件取得: %s",
                                len(refetched),
                                [(f.get("name"), f.get("mimetype")) for f in refetched])
                    return refetched
        except Exception:
            logger.exception("conversations.historyでの再取得失敗")

    if has_file_hints:
        logger.warning("ファイルの手がかりはあったが取得できず (event_keys=%s)", sorted(event.keys()))
    return []


def _download_slack_files(files: list[dict], save_dir: str) -> list[str]:
    """Slackの添付ファイル（画像のみ）をダウンロードしローカルパスのリストを返す"""
    # mimetypeがない場合filetypeからの推定もサポート
    _IMAGE_FILETYPES = {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tiff"}

    def _is_image(f: dict) -> bool:
        mime = f.get("mimetype", "")
        if mime and mime.startswith("image/"):
            return True
        ft = f.get("filetype", "").lower()
        return ft in _IMAGE_FILETYPES

    image_files = [f for f in files if _is_image(f)]
    if not image_files:
        logger.info("添付ファイルに画像なし: %s",
                    [(f.get("name"), f.get("mimetype"), f.get("filetype")) for f in files])
        return []

    attach_dir = os.path.join(save_dir, ".slack-attachments")
    os.makedirs(attach_dir, exist_ok=True)

    paths: list[str] = []
    for f in image_files:
        size = f.get("size", 0)
        if size > MAX_SLACK_FILE_SIZE:
            logger.warning("添付ファイルが大きすぎるためスキップ: %s (%d bytes)", f.get("name"), size)
            continue

        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            logger.warning("添付ファイルにダウンロードURLなし: %s (keys=%s)", f.get("name"), list(f.keys()))
            continue

        # ファイル名のスペースをアンダースコアに置換（パス解釈の問題を防止）
        raw_name = f.get("name", "image").replace(" ", "_")
        filename = f"{int(time.time())}_{raw_name}"
        dest = os.path.join(attach_dir, filename)
        try:
            data = _download_slack_file_content(url, SLACK_BOT_TOKEN)
            if data is None:
                logger.warning("添付ファイルのダウンロードに失敗（無効なレスポンス）: %s", raw_name)
                continue
            with open(dest, "wb") as out:
                out.write(data)

            # 画像を検証・正規化（形式変換、リサイズ等）
            normalized = _normalize_image(dest)
            if normalized:
                paths.append(normalized)
                logger.info("添付画像を準備: %s", normalized)
            else:
                logger.warning("添付画像の正規化に失敗、スキップ: %s", raw_name)
        except Exception:
            logger.exception("添付ファイルのダウンロードに失敗: %s", raw_name)
    return paths


def _augment_prompt_with_images(prompt: str, image_paths: list[str]) -> str:
    """画像パスをプロンプト末尾に追記"""
    lines = "\n".join(image_paths)
    return f"{prompt}\n\n添付画像:\n{lines}"


# ---------------------------------------------------------------------------
# Markdown → Slack mrkdwn 変換
# ---------------------------------------------------------------------------
# Claude CLI は標準 Markdown で出力するが、Slack の mrkdwn は異なる記法を使う。
#   **bold** → *bold*,  ***bold italic*** → *_text_*,
#   *italic* → _italic_,  ~~strike~~ → ~strike~,
#   # Header → *Header*,  [text](url) → <url|text>,
#   - item → • item,  table → code block,  --- → ━━━
# コードブロック / インラインコード内部は変換しない。
# CJK文字隣接時にゼロ幅スペース(U+200B)を挿入してSlackの書式レンダリングを保証。

def _md_to_slack(text: str) -> str:
    """標準 Markdown テキストを Slack mrkdwn 形式に変換する。"""
    _placeholders: list[str] = []
    ZWS = "\u200B"  # ゼロ幅スペース: Slack mrkdwn の書式境界を確保

    def _ph(content: str) -> str:
        """テキストをプレースホルダーに退避"""
        _placeholders.append(content)
        return f"\x00CB{len(_placeholders) - 1}\x00"

    def _save(replacement: str):
        """変換済みテキストをZWS付きプレースホルダーに退避して返す"""
        def _inner(m: re.Match) -> str:
            converted = replacement.replace(r"\1", m.group(1))
            return _ph(f"{ZWS}{converted}{ZWS}")
        return _inner

    def _save_raw(m: re.Match) -> str:
        """マッチしたテキストをそのまま退避（コードブロック用）"""
        code = m.group(0)
        # コードブロックの言語指定を除去（Slack では表示されるだけで意味がない）
        if code.startswith("```"):
            code = re.sub(r"^```\w*", "```", code)
        return _ph(code)

    # コードブロック (```...```) → 退避
    text = re.sub(r"```[\s\S]*?```", _save_raw, text)
    # インラインコード (`...`) → 退避
    text = re.sub(r"`[^`\n]+`", _save_raw, text)

    # Markdown テーブル（|で始まる連続行、2行以上）→ コードブロック化して退避
    text = re.sub(
        r"(?:^[ \t]*\|[^\n]*\|[ \t]*$\n?){2,}",
        lambda m: _ph(f"```\n{m.group(0).rstrip()}\n```"),
        text, flags=re.MULTILINE,
    )

    # 水平線（行全体が --- / *** / ___ 等）→ 区切り線
    text = re.sub(r"^[ \t]*([-*_])\1{2,}[ \t]*$", "━━━━━━━━━━━━━━━━━━━━", text, flags=re.MULTILINE)

    # ブロック引用: ネストされた引用 (>>, > >, ...) → 単一レベルに平坦化 (Slackは1レベルのみ)
    text = re.sub(r"^(?:>[ \t]*){2,}", "> ", text, flags=re.MULTILINE)

    # 順序なしリスト: 行頭の - / * / + → •（太字/イタリック変換前に実行）
    text = re.sub(r"^([ \t]*)[-*+] ", r"\1• ", text, flags=re.MULTILINE)

    # タスクリスト: [x]/[X] → ✅, [ ]/[] → ☐ (コードブロック退避済みなので安全)
    text = re.sub(r"\[([xX])\]", "✅", text)
    text = re.sub(r"\[ ?\]", "☐", text)

    # --- インライン書式変換 ---
    # ***bold italic*** → *_text_*
    text = re.sub(r"\*\*\*(.+?)\*\*\*", _save(r"*_\1_*"), text)
    # **bold** / __bold__ → *bold*（退避して italic 誤変換を防止）
    text = re.sub(r"\*\*(.+?)\*\*", _save(r"*\1*"), text)
    text = re.sub(r"__(.+?)__", _save(r"*\1*"), text)
    # *italic* → _italic_（ZWS付き）
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
                  lambda m: f"{ZWS}_{m.group(1)}_{ZWS}", text)
    # ~~strikethrough~~ → ~strikethrough~（ZWS付き）
    text = re.sub(r"~~(.+?)~~",
                  lambda m: f"{ZWS}~{m.group(1)}~{ZWS}", text)
    # [text](url) → <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    # # Header → *Header*（行頭の # を太字に変換）
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # プレースホルダー復元（逆順: テーブル内のインラインコード等、ネストを正しく展開）
    for i in range(len(_placeholders) - 1, -1, -1):
        text = text.replace(f"\x00CB{i}\x00", _placeholders[i])

    return text


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


# ── データ構造（3層モデル: Project → Session → Task） ────
class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """1回のClaude Code呼び出し。Session内で直列実行される。"""
    id: int
    prompt: str
    status: TaskStatus = TaskStatus.QUEUED
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tool_calls: list = field(default_factory=list)
    allowed_tools: Optional[str] = None
    user_id: Optional[str] = None

    # プロセス管理（実行中のみ）
    process: Optional[subprocess.Popen] = None
    master_fd: Optional[int] = None

    # セッション継続（Session.claude_session_id から自動設定）
    resume_session: Optional[str] = None

    @property
    def short_id(self) -> str:
        return f"#{self.id}"


@dataclass
class Session:
    """Slackスレッド = 1セッション。Project内で並列、タスクは直列チェーン。"""
    thread_ts: str                    # Slackスレッド親ts = 識別子
    channel_id: str                   # 所属チャンネル
    claude_session_id: Optional[str] = None  # Claude CLIのsession_id
    label_emoji: str = ""
    label_name: str = ""
    working_dir: Optional[str] = None  # in コマンドによるオーバーライド
    created_at: Optional[datetime] = None
    tasks: list[Task] = field(default_factory=list)
    next_tools: Optional[str] = None  # tools コマンドで設定、次タスク実行時に消費
    pending_question: Optional[dict] = None  # プロセス終了後の --resume 用質問メタデータ

    @property
    def active_task(self) -> Optional[Task]:
        """実行中のタスクを返す"""
        for t in self.tasks:
            if t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                return t
        return None

    @property
    def latest_task(self) -> Optional[Task]:
        """最後のタスクを返す"""
        return self.tasks[-1] if self.tasks else None

    @property
    def display_label(self) -> str:
        return f"{self.label_emoji} {self.label_name}" if self.label_name else self.label_emoji

    def consume_tools(self) -> Optional[str]:
        """next_tools を取り出してリセット"""
        tools = self.next_tools
        self.next_tools = None
        return tools


@dataclass
class Project:
    """Slackチャンネル = 1プロジェクト。bind で作成、ルートディレクトリ必須。"""
    channel_id: str                   # Slackチャンネル = 識別子
    root_dir: str                     # プロジェクトルート（絶対パス、必須）
    sessions: dict[str, Session] = field(default_factory=dict)

    def assign_label(self, session: Session):
        """ラベル割り当て（プロジェクトスコープ：アクティブセッション間で重複なし）"""
        used = set()
        for s in self.sessions.values():
            if s.active_task and s.label_name:
                used.add(s.label_name)
        for emoji, name in TASK_LABELS:
            if name not in used:
                session.label_emoji = emoji
                session.label_name = name
                return
        session.label_emoji = "⚪"
        session.label_name = f"session-{session.thread_ts[:8]}"

    def get_or_create_session(self, thread_ts: str) -> Session:
        """既存セッションを返すか、新規作成する"""
        if thread_ts not in self.sessions:
            session = Session(
                thread_ts=thread_ts,
                channel_id=self.channel_id,
                created_at=datetime.now(),
            )
            self.assign_label(session)
            self.sessions[thread_ts] = session
        return self.sessions[thread_ts]

    def find_task_by_id(self, task_id: int) -> Optional[tuple["Session", Task]]:
        """タスクIDからセッションとタスクを検索"""
        for session in self.sessions.values():
            for task in session.tasks:
                if task.id == task_id:
                    return (session, task)
        return None

    def find_session_by_claude_id(self, claude_session_id: str) -> Optional["Session"]:
        """Claude CLIのsession_idからセッションを検索（部分一致対応）"""
        for session in self.sessions.values():
            if session.claude_session_id and session.claude_session_id.startswith(claude_session_id):
                return session
        return None

    @property
    def active_sessions(self) -> list["Session"]:
        """アクティブタスクを持つセッション一覧"""
        return [s for s in self.sessions.values() if s.active_task]

    @property
    def all_tasks(self) -> list[Task]:
        """全タスク一覧"""
        tasks = []
        for session in self.sessions.values():
            tasks.extend(session.tasks)
        return tasks


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

            # ExitPlanMode検出（プラン承認質問として表示）
            if tool_name == "ExitPlanMode":
                formatted, meta = _format_exit_plan_mode(tool_input)
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
            "question": question_text,
            "options": option_items,
            "multi_select": multi_select,
        })

    if not all_parts:
        return ("", None)

    formatted = "\n\n".join(all_parts)
    metadata = {"questions": all_questions_meta}
    return (formatted, metadata)


def _format_exit_plan_mode(tool_input: dict) -> tuple[str, dict | None]:
    """ExitPlanModeをプラン承認質問として整形。
    tool_inputからプラン内容を抽出して表示する。"""
    plan_text = ""
    if isinstance(tool_input, dict):
        # 既知のフィールドを順に探す
        for key in ("plan", "content", "summary"):
            val = tool_input.get(key, "")
            if isinstance(val, str) and val.strip():
                plan_text = val.strip()
                break
        # allowedPrompts の表示
        prompts = tool_input.get("allowedPrompts", [])
        if isinstance(prompts, list) and prompts:
            prompt_lines = []
            for p in prompts:
                if isinstance(p, dict):
                    prompt_lines.append(f"- `{p.get('tool', '?')}`: {p.get('prompt', '')}")
            if prompt_lines:
                plan_text += "\n\n*許可プロンプト:*\n" + "\n".join(prompt_lines)

    lines = [":clipboard: *プランの承認が必要です*"]
    if plan_text:
        # Slack表示用にプラン内容をコードブロックで表示（長い場合は切り詰め）
        display = plan_text
        max_plan_len = 2500  # 質問テキスト等のオーバーヘッドを考慮
        if len(display) > max_plan_len:
            display = display[:max_plan_len] + "\n...(省略)"
        lines.append(f"```\n{display}\n```")
    lines.append("  1. 承認して実行")
    lines.append("  2. 却下・フィードバック")
    lines.append("_番号を返信してください（テキストでフィードバックも可）_")

    metadata = {
        "questions": [{
            "options": [
                {"label": "承認して実行", "description": ""},
                {"label": "却下・フィードバック", "description": ""},
            ],
            "multi_select": False,
        }]
    }
    return ("\n".join(lines), metadata)


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
        session_ref = inst.get("session")  # Session オブジェクト参照
        entry_type = entry.get("type", "")
        msg = entry.get("message", {})
        # session_id → Session に設定（task_ref がなくても実行）
        if isinstance(msg, dict):
            sid = msg.get("sessionId") or entry.get("sessionId")
        else:
            sid = entry.get("sessionId")
        if sid and session_ref:
            logger.debug("JSONL監視: session_id取得 sid=%s thread=%s", sid[:16] if sid else None, thread_ts)
            session_ref.claude_session_id = sid
        if not task_ref:
            return
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
        """応答テキストをステータスメッセージに追記。同一テキストの重複投稿を防止。"""
        nonlocal last_posted_text
        if text == last_posted_text:
            return  # 同一テキストの重複投稿を防止
        last_posted_text = text
        # JSONL経由で正式な応答が来たため、PTYペンディングメッセージを削除
        _finalize_pty_pending(inst, channel, client, delete=True)
        display = _md_to_slack(text)
        if len(display) > MAX_SLACK_MSG_LENGTH:
            display = "...\n" + display[-MAX_SLACK_MSG_LENGTH:]
        status_lines.append(f":speech_balloon: {display_prefix}\n{display}")
        _flush_status(final=True)

    def _post_question(text: str, metadata: dict | None):
        """AskUserQuestion の選択肢をスレッドに投稿し、pending_questionを設定。"""
        # JSONL経由で正式な質問が来たため、PTYペンディングメッセージを削除
        _finalize_pty_pending(inst, channel, client, delete=True)
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
            pq = {
                "question": q.get("question", ""),
                "options": q["options"],
                "multi_select": q["multi_select"],
            }
            inst["pending_question"] = pq
            # Sessionにも保存（プロセス終了後の --resume 用）
            session_ref = inst.get("session")
            if session_ref:
                session_ref.pending_question = pq

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
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    status_lines.append(text)
                    has_status = True
                elif category == "text":
                    text_parts.append(text)
                elif category == "question":
                    # 先にテキストを確定
                    if text_parts:
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    _finalize_status()
                    has_status = False
                    # 選択肢をスレッドに投稿（質問は別メッセージ）
                    _post_question(text, metadata)

        # テキスト応答があれば追記
        if text_parts:
            _post_text("\n".join(text_parts))
        elif has_status:
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
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    status_lines.append(text)
                elif category == "text":
                    text_parts.append(text)
                elif category == "question":
                    if text_parts:
                        _post_text("\n".join(text_parts))
                        text_parts = []
                    _finalize_status()
                    _post_question(text, metadata)
        if text_parts:
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

    # フォーク用クリーンアップ（bridge-spawned タスクは _execute の finally で既に除去済み）
    if inst.get("session") and not inst.get("skip_exit_message"):
        for ts, data in list(instance_threads.items()):
            if data is inst:
                instance_threads.pop(ts, None)
                break


def _extract_session_info_from_jsonl(task: Task, session: Optional["Session"], jsonl_path: str):
    """JONLファイルからsession_idとresultを抽出（フォールバック用）。
    session が指定されていれば claude_session_id も更新する。"""
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
                # sessionId
                if isinstance(msg, dict):
                    sid = msg.get("sessionId") or entry.get("sessionId")
                else:
                    sid = entry.get("sessionId")
                if sid:
                    if session:
                        session.claude_session_id = sid
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


def _finalize_pty_pending(inst: dict, channel_id: str, client: WebClient,
                          *, delete: bool = False):
    """PTYのペンディングメッセージを確定。
    delete=True: JSONL経由で正式な応答が投稿されるため、PTYメッセージを削除。
    delete=False: プロセス終了時等、⏳インジケータを除去してメッセージを残す。"""
    pending_ts = inst.pop("pty_pending_msg_ts", None)
    pending_text = inst.pop("pty_pending_text", None)
    if not pending_ts:
        return
    try:
        if delete:
            client.chat_delete(channel=channel_id, ts=pending_ts)
        elif pending_text:
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

    # フォーク用クリーンアップ（bridge-spawned タスクは _execute の finally で既に除去済み）
    if inst.get("session") and not inst.get("skip_exit_message"):
        for ts, data in list(instance_threads.items()):
            if data is inst:
                instance_threads.pop(ts, None)
                break


# ── Claude Code ランナー ──────────────────────────────────
class ClaudeCodeRunner:
    """Project → Session → Task の3層構造でClaude Codeタスクを管理"""

    def __init__(self, slack_client: WebClient):
        self.client = slack_client
        self.projects: dict[str, Project] = {}  # channel_id → Project
        self.lock = threading.Lock()
        self._task_counter = 0

    def _next_id(self) -> int:
        self._task_counter += 1
        return self._task_counter

    # ── プロジェクト管理 ──
    def bind_project(self, channel_id: str, root_dir: str) -> Project:
        """チャンネルにプロジェクトルートを紐付け"""
        if channel_id in self.projects:
            self.projects[channel_id].root_dir = root_dir
        else:
            self.projects[channel_id] = Project(channel_id=channel_id, root_dir=root_dir)
        self.save_projects()
        return self.projects[channel_id]

    def unbind_project(self, channel_id: str) -> Optional[str]:
        """プロジェクトルートの紐付けを解除。旧パスを返す。"""
        project = self.projects.pop(channel_id, None)
        if project:
            self.save_projects()
            return project.root_dir
        return None

    def get_project(self, channel_id: str) -> Optional[Project]:
        return self.projects.get(channel_id)

    def save_projects(self):
        """Project の channel_id→root_dir のみ永続化（channel_projects.json 形式）"""
        data = {p.channel_id: p.root_dir for p in self.projects.values()}
        try:
            with open(CHANNEL_PROJECTS_FILE, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("チャンネルプロジェクト設定の保存に失敗: %s", e)

    def load_projects(self):
        """channel_projects.json からプロジェクトを読み込み"""
        try:
            with open(CHANNEL_PROJECTS_FILE, "r") as f:
                data = json.load(f)
            for channel_id, root_dir in data.items():
                if channel_id not in self.projects:
                    self.projects[channel_id] = Project(channel_id=channel_id, root_dir=root_dir)
                else:
                    self.projects[channel_id].root_dir = root_dir
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as e:
            logger.warning("チャンネルプロジェクト設定の読み込みに失敗: %s", e)

    # ── タスク検索 ──
    def find_task_globally(self, task_id: int) -> Optional[tuple[Project, Session, Task]]:
        """全プロジェクトからタスクIDで検索"""
        for project in self.projects.values():
            result = project.find_task_by_id(task_id)
            if result:
                session, task = result
                return (project, session, task)
        return None

    # ── コマンドビルド ──
    def build_command(self, task: Task, prompt_as_arg: bool = False) -> list[str]:
        cmd = [CLAUDE_CMD, "-p"]
        cmd.append("--verbose")

        tools = task.allowed_tools or DEFAULT_ALLOWED_TOOLS
        if tools:
            cmd.extend(["--allowedTools", tools])

        if task.resume_session:
            cmd.extend(["--resume", task.resume_session])

        if prompt_as_arg:
            cmd.append("--")
            cmd.append(task.prompt)

        return cmd

    # ── タスク実行 ──
    def run_task(self, project: Project, session: Session, task: Task) -> Optional[str]:
        """タスク実行を開始。エラー時はメッセージ文字列を返す"""
        with self.lock:
            task.id = self._next_id()
            session.tasks.append(task)

        thread = threading.Thread(
            target=self._execute, args=(project, session, task), daemon=True
        )
        thread.start()
        return None

    def _execute(self, project: Project, session: Session, task: Task):
        cwd = session.working_dir or project.root_dir
        channel_id = session.channel_id
        thread_ts = session.thread_ts
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()

        dir_display = os.path.basename(cwd) or cwd
        display_label = f"{session.label_emoji} {task.short_id}"
        is_resume = task.resume_session is not None

        if is_resume:
            header = (
                f"{display_label}  :arrow_forward: *セッション続行*\n"
                f"```{task.prompt[:500]}```"
            )
        else:
            header = (
                f"{display_label}  *タスク開始*\n"
                f":file_folder: `{dir_display}`\n"
                f"```{task.prompt[:500]}```"
            )
        self._post_to_session(session, header)

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
                inst = {
                    "pid": proc.pid,
                    "cwd": cwd,
                    "master_fd": master_fd,
                    "task": task,
                    "session": session,
                    "display_prefix": display_label,
                    "skip_exit_message": True,
                }
                instance_threads[thread_ts] = inst
                registered_thread_ts = thread_ts

                # PTY出力監視スレッドを起動
                pty_thread = threading.Thread(
                    target=_monitor_pty_output,
                    args=(proc.pid, master_fd, thread_ts, channel_id,
                          self.client, instance_threads.get(thread_ts)),
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
            jsonl_path = None
            for poll_i in range(30):
                if proc.poll() is not None:
                    logger.debug("_execute: JONLポーリング中にプロセス終了 pid=%d poll=%d thread=%s", proc.pid, poll_i, thread_ts)
                    break
                found = _find_session_jsonl(cwd, min_ctime=start_time)
                if found:
                    jsonl_path = found
                    logger.debug("_execute: JONLファイル発見 path=%s poll=%d thread=%s", found, poll_i, thread_ts)
                    break
                time.sleep(1)
            else:
                logger.warning("_execute: JONLファイルが30秒以内に見つからず pid=%d cwd=%s thread=%s", proc.pid, cwd, thread_ts)

            # JSONL監視（プロセスがまだ実行中の場合）
            jsonl_monitored = False
            if jsonl_path and proc.poll() is not None:
                logger.debug("_execute: JSONL発見済みだがプロセス既に終了 pid=%d jsonl=%s thread=%s", proc.pid, jsonl_path, thread_ts)
            if jsonl_path and proc.poll() is None:
                if registered_thread_ts and registered_thread_ts in instance_threads:
                    inst = instance_threads[registered_thread_ts]
                    inst["jsonl_path"] = jsonl_path
                    inst["fixed_jsonl"] = True
                    inst["start_from_beginning"] = True
                    inst["task"] = task
                    inst["session"] = session
                else:
                    inst = {
                        "pid": proc.pid,
                        "cwd": cwd,
                        "jsonl_path": jsonl_path,
                        "display_prefix": display_label,
                        "skip_exit_message": True,
                        "fixed_jsonl": True,
                        "start_from_beginning": True,
                        "task": task,
                        "session": session,
                    }
                _monitor_session_jsonl(inst, thread_ts, channel_id, self.client)
                jsonl_monitored = True

            proc.wait()

            if task.status == TaskStatus.CANCELLED:
                self._post_to_session(session, f"{display_label}  :stop_sign: キャンセルされました")
                return

            # JSONL からsession_idを取得できなかった場合のフォールバック
            if not session.claude_session_id:
                fallback_path = jsonl_path or _find_session_jsonl(cwd, min_ctime=start_time)
                logger.debug("_execute: session_id未取得、フォールバック試行 fallback_path=%s jsonl_monitored=%s pid=%d thread=%s",
                             fallback_path, jsonl_monitored, proc.pid, thread_ts)
                if fallback_path:
                    _extract_session_info_from_jsonl(task, session, fallback_path)
                    if session.claude_session_id:
                        logger.debug("_execute: フォールバックでsession_id取得成功 sid=%s thread=%s",
                                     session.claude_session_id[:16], thread_ts)
                    else:
                        logger.warning("_execute: フォールバックでもsession_id取得失敗 jsonl=%s thread=%s", fallback_path, thread_ts)
                else:
                    logger.warning("_execute: フォールバック用JONLファイルも見つからず pid=%d cwd=%s thread=%s", proc.pid, cwd, thread_ts)
            else:
                logger.debug("_execute: タスク完了時 session_id=%s thread=%s", session.claude_session_id[:16], thread_ts)

            if proc.returncode == 0:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now()
                elapsed = (task.completed_at - task.started_at).total_seconds()
                summary = self._format_result(
                    task, session, elapsed, show_result=not jsonl_monitored
                )
                self._post_to_session(session, summary)
            else:
                stderr_raw = proc.stderr.read() if proc.stderr else ""
                stderr_output = stderr_raw.decode("utf-8", errors="replace") if isinstance(stderr_raw, bytes) else stderr_raw
                task.status = TaskStatus.FAILED
                task.error = stderr_output
                task.completed_at = datetime.now()
                self._post_to_session(
                    session,
                    f"{display_label}  :x: 失敗 (exit {proc.returncode})\n```{stderr_output[:1000]}```",
                )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now()
            self._post_to_session(session, f"{display_label}  :x: エラー: {e}")

        finally:
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
                task.master_fd = None
            if registered_thread_ts:
                instance_threads.pop(registered_thread_ts, None)

    def _format_result(self, task: Task, session: Session, elapsed: float,
                       show_result: bool = True) -> str:
        cwd = session.working_dir or session.channel_id
        # project から root_dir を取得
        project = self.projects.get(session.channel_id)
        dir_name = os.path.basename(project.root_dir if project else cwd)
        display_label = f"{session.label_emoji} {task.short_id}"
        user_info = f"  <@{task.user_id}>" if task.user_id else ""
        parts = [f"{display_label}  :white_check_mark: *タスク完了* ({elapsed:.0f}秒)  :file_folder: `{dir_name}`{user_info}"]
        if task.tool_calls:
            tools_summary = ", ".join(f"`{t['name']}`" for t in task.tool_calls[:10])
            if len(task.tool_calls) > 10:
                tools_summary += f" 他{len(task.tool_calls) - 10}件"
            parts.append(f"使用ツール ({len(task.tool_calls)}回): {tools_summary}")
        if show_result and task.result:
            result_text = _md_to_slack(task.result)[:MAX_SLACK_MSG_LENGTH]
            if len(task.result) > MAX_SLACK_MSG_LENGTH:
                result_text += "\n...(省略)"
            parts.append(f"\n{result_text}")
        if session.claude_session_id:
            parts.append(f"\n_Session: `{session.claude_session_id[:12]}...`_")
            parts.append(f"_このスレッドに返信すると自動で続行します_")
        return "\n".join(parts)

    def _post_to_session(self, session: Session, text: str):
        """セッション（スレッド）にメッセージを投稿"""
        try:
            if session.thread_ts:
                self.client.chat_postMessage(
                    channel=session.channel_id,
                    thread_ts=session.thread_ts,
                    text=text,
                )
            else:
                resp = self.client.chat_postMessage(
                    channel=session.channel_id, text=text
                )
                session.thread_ts = resp["ts"]
        except Exception as e:
            logger.error("Slack投稿エラー: %s", e)

    # ── キャンセル ──
    def cancel_task(self, task_id: int) -> bool:
        """グローバル検索でタスクをキャンセル"""
        result = self.find_task_globally(task_id)
        if not result:
            return False
        _, _, task = result
        if task.status != TaskStatus.RUNNING:
            return False
        task.status = TaskStatus.CANCELLED
        if task.process:
            try:
                task.process.terminate()
                task.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                task.process.kill()
        return True

    def cancel_all_in_project(self, channel_id: str) -> int:
        """プロジェクト内の全アクティブタスクをキャンセル"""
        project = self.projects.get(channel_id)
        if not project:
            return 0
        cancelled = 0
        for session in project.sessions.values():
            active = session.active_task
            if active and active.status == TaskStatus.RUNNING:
                active.status = TaskStatus.CANCELLED
                if active.process:
                    try:
                        active.process.terminate()
                        active.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        active.process.kill()
                cancelled += 1
        return cancelled

    def cancel_all(self) -> int:
        """全プロジェクトの全タスクをキャンセル（シャットダウン用）"""
        cancelled = 0
        for channel_id in list(self.projects.keys()):
            cancelled += self.cancel_all_in_project(channel_id)
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

# スレッドts → インスタンス情報のマッピング（起動時に検出したclaude CLIプロセス用）
instance_threads: dict[str, dict] = {}

# bind -p / bind（引数なし）の複数候補選択状態（channel_id → 選択情報）
pending_fork_selections: dict[str, dict] = {}


BOT_USER_ID = None


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
        files = event.get("files", [])
        if not text and not files:
            return

        parent_ts = event.get("thread_ts")

        # スレッド返信の処理
        if parent_ts:
            # 1. instance_threads に登録あり & アクティブタスク実行中 → PTY に入力転送
            if parent_ts in instance_threads:
                input_text = _strip_bot_mention(text)
                input_text = "/" + input_text[1:] if input_text.startswith("!") else input_text
                handled = _handle_instance_input(input_text, say, parent_ts, channel_id,
                                                _resolve_event_files(event, channel_id))
                if handled:
                    return
                # EIOでFalse返却 → instance_threads除去済み、セッション --resume パスへフォールスルー

            # 2. セッション存在 → --resume で新タスク自動作成（メンション不要）
            project = runner.get_project(channel_id)
            if project:
                session = project.sessions.get(parent_ts)
                if session:
                    logger.debug("スレッド返信: セッション発見 thread=%s claude_session_id=%s task数=%d active=%s",
                                 parent_ts, session.claude_session_id[:16] if session.claude_session_id else "None",
                                 len(session.tasks), session.active_task is not None)
                    input_text = _strip_bot_mention(text)
                    if not input_text:
                        input_text = text  # メンションなくてもOK
                    # コマンド判定（toolsコマンドはスレッド内で有効）
                    cmd_lower = input_text.lower()
                    if cmd_lower.startswith("tools "):
                        tools = input_text[6:].strip()
                        session.next_tools = tools
                        say(
                            text=f":wrench: このセッションの次のタスクの許可ツール: `{tools}`\n続けてタスクを送信してください",
                            thread_ts=parent_ts,
                        )
                        return
                    if cmd_lower.startswith("cancel"):
                        _handle_cancel(input_text, say, parent_ts, channel_id)
                        return
                    if cmd_lower == "status":
                        _handle_status(say, parent_ts, channel_id)
                        return
                    # 新タスクとして --resume 続行
                    resolved_files = _resolve_event_files(event, channel_id)
                    _handle_thread_reply_task(
                        input_text, project, session, say, parent_ts, user_id, resolved_files
                    )
                    return

            # フォールバック: メンション付きならコマンドとして処理
            stripped = _strip_bot_mention(text)
            if stripped != text:
                # メンション付きスレッド返信 → コマンドとして処理
                logger.debug("スレッド返信: セッション未発見、メンション付きコマンドとして処理 thread=%s channel=%s project=%s",
                             parent_ts, channel_id, project is not None)
                _dispatch_command(stripped, event, say)
            else:
                logger.debug("スレッド返信: セッション未発見かつメンションなし、無視 thread=%s channel=%s project=%s",
                             parent_ts, channel_id, project is not None)
            return

        # フォーク選択割り込み（メンション有無問わず、番号 or cancel のみ処理）
        if channel_id in pending_fork_selections:
            fork_input = _strip_bot_mention(text)
            if fork_input == text:
                fork_input = text  # メンションなしでもOK
            if _handle_fork_selection(fork_input, say, channel_id):
                return

        # トップレベルメッセージ: botメンション必須
        stripped = _strip_bot_mention(text)
        if stripped == text:
            return  # メンションなし → 無視
        text = stripped
        if not text and not files:
            return

        _dispatch_command(text, event, say)


def _build_question_answer_prompt(question_text: str, options: list[dict],
                                  selected_num: int | None, answer_label: str) -> str:
    """質問への回答プロンプトを構築。--resume なしでもClaude が文脈を理解できるよう質問全体を含める。"""
    parts = []
    if question_text:
        parts.append(f"以下の質問への回答です:\n質問: {question_text}")
    else:
        parts.append("前の質問への回答です:")
    if options:
        opts = "\n".join(
            f"  {i+1}. {o.get('label', '')}" + (f" — {o['description']}" if o.get('description') else "")
            for i, o in enumerate(options)
        )
        parts.append(f"選択肢:\n{opts}")
    if selected_num is not None:
        parts.append(f"回答: {selected_num}. {answer_label}")
    else:
        parts.append(f"回答: {answer_label}")
    return "\n".join(parts)


def _handle_thread_reply_task(prompt: str, project: Project, session: Session,
                              say, thread_ts: str, user_id: str,
                              files: list[dict] | None = None):
    """スレッド返信を --resume で新タスクとして実行"""
    # pending_question がある場合、回答を質問コンテキスト付きプロンプトに変換
    pq = session.pending_question
    if pq and not pq.get("multi_select", False):
        options = pq.get("options", [])
        question_text = pq.get("question", "")
        num_match = re.match(r"^\s*(\d+)\s*$", prompt)
        if num_match:
            num = int(num_match.group(1))
            if 1 <= num <= len(options):
                label = options[num - 1].get("label", prompt)
                prompt = _build_question_answer_prompt(question_text, options, num, label)
        else:
            # テキスト回答の場合もコンテキスト付与
            prompt = _build_question_answer_prompt(question_text, options, None, prompt)
        session.pending_question = None

    # 添付画像のダウンロードとプロンプト拡張
    if files:
        cwd = session.working_dir or project.root_dir
        image_paths = _download_slack_files(files, cwd)
        if image_paths:
            prompt = _augment_prompt_with_images(prompt, image_paths)

    # セッションにclaude_session_idがまだ設定されていない場合、短時間待機
    # （前タスクの_executeがJSONL処理中の可能性があるため）
    if not session.claude_session_id and session.tasks:
        logger.debug("_handle_thread_reply_task: session_id未設定、待機開始 thread=%s task数=%d", thread_ts, len(session.tasks))
        for wait_i in range(10):
            time.sleep(0.3)
            if session.claude_session_id:
                logger.debug("_handle_thread_reply_task: 待機中にsession_id取得 sid=%s wait=%.1fs", session.claude_session_id[:16], (wait_i+1)*0.3)
                break
        else:
            logger.warning("_handle_thread_reply_task: 3秒待機してもsession_id取得できず thread=%s", thread_ts)

    task = Task(
        id=0,
        prompt=prompt,
        allowed_tools=session.consume_tools(),
        user_id=user_id,
    )
    # セッションにclaude_session_idがあれば自動で --resume
    if session.claude_session_id:
        task.resume_session = session.claude_session_id
        logger.debug("_handle_thread_reply_task: --resume付きでタスク作成 sid=%s thread=%s", session.claude_session_id[:16], thread_ts)
    else:
        logger.warning("_handle_thread_reply_task: session_idなし、--resumeなしで新規タスク実行 thread=%s", thread_ts)

    err = runner.run_task(project, session, task)
    if err:
        say(text=err, thread_ts=thread_ts)


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
        _handle_status(say, thread_ts, channel_id)
        return

    # ── cancel ──
    if cmd_lower.startswith("cancel"):
        _handle_cancel(text, say, thread_ts, channel_id)
        return

    # ── bind [-d <path> | -p <PID>] ──
    if cmd_lower == "bind" or cmd_lower.startswith("bind "):
        _handle_bind(text, say, thread_ts, channel_id, user_id)
        return

    # ── unbind ──
    if cmd_lower == "unbind":
        _handle_unbind(say, thread_ts, channel_id)
        return

    # ── sessions ──
    if cmd_lower == "sessions":
        _handle_sessions(say, thread_ts, channel_id)
        return

    # ── tools <list> （トップレベルではエラー） ──
    if cmd_lower.startswith("tools "):
        say(
            text=":warning: `tools` コマンドはスレッド内でのみ有効です。タスクのスレッドに返信してください。",
            thread_ts=thread_ts,
        )
        return

    # ── continue / resume （廃止） ──
    if cmd_lower.startswith("continue") or cmd_lower.startswith("resume "):
        say(
            text=":warning: `continue` / `resume` は廃止されました。タスクのスレッドに返信すると自動で続行します。",
            thread_ts=thread_ts,
        )
        return

    # ── in <path> <タスク> ──
    if cmd_lower.startswith("in "):
        _handle_in_dir(text, say, thread_ts, channel_id, user_id, _resolve_event_files(event, channel_id))
        return

    # ── 新規タスク（bind必須） ──
    project = runner.get_project(channel_id)
    if not project:
        say(
            text=":warning: このチャンネルにはプロジェクトが設定されていません。\n`bind -d <path>` でプロジェクトルートを設定してください。",
            thread_ts=thread_ts,
        )
        return

    session = project.get_or_create_session(thread_ts)
    prompt = text
    # 添付画像のダウンロードとプロンプト拡張
    files = _resolve_event_files(event, channel_id)
    if files:
        cwd = session.working_dir or project.root_dir
        image_paths = _download_slack_files(files, cwd)
        if image_paths:
            prompt = _augment_prompt_with_images(prompt, image_paths)

    task = Task(
        id=0,
        prompt=prompt,
        allowed_tools=session.consume_tools(),
        user_id=user_id,
    )
    err = runner.run_task(project, session, task)
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


def _handle_instance_input(text: str, say, parent_ts: str, channel_id: str,
                           files: list[dict] | None = None) -> bool:
    """インスタンススレッドへの返信をPTY書き込みまたはクリップボード+ペースト経由でターミナルに転送。
    pending_questionがある場合は選択肢回答として処理する。
    戻り値: True=処理済み, False=EIOでinstance_threadsから除去済み（呼び出し元でフォールスルーすべき）"""
    inst = instance_threads[parent_ts]

    # 添付画像がある場合、ダウンロードしてテキストに画像パスを追記
    if files:
        cwd = inst.get("cwd", WORKING_DIR)
        image_paths = _download_slack_files(files, cwd)
        if image_paths:
            text = _augment_prompt_with_images(text, image_paths)
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
                        return True
                else:
                    # テキスト入力 → "Other" を選択してテキスト入力
                    pty_input = b"\x1b[B" * option_count + b"\r"
                    os.write(master_fd, pty_input)
                    time.sleep(0.5)
                    os.write(master_fd, text.encode("utf-8") + b"\r")
                    action_label = f"Other: {text[:50]}"

                inst.pop("pending_question", None)
                session_ref = inst.get("session")
                if session_ref:
                    session_ref.pending_question = None
                _finalize_pty_pending(inst, channel_id, slack_client)
            else:
                # 通常のテキスト入力
                if pending_q and pending_q.get("multi_select", False):
                    inst.pop("pending_question", None)
                    session_ref = inst.get("session")
                    if session_ref:
                        session_ref.pending_question = None
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
            return True

        # ── TTYがない場合のエラー ──
        if not tty:
            say(text=f":warning: PID {pid} にはTTYが接続されていません", thread_ts=parent_ts)
            return True

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
                    return True
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
            session_ref = inst.get("session")
            if session_ref:
                session_ref.pending_question = None
            _finalize_pty_pending(inst, channel_id, slack_client)
        else:
            # 通常のテキスト入力（既存動作）
            # multiSelectの場合もフォールバック
            if pending_q and pending_q.get("multi_select", False):
                inst.pop("pending_question", None)
                session_ref = inst.get("session")
                if session_ref:
                    session_ref.pending_question = None
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
    except OSError as e:
        if e.errno == 5:  # EIO — プロセス終了済み → instance_threadsから除去してフォールスルー
            instance_threads.pop(parent_ts, None)
            return False
        else:
            say(text=f":x: 入力送信エラー: {e}", thread_ts=parent_ts)
    except Exception as e:
        say(text=f":x: 入力送信エラー: {e}", thread_ts=parent_ts)
    return True


# Mentionイベントは無視（message.channels で処理済み）
@app.event("app_mention")
def handle_mention(event, say):
    pass


def _help_text() -> str:
    return (
        ":robot_face: *Claude Code Bridge* — 使い方:\n"
        "*基本操作:*\n"
        "• `@bot <タスク>` → 新しいセッション＋タスクを実行（bind必須）\n"
        "• (スレッド返信) `<指示>` → 同セッションで自動続行（メンション不要）\n"
        "• `@bot in <path> <タスク>` → 指定ディレクトリでセッション作成＋実行\n"
        "*管理:*\n"
        "• `@bot status` → プロジェクト内タスクの状態一覧\n"
        "• `@bot sessions` → プロジェクト内セッション一覧\n"
        "• `@bot cancel #2` → タスクをキャンセル\n"
        "• `@bot cancel all` → プロジェクト内全タスクをキャンセル\n"
        "*設定:*\n"
        "• `@bot bind` → usage表示＋バインド可能なプロセスリスト\n"
        "• `@bot bind -d <path>` → チャンネルにプロジェクトルートを紐付け\n"
        "• `@bot bind -p <PID>` → 実行中のclaude CLIプロセスをフォーク\n"
        "• `@bot unbind` → プロジェクトルートの紐付けを解除\n"
        "• (スレッド内) `tools <tool1,...>` → 次回の許可ツール設定"
    )


def _handle_status(say, thread_ts, channel_id: str):
    """プロジェクトスコープのタスク状態一覧"""
    project = runner.get_project(channel_id)
    if not project:
        say(text=":warning: このチャンネルにはプロジェクトが設定されていません", thread_ts=thread_ts)
        return

    active_sessions = project.active_sessions
    if not active_sessions:
        # 直近の完了タスクを表示
        all_tasks = project.all_tasks
        recent = [t for t in all_tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)][-5:]
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

    lines = [f":gear: *実行中のタスク ({len(active_sessions)}セッション)*  :file_folder: `{os.path.basename(project.root_dir)}`"]
    for session in active_sessions:
        task = session.active_task
        if not task:
            continue
        elapsed = (datetime.now() - task.started_at).total_seconds() if task.started_at else 0
        prompt_preview = task.prompt[:50] + ("..." if len(task.prompt) > 50 else "")
        tool_count = len(task.tool_calls)

        user_info = f"  <@{task.user_id}>" if task.user_id else ""
        lines.append(
            f"\n{session.label_emoji} {task.short_id}"
            f"  ({elapsed:.0f}秒, ツール{tool_count}回){user_info}\n"
            f"> {prompt_preview}"
        )
        if task.tool_calls:
            recent_tools = " -> ".join(f"`{t['name']}`" for t in task.tool_calls[-3:])
            lines.append(f"  最近: {recent_tools}")

    say(text="\n".join(lines), thread_ts=thread_ts)


def _handle_cancel(text: str, say, thread_ts, channel_id: str):
    arg = text[6:].strip().lower()

    if arg == "all":
        count = runner.cancel_all_in_project(channel_id)
        say(text=f":stop_sign: {count}件のタスクをキャンセルしました", thread_ts=thread_ts)
        return

    task_id = parse_task_id(arg)
    if task_id is None:
        # プロジェクト内のアクティブタスクが1つだけならそれをキャンセル
        project = runner.get_project(channel_id)
        if project:
            active = project.active_sessions
            if len(active) == 1 and active[0].active_task:
                task_id = active[0].active_task.id
        if task_id is None:
            say(
                text=":warning: キャンセルするタスクを指定してください: `cancel #2` or `cancel all`",
                thread_ts=thread_ts,
            )
            return

    if runner.cancel_task(task_id):
        say(text=f":stop_sign: タスク #{task_id} のキャンセルリクエストを送信しました", thread_ts=thread_ts)
    else:
        say(text=f"タスク #{task_id} は実行中ではありません", thread_ts=thread_ts)


def _handle_bind(text: str, say, thread_ts, channel_id: str, user_id: str = ""):
    """bind 統合ハンドラ: -d <path> / -p <PID> / 引数なし(usage+プロセスリスト)"""
    rest = text[4:].strip()  # "bind" の後ろ

    usage = (
        "*使い方:*\n"
        "• `bind -d <path>` — チャンネルにプロジェクトルートを紐付け\n"
        "• `bind -p <PID>` — 実行中のclaude CLIプロセスをフォーク\n"
        "• `bind` — この表示＋バインド可能なプロセスリスト\n"
        "• `unbind` — 紐付けを解除"
    )

    # ── bind -d <path> ──
    if rest.startswith("-d ") or rest.startswith("-d\t"):
        dir_path = rest[3:].strip()
        if not dir_path:
            say(text=":warning: パスを指定してください: `bind -d <path>`", thread_ts=thread_ts)
            return
        expanded = os.path.abspath(os.path.expanduser(dir_path))
        if not os.path.isdir(expanded):
            say(text=f":warning: ディレクトリが見つかりません: `{dir_path}`", thread_ts=thread_ts)
            return
        runner.bind_project(channel_id, expanded)
        say(
            text=f":link: このチャンネルのプロジェクトルートを設定しました: `{expanded}`",
            thread_ts=thread_ts,
        )
        return

    # ── bind -p [PID] ──
    if rest == "-p" or rest.startswith("-p ") or rest.startswith("-p\t"):
        pid_str = rest[2:].strip()
        _handle_bind_process(pid_str, say, thread_ts, channel_id, user_id)
        return

    # ── bind（引数なし）→ usage + プロセスリスト（番号選択可） ──
    if not rest:
        _handle_bind_list(usage, say, thread_ts, channel_id, user_id)
        return

    # ── それ以外 → エラー + usage ──
    say(
        text=f":warning: 不明な引数: `{rest}`\n\n{usage}",
        thread_ts=thread_ts,
    )


def _handle_bind_process(pid_str: str, say, thread_ts, channel_id: str, user_id: str):
    """bind -p のPID指定 / PID省略時のプロセスリスト表示"""
    target_pid = None
    if pid_str:
        try:
            target_pid = int(pid_str)
        except ValueError:
            say(text=":warning: PIDは数字で指定してください: `bind -p <PID>`", thread_ts=thread_ts)
            return

    # 実行中のclaude CLIプロセスを検出
    instances = detect_running_claude_instances()
    if not instances:
        say(text=":mag: 実行中のclaude CLIインスタンスが見つかりません", thread_ts=thread_ts)
        return

    # 既に instance_threads で追跡中のPIDを除外
    tracked_pids = {data["pid"] for data in instance_threads.values()}
    candidates = [i for i in instances if i["pid"] not in tracked_pids]

    if not candidates:
        say(
            text=f":mag: フォーク可能なインスタンスがありません（{len(tracked_pids)}件追跡中）",
            thread_ts=thread_ts,
        )
        return

    # PID指定あり → 直接実行
    if target_pid is not None:
        matched = [i for i in candidates if i["pid"] == target_pid]
        if not matched:
            if target_pid in tracked_pids:
                say(text=f":warning: PID {target_pid} は既に追跡中です", thread_ts=thread_ts)
            else:
                say(text=f":warning: PID {target_pid} が見つかりません", thread_ts=thread_ts)
            return
        _execute_bind_fork(matched[0], channel_id, say, thread_ts, user_id)
        return

    # 1件 → 自動選択
    if len(candidates) == 1:
        _execute_bind_fork(candidates[0], channel_id, say, thread_ts, user_id)
        return

    # 複数件 → 番号リスト投稿 + pending_fork_selections に状態保存
    lines = [":computer: *フォーク可能なclaude CLIインスタンス:*"]
    for i, inst in enumerate(candidates, 1):
        lines.append(f"  `{i}` — PID {inst['pid']}  :file_folder: `{inst['cwd']}`  :clock1: {inst['etime']}")
    lines.append("\n番号を入力して選択、または `cancel` でキャンセル")
    say(text="\n".join(lines), thread_ts=thread_ts)

    pending_fork_selections[channel_id] = {
        "instances": candidates,
        "thread_ts": thread_ts,
        "user_id": user_id,
    }


def _handle_bind_list(usage: str, say, thread_ts, channel_id: str, user_id: str):
    """bind 引数なし: usage表示＋バインド可能なプロセスリスト"""
    # プロセスリストを取得
    instances = detect_running_claude_instances()
    tracked_pids = {data["pid"] for data in instance_threads.values()}
    candidates = [i for i in instances if i["pid"] not in tracked_pids] if instances else []

    lines = [usage, ""]
    if candidates:
        lines.append(":computer: *フォーク可能なclaude CLIインスタンス:*")
        for i, inst in enumerate(candidates, 1):
            lines.append(f"  `{i}` — PID {inst['pid']}  :file_folder: `{inst['cwd']}`  :clock1: {inst['etime']}")
        lines.append("\n番号を入力して選択、または `cancel` でキャンセル")

        pending_fork_selections[channel_id] = {
            "instances": candidates,
            "thread_ts": thread_ts,
            "user_id": user_id,
        }
    else:
        lines.append(":mag: フォーク可能なclaude CLIインスタンスはありません")

    say(text="\n".join(lines), thread_ts=thread_ts)


def _handle_unbind(say, thread_ts, channel_id: str):
    """チャンネルのプロジェクトルート紐付けを解除"""
    removed = runner.unbind_project(channel_id)
    if removed:
        say(
            text=f":broken_chain: プロジェクトルートの紐付けを解除しました（旧: `{removed}`）",
            thread_ts=thread_ts,
        )
    else:
        say(text=":warning: このチャンネルにはプロジェクトルートが設定されていません", thread_ts=thread_ts)


def _handle_in_dir(text: str, say, thread_ts, channel_id: str, user_id: str = "",
                   files: list[dict] | None = None):
    rest = text[3:].strip()
    parts = rest.split(maxsplit=1)
    if len(parts) < 2:
        say(text=":warning: 使い方: `in <path> タスク内容`\n相対パスはプロジェクトルート基準で解決されます", thread_ts=thread_ts)
        return

    dir_path, prompt = parts
    dir_path = os.path.expanduser(dir_path)

    # プロジェクトルートが必要（相対パス解決に使用）
    project = runner.get_project(channel_id)
    if not project:
        say(
            text=":warning: このチャンネルにはプロジェクトが設定されていません。\n`bind -d <path>` でプロジェクトルートを設定してください。",
            thread_ts=thread_ts,
        )
        return

    root = project.root_dir

    # 相対パスをプロジェクトルート基準で解決
    if not os.path.isabs(dir_path):
        dir_path = os.path.normpath(os.path.join(root, dir_path))
        # セキュリティ: 解決後パスがプロジェクトルート配下であることを検証
        if not dir_path.startswith(root):
            say(text=f":warning: プロジェクトルート外のパスは指定できません: `{dir_path}`", thread_ts=thread_ts)
            return

    if not os.path.isdir(dir_path):
        say(text=f":warning: ディレクトリが見つかりません: `{dir_path}`", thread_ts=thread_ts)
        return

    # 新セッション作成（working_dir オーバーライド付き）
    session = project.get_or_create_session(thread_ts)
    session.working_dir = dir_path

    # 添付画像のダウンロードとプロンプト拡張
    if files:
        image_paths = _download_slack_files(files, dir_path)
        if image_paths:
            prompt = _augment_prompt_with_images(prompt, image_paths)

    task = Task(
        id=0,
        prompt=prompt,
        allowed_tools=session.consume_tools(),
        user_id=user_id,
    )
    err = runner.run_task(project, session, task)
    if err:
        say(text=err, thread_ts=thread_ts)


def _handle_sessions(say, thread_ts, channel_id: str):
    """プロジェクトスコープのセッション一覧を表示"""
    project = runner.get_project(channel_id)
    if not project:
        say(text=":warning: このチャンネルにはプロジェクトが設定されていません", thread_ts=thread_ts)
        return

    if not project.sessions:
        say(text="セッション履歴はまだありません", thread_ts=thread_ts)
        return

    lines = [f":clipboard: *セッション一覧*  :file_folder: `{os.path.basename(project.root_dir)}`"]

    # 最新のセッション10件を表示
    sessions = sorted(
        project.sessions.values(),
        key=lambda s: s.created_at or datetime.min,
        reverse=True,
    )[:10]

    for session in sessions:
        active = session.active_task
        if active:
            status_emoji = ":gear:"
        elif session.latest_task:
            status_emoji = {
                TaskStatus.COMPLETED: ":white_check_mark:",
                TaskStatus.FAILED: ":x:",
                TaskStatus.CANCELLED: ":stop_sign:",
            }.get(session.latest_task.status, ":grey_question:")
        else:
            status_emoji = ":grey_question:"

        sid = session.claude_session_id[:16] + "..." if session.claude_session_id else "N/A"
        task_count = len(session.tasks)
        cwd = session.working_dir or project.root_dir
        dir_name = os.path.basename(cwd)
        latest = session.latest_task
        prompt_preview = latest.prompt[:40] + ("..." if latest and len(latest.prompt) > 40 else "") if latest else ""

        lines.append(
            f"{status_emoji} {session.label_emoji} `{sid}` ({task_count}タスク)"
            f" :file_folder:`{dir_name}` {prompt_preview}"
        )

    lines.append("\n_タスクのスレッドに返信すると自動で続行します_")
    say(text="\n".join(lines), thread_ts=thread_ts)


def _execute_bind_fork(inst: dict, channel_id: str, say, thread_ts, user_id: str):
    """フォーク実行: Project+Session作成、instance_threads登録、監視開始"""
    pid = inst["pid"]
    cwd = inst["cwd"]

    # 1. Project 作成/更新
    project = runner.bind_project(channel_id, cwd)

    # 2. Slack にフォーク開始メッセージ投稿 → fork_thread_ts 取得
    try:
        resp = say(
            text=(
                f":fork_and_knife: *bind -p* — PID {pid} をこのチャンネルにフォーク\n"
                f":file_folder: `{cwd}`\n"
                f":clock1: 経過: {inst.get('etime', '?')}\n"
                f"_このスレッドに返信するとclaude CLIに入力を送信します_"
            ),
            thread_ts=None,  # トップレベルメッセージとして投稿
        )
        fork_thread_ts = resp["ts"]
    except Exception as e:
        logger.error("フォーク開始メッセージの投稿に失敗: %s", e)
        say(text=f":x: フォーク開始メッセージの投稿に失敗しました: {e}", thread_ts=thread_ts)
        return

    # 3. Session 作成
    session = project.get_or_create_session(fork_thread_ts)

    # 4. JSONL があれば session_id を抽出
    jsonl_path = _find_session_jsonl(cwd)
    if jsonl_path:
        # task は無いので、session_id 抽出のみを行う簡易パス
        # _extract_session_info_from_jsonl は task が必須なのでダミー Task を使う
        dummy_task = Task(id=0, prompt="(fork)")
        _extract_session_info_from_jsonl(dummy_task, session, jsonl_path)

    # 5. instance_threads に登録（session 参照付き、task なし）
    thread_data = {
        "pid": pid,
        "tty": inst.get("tty", ""),
        "cwd": cwd,
        "session": session,
        # task なし = フォークインスタンス
    }

    if jsonl_path:
        # JSONL監視モード
        thread_data["jsonl_path"] = jsonl_path
        monitor_target = _monitor_session_jsonl
    else:
        # ターミナル監視にフォールバック
        tty_device = f"/dev/{inst.get('tty', '')}"
        initial_content = _read_terminal_contents(tty_device) or ""
        thread_data["passive_baseline_len"] = len(initial_content)
        thread_data["passive_baseline_marker"] = initial_content[-500:] if len(initial_content) >= 500 else initial_content
        monitor_target = _monitor_terminal_output

    instance_threads[fork_thread_ts] = thread_data

    # 6. 監視スレッド起動
    monitor = threading.Thread(
        target=monitor_target,
        args=(thread_data, fork_thread_ts, channel_id, slack_client),
        daemon=True,
    )
    monitor.start()

    # 7. 完了通知
    monitor_label = "JSONL" if jsonl_path else "Terminal"
    sid_info = f"\n_Session: `{session.claude_session_id[:12]}...`_" if session.claude_session_id else ""
    say(
        text=(
            f":white_check_mark: PID {pid} をフォークしました\n"
            f":link: プロジェクトルート: `{cwd}`\n"
            f":mag: 監視: {monitor_label}{sid_info}"
        ),
        thread_ts=thread_ts,
    )


def _handle_fork_selection(text: str, say, channel_id: str) -> bool:
    """pending_fork_selections の番号選択/キャンセルを処理。
    処理した場合 True、fallthrough の場合 False を返す。"""
    if channel_id not in pending_fork_selections:
        return False

    selection = pending_fork_selections[channel_id]
    instances = selection["instances"]
    thread_ts = selection["thread_ts"]
    user_id = selection["user_id"]
    input_text = text.strip().lower()

    # cancel
    if input_text == "cancel":
        del pending_fork_selections[channel_id]
        say(text=":x: フォーク選択をキャンセルしました", thread_ts=thread_ts)
        return True

    # 番号入力
    try:
        num = int(input_text)
    except ValueError:
        return False  # 数字でもcancelでもない → 通常のコマンド処理にfallthrough

    if num < 1 or num > len(instances):
        say(text=f":warning: 1〜{len(instances)} の番号を入力してください", thread_ts=thread_ts)
        return True

    selected = instances[num - 1]
    del pending_fork_selections[channel_id]

    # プロセス死亡チェック
    if not _is_process_alive(selected["pid"]):
        say(text=f":warning: PID {selected['pid']} は既に終了しています", thread_ts=thread_ts)
        return True

    _execute_bind_fork(selected, channel_id, say, thread_ts, user_id)
    return True


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

    # プロジェクト設定を読み込み
    runner.load_projects()
    if runner.projects:
        logger.info("チャンネルプロジェクト紐付け: %d件", len(runner.projects))

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
