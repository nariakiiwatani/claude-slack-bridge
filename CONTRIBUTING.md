# Contributing / コントリビューションガイド

Thank you for your interest in contributing to Claude Code ⇔ Slack Bridge!

Claude Code ⇔ Slack Bridge へのコントリビューションに興味を持っていただきありがとうございます！

## Bug Reports / バグ報告

Please open an [Issue](https://github.com/nariakiiwatani/claude-slack-bridge/issues) with the following information:

Issueを作成し、以下の情報を含めてください:

- Steps to reproduce / 再現手順
- Expected vs actual behavior / 期待される動作と実際の動作
- Python version, macOS version / Pythonバージョン、macOSバージョン
- Relevant logs (with secrets redacted) / 関連ログ（シークレットは除去）

## Pull Requests

1. Fork the repository / リポジトリをフォーク
2. Create a feature branch / フィーチャーブランチを作成:
   ```bash
   git checkout -b feature/your-feature
   ```
3. Make your changes / 変更を実施
4. Run tests / テストを実行:
   ```bash
   pytest
   ```
5. Open a Pull Request / プルリクエストを作成

## Development Setup / 開発環境セットアップ

```bash
git clone https://github.com/nariakiiwatani/claude-slack-bridge.git
cd claude-slack-bridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run tests / テスト実行
pytest
```

## Coding Guidelines / コーディング規約

- Code comments and Slack-facing messages should be in **Japanese** / コードのコメントやSlackメッセージは**日本語**で記述
- Documentation (README, guides) should be **bilingual** (English + Japanese) / ドキュメント（README、ガイド）は**日英バイリンガル**で記述
- All code is in a single file (`bridge.py`) / コードは単一ファイル（`bridge.py`）に集約
- Add tests for new features in `tests/` / 新機能には `tests/` にテストを追加
