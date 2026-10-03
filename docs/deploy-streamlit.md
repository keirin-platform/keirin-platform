# 出走表ビューアのデプロイ（Streamlit Community Cloud）

## 仕組み
- **Streamlit Community Cloud**（無料、カード登録は要らない）で、Private の**データリポジトリ（keirin-data）**からアプリを動かす。
  - エントリポイントは `streamlit_app.py`。中身は `keirin.viewer.main()` を呼ぶだけ。
  - ビューアのコードは、`requirements.txt` で keirin-platform を**コミット SHA で固定**して入れる。
- データ（`tables/`）は、データリポジトリのものをそのまま読む。データのコミット（毎日 03:30 JST の収集と backfill）があるたびに、アプリも自動で最新になる。
- **コードを変えたとき**は、データリポジトリの `requirements.txt` の SHA を更新する（PR で）。Community Cloud は requirements.txt が変わったときだけ依存を入れ直すため。
- 閲覧の制限は Community Cloud の Private アプリ機能でかける（許可したメールアドレスだけが見られる。Google ログインか、メールで届くリンクでログインする）。無料プランで Private アプリは1つまで。
- 12時間アクセスがないとスリープする。開くと起動ボタンが出る。
- 当日・翌日など未収集の日の出走表は、表示するときに keirin.jp から取得する（5分キャッシュ、1件ずつ、1秒以上の間隔）。
- Render 版（FastAPI）も作ったが、Render のカード認証が通らなかったので Streamlit に切り替えた（2026-10-02）。
- URL: <https://keirin.streamlit.app/>（Private。招待したメールアドレスでログインする）。
- デプロイのとき、Community Cloud がデータリポジトリに `.devcontainer/`（Codespaces 用）を、オーナーの名前で署名なしのコミットとして追加した。動作には関係ないが、Python を 3.12 に直した。

## Streamlit で使う GitHub アカウント
- Streamlit Community Cloud は OAuth アプリで、Private リポジトリには `repo` 権限を要求する。これは**ログインしたアカウントが触れるすべての Private リポジトリ**が対象で、リポジトリや組織の単位で絞れない（個人アカウントも必ず対象になる）。
- そのため、専用アカウント **`shimomobot`** でログインする。shimomobot は `keirin-data` **だけ**の外部コラボレーター（admin。デプロイと deploy key の登録に admin が必要）。
- オーナーの個人アカウントは、GitHub の Settings → Applications → Authorized OAuth Apps から Streamlit の許可を取り消す。

## 初回のセットアップ（オーナーの作業）
0. **組織で deploy key を有効にする**: <https://github.com/organizations/keirin-platform/settings/member_privileges> の **Deploy keys** を **Enabled** にして Save する。
   - Community Cloud は Private リポジトリを deploy key で clone する。新しい組織では deploy key が既定で無効なので、これをしないと
     `Failed to download the sources for repository` で起動しない（2026-10-03 に実際に起きた）。
   - 有効にしたあとは、アプリを **Manage app → ︙ → Reboot app** すると鍵が登録される。だめなら、アプリを削除して作り直す。
1. <https://share.streamlit.io> を開き、**Continue with GitHub** で **shimomobot** としてサインインする。
   - GitHub の認可画面で、**Private リポジトリへのアクセス**と、**Organization access の keirin-platform（Grant）**を許可する。
2. 右上の **Create app** → **Deploy a public app from GitHub**（「Yup, I have an app」）を選ぶ。
   - Repository: `keirin-platform/keirin-data`
   - Branch: `main`
   - Main file path: `streamlit_app.py`
   - App URL: 好きなサブドメイン（例: `keirin-<任意>`）
   - Advanced settings → Python version: **3.12**
   - **Deploy** を押す（初回は依存のインストールで数分かかる）。
3. アプリ右上の **Share**（または Settings → Sharing）で、**Only specific people can view this app** を選び、自分のメールアドレスを招待する。
   - **Public（This app is public and searchable）には絶対にしない**（データが誰でも見られて検索にも載る。私的使用の範囲を超える）。
   - Private リポジトリから動かすアプリは既定で Private になるが、念のため確認する。
4. アプリの URL を開き、招待したメールアドレス（Google ログインか、メールのリンク）でログインする。

## ローカルで動かす
```bash
uv sync --all-extras
KEIRIN_DATA_DIR=../keirin-data uv run streamlit run src/keirin/viewer.py
```
