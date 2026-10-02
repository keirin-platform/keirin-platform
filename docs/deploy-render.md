# 出走表ビューアのデプロイ（Render）

## 仕組み
- Render の Web サービスは、**Private のデータリポジトリ（keirin-data）** につなぐ。
  - Blueprint（`render.yaml`）もデータリポジトリにある（雛形は `infra/data-repo/render.yaml`）。
- ビルド時に、Public の keirin-platform を `.code/` に clone して依存を入れる。データ（`tables/`）は、Render が clone したデータリポジトリのものをそのまま使う。
- データリポジトリにコミットがあるたびに（毎日 03:30 JST の収集と backfill）、自動で再デプロイされる。秘密鍵やトークンは要らない。
- コードの変更は、次のデータのコミットか、Render の手動デプロイ（Manual Deploy）で反映される。
- Basic 認証で保護する（個人利用のため）。ユーザー名は `keirin`、パスワードは Render が生成する（`KEIRIN_WEB_PASSWORD`）。
- Free プランは、15分アクセスがないとスリープする。次のアクセスでは起動に1分ほどかかる。
- 当日・翌日など未収集の日の出走表は、表示するときに keirin.jp から取得する（5分キャッシュ、1件ずつ、1秒以上の間隔）。

## 初回のセットアップ（オーナーの作業）
1. <https://dashboard.render.com> に GitHub アカウントでサインアップする。
2. **New → Blueprint** を選び、GitHub の連携で `keirin-platform` 組織への Render のインストールを許可する。
   - Repository access は `keirin-data` だけを選べばよい。
3. リポジトリに `keirin-data` を選び、Blueprint 名を入れて **Apply** する（Free、Singapore）。
4. デプロイが終わったら、サービスの **Environment** で `KEIRIN_WEB_PASSWORD` の値を確認する。
5. サービスの URL（`https://keirin-viewer-xxxx.onrender.com`）を開き、`keirin` とそのパスワードでログインする。

## ローカルで動かす
```bash
uv sync --all-extras
KEIRIN_WEB_AUTH=off KEIRIN_DATA_DIR=../keirin-data \
  uv run uvicorn --factory keirin.web.app:create_app_from_env --reload
# → http://127.0.0.1:8000
```

## 画面
- `/d/<日付>`: その日の開催一覧
- `/d/<日付>/<場コード>`: レース一覧
- `/d/<日付>/<場コード>/<R>`: 出走表（競走得点、補正得点、補正量、得点順位の変化、他級走/走数、勝率など。収集済みの日は着順も出る）
