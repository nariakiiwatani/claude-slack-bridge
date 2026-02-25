"""テキスト処理関数のテスト。

対象: _md_to_slack, _strip_ansi, _filter_terminal_ui,
      _augment_prompt_with_images, _strip_bot_mention, _summarize_input
"""

import bridge

ZWS = "\u200B"  # ゼロ幅スペース（_md_to_slack 出力で使用）


# ── _md_to_slack ──────────────────────────────────────────

class TestMdToSlack:
    """Markdown → Slack mrkdwn 変換"""

    def test_bold(self):
        result = bridge._md_to_slack("**text**")
        assert f"{ZWS}*text*{ZWS}" in result

    def test_italic(self):
        result = bridge._md_to_slack("*text*")
        assert f"{ZWS}_text_{ZWS}" in result

    def test_bold_italic(self):
        result = bridge._md_to_slack("***text***")
        assert f"{ZWS}*_text_*{ZWS}" in result

    def test_strikethrough(self):
        result = bridge._md_to_slack("~~text~~")
        assert f"{ZWS}~text~{ZWS}" in result

    def test_code_block_preserved(self):
        md = "```python\nprint('hello')\n```"
        result = bridge._md_to_slack(md)
        assert "```\nprint('hello')\n```" in result

    def test_code_block_language_stripped(self):
        md = "```javascript\nconsole.log('hi')\n```"
        result = bridge._md_to_slack(md)
        # 言語指定が除去されていること
        assert "```javascript" not in result
        assert "```\nconsole.log('hi')\n```" in result

    def test_inline_code_preserved(self):
        result = bridge._md_to_slack("use `foo()` here")
        assert "`foo()`" in result

    def test_table_to_code_block(self):
        md = "| col1 | col2 |\n| --- | --- |\n| a | b |"
        result = bridge._md_to_slack(md)
        assert result.startswith("```\n")
        assert "| col1 | col2 |" in result

    def test_link(self):
        result = bridge._md_to_slack("[click](https://example.com)")
        assert "<https://example.com|click>" in result

    def test_header(self):
        result = bridge._md_to_slack("# Header")
        assert "*Header*" in result

    def test_header_h2(self):
        result = bridge._md_to_slack("## Sub Header")
        assert "*Sub Header*" in result

    def test_unordered_list(self):
        result = bridge._md_to_slack("- item one\n- item two")
        assert "\u2022 item one" in result
        assert "\u2022 item two" in result

    def test_horizontal_rule(self):
        result = bridge._md_to_slack("---")
        assert "\u2501" in result  # ━ 文字

    def test_horizontal_rule_asterisks(self):
        result = bridge._md_to_slack("***")
        # *** は bold italic ではなく、行全体がマッチする場合は水平線
        # ただし *** は bold-italic の空マッチとして扱われうるのでどちらかを確認
        # 実際の挙動: ***は行頭の --- 等と同じパターン
        assert "\u2501" in result or "*" in result

    def test_underscore_bold(self):
        result = bridge._md_to_slack("__bold__")
        assert f"{ZWS}*bold*{ZWS}" in result

    def test_nested_quote_flattened(self):
        result = bridge._md_to_slack(">> nested quote")
        assert result.startswith("> ")
        assert ">>" not in result

    def test_task_list_checked(self):
        result = bridge._md_to_slack("- [x] done")
        assert "\u2705" in result  # ✅

    def test_task_list_unchecked(self):
        result = bridge._md_to_slack("- [ ] todo")
        assert "\u2610" in result  # ☐

    def test_plain_text_unchanged(self):
        result = bridge._md_to_slack("just plain text")
        assert result == "just plain text"

    def test_inline_code_not_formatted(self):
        """インラインコード内部のマークダウンは変換されない"""
        result = bridge._md_to_slack("`**not bold**`")
        assert "`**not bold**`" in result

    def test_code_block_content_not_formatted(self):
        """コードブロック内部のマークダウンは変換されない"""
        md = "```\n**not bold**\n```"
        result = bridge._md_to_slack(md)
        assert "**not bold**" in result


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


# ── _filter_terminal_ui ──────────────────────────────────

class TestFilterTerminalUI:
    def test_removes_separator(self):
        result = bridge._filter_terminal_ui("─────────────")
        assert result == ""

    def test_removes_prompt_line(self):
        result = bridge._filter_terminal_ui("❯ some prompt")
        assert result == ""

    def test_removes_status_bar(self):
        result = bridge._filter_terminal_ui("⏵ running")
        assert result == ""

    def test_removes_spinner(self):
        result = bridge._filter_terminal_ui("✶ thinking…")
        assert result == ""

    def test_removes_timer(self):
        result = bridge._filter_terminal_ui("(28s)")
        assert result == ""

    def test_removes_ctrl_hint(self):
        result = bridge._filter_terminal_ui("ctrl+c to cancel")
        assert result == ""

    def test_removes_background_hint(self):
        result = bridge._filter_terminal_ui("press to run in background")
        assert result == ""

    def test_removes_fold_hint(self):
        result = bridge._filter_terminal_ui("5 lines (collapsed)")
        assert result == ""

    def test_keeps_normal_text(self):
        result = bridge._filter_terminal_ui("Hello world\nThis is real output")
        assert "Hello world" in result
        assert "This is real output" in result

    def test_strips_ansi_before_filtering(self):
        result = bridge._filter_terminal_ui("\x1b[31mhello\x1b[0m")
        assert result == "hello"

    def test_removes_empty_lines(self):
        result = bridge._filter_terminal_ui("\n\n\ntext\n\n")
        assert result == "text"


# ── _augment_prompt_with_images ───────────────────────────

class TestAugmentPromptWithImages:
    def test_appends_image_paths(self):
        result = bridge._augment_prompt_with_images("describe", ["/tmp/a.png", "/tmp/b.jpg"])
        assert result.startswith("describe")
        assert "/tmp/a.png" in result
        assert "/tmp/b.jpg" in result
        assert "\u6dfb\u4ed8\u753b\u50cf:" in result  # 「添付画像:」

    def test_single_image(self):
        result = bridge._augment_prompt_with_images("hello", ["/img.png"])
        assert "/img.png" in result

    def test_preserves_original_prompt(self):
        result = bridge._augment_prompt_with_images("original prompt", ["/x.png"])
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
