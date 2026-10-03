# CLAUDE.md (keirin-blog)

このリポジトリのルールと設計は、keirin-platform の CLAUDE.md（https://github.com/keirin-platform/keirin-platform/blob/main/CLAUDE.md）に従う。
要点:
- PR 形式。コミットは GPG 署名つきで、メッセージは Conventional Commits 形式の英語。CI が通ったら Claude がマージする
- 記事の公開ルール: 集計した統計・グラフ・考察だけを載せる（収集データそのものは載せない）、出典「KEIRIN.JP」を明記する、収益化しない、ビューアや Private リポジトリにリンクしない、内部 API の使い方は書かない
- 同期の仕組みは keirin-platform の `docs/blog.md` と、再利用可能ワークフロー `blog-sync.yml`
