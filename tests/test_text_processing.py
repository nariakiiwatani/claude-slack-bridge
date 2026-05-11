"""テキスト処理関数のテスト。

対象: _strip_ansi, _augment_prompt_with_files,
      _strip_bot_mention, _summarize_input
"""

import bridge


# ── _strip_ansi ───────────────────────────────────────────

class TestStripAnsi:
    def test_removes_color_codes(self):
        result = bridge._strip_ansi("\x1b[31mred text\x1b[0m")
        assert result == "red text"

    def test_removes_osc_sequences(self):
        result = bridge._strip_ansi("\x1b]0;title\x07some text")
        assert result == "some text"

    def test_removes_charset_designators(self):
        result = bridge._strip_ansi("\x1b(Bhello")
        assert result == "hello"

    def test_plain_text_unchanged(self):
        result = bridge._strip_ansi("no ansi here")
        assert result == "no ansi here"

    def test_multiple_sequences(self):
        result = bridge._strip_ansi("\x1b[1m\x1b[34mbold blue\x1b[0m")
        assert result == "bold blue"

    def test_removes_private_mode_sequences(self):
        """?付きのプライベートモードシーケンス（カーソル表示/非表示等）"""
        result = bridge._strip_ansi("\x1b[?25hvisible\x1b[?25l")
        assert result == "visible"

    def test_removes_focus_tracking(self):
        """フォーカストラッキング制御（\x1b[?1004l）"""
        result = bridge._strip_ansi("text\x1b[?1004l\x1b[?2004l")
        assert result == "text"

    def test_removes_bracketed_paste(self):
        """ブラケットペーストモード（~終端）"""
        result = bridge._strip_ansi("\x1b[200~pasted text\x1b[201~")
        assert result == "pasted text"

    def test_removes_sgr_mouse_mode(self):
        """SGRマウスモード（<付きシーケンス）"""
        result = bridge._strip_ansi("\x1b[<u\x1b[?1004ltext")
        assert result == "text"

    def test_removes_combined_terminal_control(self):
        """実際のターミナル出力末尾に現れる制御シーケンスの組み合わせ"""
        result = bridge._strip_ansi("output\x1b[<u\x1b[?1004l\x1b[?2004l\x1b[?25h\x1b[?25h")
        assert result == "output"

    def test_removes_orphan_esc(self):
        """未知のエスケープシーケンスの残留ESC文字"""
        result = bridge._strip_ansi("text\x1bremainder")
        assert result == "textremainder"


# ── _augment_prompt_with_files ────────────────────────────

class TestAugmentPromptWithFiles:
    def test_appends_file_paths(self):
        result = bridge._augment_prompt_with_files("describe", ["/tmp/a.png", "/tmp/b.jpg"])
        assert result.startswith("describe")
        assert "/tmp/a.png" in result
        assert "/tmp/b.jpg" in result
        assert "添付ファイル:" in result  # 「添付ファイル:」

    def test_single_file(self):
        result = bridge._augment_prompt_with_files("hello", ["/img.png"])
        assert "/img.png" in result

    def test_non_image_file(self):
        result = bridge._augment_prompt_with_files("read this", ["/tmp/spec.md"])
        assert "/tmp/spec.md" in result

    def test_preserves_original_prompt(self):
        result = bridge._augment_prompt_with_files("original prompt", ["/x.png"])
        assert result.startswith("original prompt")


# ── _strip_bot_mention ────────────────────────────────────

class TestStripBotMention:
    def test_removes_mention(self):
        result = bridge._strip_bot_mention("<@UBOTTEST> hello world")
        assert result == "hello world"

    def test_no_mention(self):
        result = bridge._strip_bot_mention("just text")
        assert result == "just text"

    def test_mention_with_extra_spaces(self):
        result = bridge._strip_bot_mention("<@UBOTTEST>   hello")
        assert result == "hello"

    def test_mention_only(self):
        result = bridge._strip_bot_mention("<@UBOTTEST>")
        assert result == ""

    def test_other_mention_preserved(self):
        result = bridge._strip_bot_mention("<@UOTHER> hello")
        assert "<@UOTHER>" in result


# ── _summarize_input ──────────────────────────────────────

class TestSummarizeInput:
    def test_file_path(self):
        result = bridge._summarize_input({"file_path": "/tmp/foo.py"})
        assert result == "/tmp/foo.py"

    def test_command(self):
        result = bridge._summarize_input({"command": "ls -la"})
        assert result == "ls -la"

    def test_command_truncated(self):
        long_cmd = "x" * 100
        result = bridge._summarize_input({"command": long_cmd})
        assert len(result) <= 83  # 80 + "..."
        assert result.endswith("...")

    def test_pattern(self):
        result = bridge._summarize_input({"pattern": "*.py"})
        assert result == "*.py"

    def test_query(self):
        result = bridge._summarize_input({"query": "search term"})
        assert result == "search term"

    def test_query_truncated(self):
        long_q = "q" * 100
        result = bridge._summarize_input({"query": long_q})
        assert result.endswith("...")

    def test_url(self):
        result = bridge._summarize_input({"url": "https://example.com/page"})
        assert result == "https://example.com/page"

    def test_notebook_path(self):
        result = bridge._summarize_input({"notebook_path": "/nb.ipynb"})
        assert result == "/nb.ipynb"

    def test_empty_dict(self):
        result = bridge._summarize_input({})
        assert result == ""

    def test_fallback(self):
        result = bridge._summarize_input({"unknown_key": "value"})
        assert "unknown_key" in result

    def test_priority_file_path_over_command(self):
        """file_path が command より優先される"""
        result = bridge._summarize_input({"file_path": "/a.py", "command": "ls"})
        assert result == "/a.py"
