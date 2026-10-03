# 記事の公開（はてなブログ）

- 記事は Private リポジトリ **keirin-platform/keirin-blog** で書き、はてなブログに公開する（2026-10-03 決定）。
- はてな公式の hatenablog-workflows は使わず、**自前の同期**（`src/keirin/hatena.py`、再利用可能ワークフロー `.github/workflows/blog-sync.yml`）にした。
  - 公式は自動コミット（画像のアップロード、下書きの作成、記事ファイルの移動）に GPG 署名が付かず、署名必須のルールに合わない。
  - 公式は自動マージ前提だが、無料プランの組織では Private リポジトリにブランチ保護を設定できず、自動マージが動かない。
  - 自前の同期は、ワークフローが状態ファイルを GitHub API（createCommitOnBranch）でコミットするので、すべて Verified になる。自動マージも要らない。

## リポジトリの構成（keirin-blog、雛形は `infra/blog-repo/`）
```
articles/<slug>/index.md      TOML front matter（title, categories, draft）＋ Markdown 本文
articles/<slug>/*.png         画像（![説明](chart.png) で参照する）
articles/<slug>/hatena.json   ワークフローが管理する状態（edit_url, preview_url, url, published, published_at, images）
templates/article.md          記事の雛形（公開ルールつき）
.github/workflows/sync.yml    blog-sync.yml を呼ぶ caller
```
- `_` や `.` で始まるディレクトリは同期しない。

## 流れ
| イベント | mode | 動き |
|---|---|---|
| PR（articles/ に変更があるとき） | preview | **まだ公開していない**記事を、はてなブログに**下書き**（プレビュー URL つき）として送る。公開済みの記事には触らない（マージ前の修正を本番に出さないため） |
| main へのマージ | publish | front matter の `draft` に従う。`false` なら公開する（公開済みなら更新する） |
| 手動実行（workflow_dispatch） | publish | すべての記事を送り直す |

- 変更された記事だけを送る（PR は base との差分、main は push 前との差分）。
- 画像は Hatena Fotolife にアップロードし、送る本文の中だけ Fotolife 記法（`[f:id:...:plain:alt=...]`）に置き換える。リポジトリの Markdown は書き換えない。内容のハッシュで、同じ画像は再アップロードしない。
- 公開日時は初めて公開したときの時刻を `published_at` に記録して、更新しても維持する。
- API:
  - ブログは AtomPub（Basic 認証。はてな ID と API キー）。下書きのときは `app:preview=yes` でプレビュー URL を発行する。
  - Fotolife は Atom（WSSE 認証）。

## セットアップ（ブログを作ったら）
1. はてなブログを作り、編集モードを Markdown にする。
2. keirin-blog の Actions に、Variables の `HATENA_ID`、`HATENA_BLOG_DOMAIN` と、Secret の `HATENA_API_KEY` を登録する（変数がないうちはワークフローは何もしない）。
3. テスト用の記事で、PR（下書き）→ マージ（`draft = true` のままなら下書きのまま）を確認する。

## 公開ルール
CLAUDE.md の「オーナーの要望 2.」を参照（集計値だけ、出典を明記、収益化しない、ビューアにリンクしない、内部 API の使い方は書かない）。
