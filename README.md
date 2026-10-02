# keirin-platform

個人利用向けの競輪データシステム。

- データソース: [KEIRIN.JP](https://keirin.jp/) の内部 JSON API（[調査メモ](docs/keirin-jp-api.md)）
- 収集したデータは私的利用の範囲にとどめるため、Private リポジトリに保存する（このリポジトリにはコードだけを置く）
- データスキーマ: [docs/data-schema.md](docs/data-schema.md)

```bash
uv sync
uv run keirin collect --data-dir ../keirin-data   # 昨日（JST）分を収集
uv run pytest -q
```

開発ルールや設計の経緯は [CLAUDE.md](CLAUDE.md) を参照。
