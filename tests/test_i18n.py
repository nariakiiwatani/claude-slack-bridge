"""i18n モジュールのテスト。

対象: t(), MESSAGES 辞書の網羅性、format 変数の展開。
"""

import os
from unittest.mock import patch

import i18n


class TestTranslationFunction:
    """t() 関数の基本動作テスト"""

    def test_returns_ja_by_default(self):
        """デフォルト言語(ja)でキーが解決されること"""
        msg = i18n.t("status_no_tasks")
        assert "タスク" in msg or "zzz" in msg

    def test_unknown_key_returns_key(self):
        """未知のキーはキーそのままを返すこと"""
        result = i18n.t("nonexistent_key_12345")
        assert result == "nonexistent_key_12345"

    def test_format_variables(self):
        """format 変数が正しく埋まること"""
        msg = i18n.t("status_thinking", chars=42)
        assert "42" in msg

    def test_format_with_float(self):
        """浮動小数点の format が動作すること"""
        msg = i18n.t("task_complete", elapsed=123.456)
        assert "123" in msg

    def test_format_multiple_vars(self):
        """複数の format 変数が埋まること"""
        msg = i18n.t("status_elapsed_tools", elapsed=10.0, tool_count=3)
        assert "10" in msg
        assert "3" in msg

    def test_no_kwargs_returns_raw(self):
        """kwargs なしの場合、テンプレートをそのまま返すこと（format呼ばない）"""
        msg = i18n.t("help_text")
        assert len(msg) > 0


class TestLanguageSwitching:
    """SLACK_LANGUAGE による言語切り替えテスト"""

    def test_ja_messages(self):
        """ja 言語で日本語メッセージが返ること"""
        with patch.dict(os.environ, {"SLACK_LANGUAGE": "ja"}):
            msg = i18n.t("status_no_tasks")
            assert "タスク" in msg

    def test_en_messages(self):
        """en 言語で英語メッセージが返ること"""
        with patch.dict(os.environ, {"SLACK_LANGUAGE": "en"}):
            msg = i18n.t("status_no_tasks")
            assert "No tasks" in msg

    def test_unknown_lang_falls_back_to_ja(self):
        """未知の言語は ja にフォールバックすること"""
        with patch.dict(os.environ, {"SLACK_LANGUAGE": "fr"}):
            msg = i18n.t("status_no_tasks")
            # ja のメッセージが返ること
            assert msg == i18n.MESSAGES["ja"]["status_no_tasks"]


class TestKeyCompleteness:
    """ja/en 両方で全キーが存在すること（キーの網羅性チェック）"""

    def test_en_has_all_ja_keys(self):
        """en に ja の全キーが存在すること"""
        ja_keys = set(i18n.MESSAGES["ja"].keys())
        en_keys = set(i18n.MESSAGES["en"].keys())
        missing = ja_keys - en_keys
        assert not missing, f"en に不足しているキー: {missing}"

    def test_ja_has_all_en_keys(self):
        """ja に en の全キーが存在すること"""
        ja_keys = set(i18n.MESSAGES["ja"].keys())
        en_keys = set(i18n.MESSAGES["en"].keys())
        missing = en_keys - ja_keys
        assert not missing, f"ja に不足しているキー: {missing}"

    def test_no_empty_values(self):
        """全メッセージが空文字列でないこと"""
        for lang in ("ja", "en"):
            for key, value in i18n.MESSAGES[lang].items():
                assert value, f"{lang}.{key} が空です"


class TestFormatVariables:
    """format 変数を使うキーが両言語で同じ変数を受け付けること"""

    def _get_format_keys(self, template: str) -> set[str]:
        """テンプレート文字列から {key} や {key:.0f} 形式の変数名を抽出"""
        import re
        return set(re.findall(r'\{(\w+)(?::[^}]*)?\}', template))

    def test_format_vars_match_between_languages(self):
        """ja/en で同じキーの format 変数が一致すること"""
        for key in i18n.MESSAGES["ja"]:
            ja_vars = self._get_format_keys(i18n.MESSAGES["ja"][key])
            en_vars = self._get_format_keys(i18n.MESSAGES["en"][key])
            assert ja_vars == en_vars, (
                f"キー '{key}' の format 変数が不一致: "
                f"ja={ja_vars}, en={en_vars}"
            )

    def test_all_format_keys_can_be_filled(self):
        """format 変数を持つ全キーが実際に展開できること"""
        import re
        test_values = {
            "chars": 100,
            "elapsed": 10.0,
            "tool_count": 5,
            "tools": "Read, Write",
            "count": 3,
            "code": 1,
            "error": "test error",
            "task_id": 42,
            "pid": 12345,
            "path": "/tmp/test",
            "max": 10,
            "old": "/old/path",
            "cwd": "/home/user",
            "sid_info": "\nSession: abc...",
            "label": "Option A",
            "num": 1,
            "question": "Which?",
        }

        for lang in ("ja", "en"):
            for key, template in i18n.MESSAGES[lang].items():
                vars_needed = self._get_format_keys(template)
                if not vars_needed:
                    continue
                kwargs = {v: test_values.get(v, "test") for v in vars_needed}
                try:
                    result = template.format(**kwargs)
                    assert isinstance(result, str)
                except (KeyError, ValueError) as e:
                    raise AssertionError(
                        f"{lang}.{key} の format に失敗: {e}"
                    )
