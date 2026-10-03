# keirin-blog

keirin-platform の分析記事を、はてなブログで公開するためのリポジトリ（Private）。

## 書き方
1. ブランチを切って、`templates/article.md` を `articles/<slug>/index.md` にコピーし、本文を書く（画像は同じディレクトリに置く）
2. PR を作ると、はてなブログに**下書き**として送られる。プレビュー URL は `articles/<slug>/hatena.json` の `preview_url` に入る
3. 公開するときは front matter を `draft = false` にしてマージする。公開 URL は `hatena.json` の `url` に入る

- `hatena.json` はワークフローが管理する（手で編集しない）。ワークフローのコミットは GitHub API 経由で署名される
- 公開済みの記事の修正は、マージした時点で反映される（PR の段階では本番に反映しない）

## 初回のセットアップ（オーナーの作業）
1. はてなブログを作り、「設定 > 編集モード」を **Markdown モード**にする
2. リポジトリの Settings → Secrets and variables → Actions で、次を登録する:
   - Variables: `HATENA_ID`（ブログオーナーのはてな ID）、`HATENA_BLOG_DOMAIN`（例: `xxxx.hatenablog.com`。独自ドメインではなく、はてなのドメイン）
   - Secret: `HATENA_API_KEY`（https://blog.hatena.ne.jp/-/config の API キー）
3. 変数を登録するまでは、ワークフローは何もしない

公開ルールは `templates/article.md` と keirin-platform の CLAUDE.md を参照すること。仕組みの詳細は keirin-platform の `docs/blog.md`。
