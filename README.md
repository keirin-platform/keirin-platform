# keirin-platform

個人利用向けの競輪データシステム。

- データソース: [KEIRIN.JP](https://keirin.jp/) の内部 JSON API（[調査メモ](docs/keirin-jp-api.md)）
- 収集したデータは私的利用の範囲にとどめるため、Private リポジトリに保存する（このリポジトリにはコードだけを置く）
- データスキーマ: [docs/data-schema.md](docs/data-schema.md)
- 昇降級を補正した競走得点: [docs/score-correction.md](docs/score-correction.md)
- 予想（各選手の1着・3着以内の確率）: [docs/prediction.md](docs/prediction.md)
- 出走表ビューア（Streamlit Community Cloud）: [docs/deploy-streamlit.md](docs/deploy-streamlit.md)

```bash
uv sync --all-extras
uv run keirin collect --data-dir ../keirin-data   # 昨日（JST）分を収集
uv run pytest -q
```

開発ルールや設計の経緯は [CLAUDE.md](CLAUDE.md) を参照。
