# CLAUDE.md

個人利用向けの競輪データシステム。Vibe Coding で開発していて、設計と実装は Claude に任されている。
オーナーは欲しい機能だけを伝える。このファイルには、決めたことと経緯をすべて記録する。

## 開発ルール（オーナー指定）

- **言語**: PHP 以外なら何でもよい → **Python** を採用（理由は「決定ログ」を参照）。
- **PR 形式**で進める。`main` へ直接コミットしない（初期コミットだけは例外）。ブランチ名は `feat/...`、`fix/...`、`docs/...`、`chore/...`。
- **GPG 署名は必須**。`git commit -S`（`commit.gpgsign=true` が設定済み）。
  - コミット前にキャッシュを確認する:
    `echo test | gpg --batch --pinentry-mode error --local-user "$(git config user.signingkey)" --clearsign >/dev/null`
  - 失敗したら（キャッシュ切れ）、**オーナーにパスフレーズの入力を依頼する**（例: `! echo test | gpg --clearsign >/dev/null` を実行してもらう）。
  - それ以外は、確認を取らずにコミットと push まで進めてよい。
- **コミットメッセージ**: Conventional Commits 形式の英語（例: `feat(collector): add race result parser`）。
- 有料化や広告掲載はしない（その点の規約確認は不要）。
- **PR のマージは Claude が行う**: CI が通ったら `gh pr merge <n> --merge --delete-branch --subject "<PRタイトル> (#<n>)"` でマージする。
  - merge commit 方式にして、オーナーの鍵で署名したコミットを main の履歴にそのまま残す（merge commit 自体は GitHub が署名する）。PR タイトルも Conventional Commits 形式で書く。
- 決めたこと、調査結果、仕様の変更はこのファイルに追記する。
- **ワークフローを変えたら、手動実行（workflow_dispatch）で起動まで確認する**。別リポジトリの再利用可能ワークフローを呼ぶ設定の誤り（例: `secrets:` を `with:` の途中に入れた）は、actionlint でも検出できず、`startup_failure` になる（2026-10-05 に実際に起きた）。
- **組織（keirin-platform）のリポジトリ名には `keirin-` を付ける**（オーナー指定、2026-10-03）。例: keirin-platform、keirin-data、keirin-blog。
  - 例外: `.github`（組織のプロフィール `profile/README.md` 用。GitHub が決めた名前）。

## オーナーの要望（機能）

1. **昇級・降級を補正した競走得点を、出走表で確認したい**（最大の目的、2026-10-02）。
   仕様と調査結果は [docs/score-correction.md](docs/score-correction.md) にある。

2. **データが溜まったら、競走得点の分析記事を書く**（2026-10-03）。分析は Claude が手伝う。
   - 記事は公開されるので、次のルールを守る（サイトポリシー: 複製は私的使用と引用まで、私的利用を超えて金銭的対価を得ることは禁止）:
     - 自分で集計した統計、グラフ、考察を載せる。**収集データそのもの（出走表、成績の一覧、CSV）は載せない**。
     - 個別の選手やレースは必要最小限の例にとどめ、出典「KEIRIN.JP」を明記する（引用の範囲）。
     - **収益化しない**（有料記事、広告収入、アフィリエイトは不可）。
     - ビューアや Private リポジトリにはリンクしない。内部 API の使い方を詳しく書かない（robots.txt で許可されていないため）。
   - **公開先ははてなブログに決定**（2026-10-03 選定、まだセットアップしていない）。
     - 最初は Zenn を選んだが、オーナーの指摘で変更した:
       - Zenn は読者がエンジニア中心なので、記事が分析手法寄りになる。
       - オーナーの既存の Zenn アカウント（GitHub 連携済み）に競輪の記事が混ざる。
     - はてなブログを選んだ理由:
       - 読者層が広く、競輪ファンのブログも多い。競輪の中身を主役にできる。
       - 公式の GitHub 連携 [hatena/hatenablog-workflows](https://github.com/hatena/hatenablog-workflows) で、下書きの作成と同期、main へのマージで公開、リポジトリの画像の自動アップロード（upload-images）ができる。PR で進める流れとも合う。
     - 収益化の回避:
       - 無料プランの広告収入ははてな側に入り、著者には入らない。
       - アフィリエイト（Amazon など）、有料記事、Pro の広告は使わない。
     - 予定の構成:
       - Private リポジトリ **`keirin-platform/keirin-blog`**（2026-10-03 作成）。
       - 同期は**自前**（`hatena.py`、`blog-sync.yml`）。仕組みは [docs/blog.md](docs/blog.md)。
       - PR で下書き（プレビュー URL）、`draft = false` でマージすると公開。
       - グラフは分析スクリプトで生成する（集計値だけ）。
     - 公式の hatenablog-workflows は使わない（オーナー判断）。自動コミットに署名が付かないうえ、Private リポジトリでは自動マージが動かないため。
     - ブログ本体はオーナーがこれから作る。作ったら、keirin-blog に Variables の `HATENA_ID`、`HATENA_BLOG_DOMAIN` と Secret の `HATENA_API_KEY` を登録してもらう。
     - 必要なら、オーナーの既存の Zenn アカウントで、分析手法の技術記事を別に書く（内部 API の詳細は書かない）。
   - 分析のテーマ案:
     - 級班別の得点の分布、得点と着順の関係
     - 昇降級組の表示得点のずれと、Δ の推定（2026-01 と 2026-07 の2回の期替わり）
     - 補正による予測精度の改善
     - 同じ級班の中でのグレードによる偏り
     - 2027-01〜03 の実地検証

3. **戦った相手の強さを考慮した評価**（2026-10-03）。オーナーの判断で**レーティング方式**（Plackett–Luce）で進める。
   詳しくは [docs/rating.md](docs/rating.md)。精度が公式の得点を上回ったら、ビューアに列を足す。

4. **競輪の通説・セオリーの調査**（2026-10-03）→ [docs/keirin-theories.md](docs/keirin-theories.md)。根拠の強さ、私たちのデータでの簡易集計、検証の優先順位をまとめた。記事の分析テーマの元ネタ。

5. **GitHub 上の競輪関連リポジトリの調査**（2026-10-04）→ [docs/github-keirin-repos.md](docs/github-keirin-repos.md)。
   私たちに最も近いのは Tower2007/keirin-ai（keirin.jp から蓄積、LightGBM、shadow 評価）。どのリポジトリもライセンスがないので、コードは流用しない。

6. **選手の特徴（クセ）を見つける分析**（2026-10-05）。例: 決勝でもないのに番手捲りをよくする選手、先行ラインの番手で、ラインが捲られたときに共倒れしやすい選手。
   並びと結果（決まり手、B・H）から検出して、経験ベイズで少ない件数を補正する。計画は [docs/rider-traits.md](docs/rider-traits.md)。

7. **注目選手の出走通知**（2026-10-05）。特徴的な選手が条件（番手など）に合うレースに出るとき、Discord に通知する。
   監視リストはデータリポジトリの `watchlist.toml`、Webhook は Secret `DISCORD_WEBHOOK_URL`。詳しくは [docs/notifications.md](docs/notifications.md)。

8. **予想: 各選手の1着・3着以内の確率**（2026-10-05）。**車券や回収率ではなく、確率で**出す（オーナー指定）。
   当夜の武雄・小松島のミッドナイトで試作して追いかけたあと、ビューアの「予想」タブとして組み込んだ。
   モデル（Plackett–Luce、出走表の成績と並び）と評価は [docs/prediction.md](docs/prediction.md)。

## 構成

```
keirin-platform/keirin-platform (Public)   ← このリポジトリ。コードだけを置く
  └─ .github/workflows/collect.yml          再利用可能ワークフロー（収集ロジック本体）
keirin-platform/keirin-data (Private)      ← 収集データの置き場
keirin-platform/keirin-blog (Private)      ← はてなブログの記事（blog-sync.yml を呼ぶ caller。docs/blog.md）
keirin-platform/.github (Public)           ← 組織のプロフィール（profile/README.md）。公開してよい内容だけを書く
                                             （Private リポジトリ、ビューアの URL、内部 API の詳細には触れない）
  └─ .github/workflows/collect.yml          上を呼ぶだけの薄い caller（cron は毎日 03:30 JST）
                                             データのリポジトリもマージは Claude が行う
Streamlit Community Cloud (無料)           ← 出走表ビューア。データリポジトリの streamlit_app.py を動かす
                                             ビューアのコードは requirements.txt で SHA を固定して入れる
                                             （データのコミットごとに最新になる。詳しくは docs/deploy-streamlit.md）
```

- **データは Public リポジトリに置かない**。KEIRIN.JP のサイトポリシーで、複製は「私的使用」の範囲までしか認められていないため。
  - `.gitignore` で `data/`、`raw/`、`tables/`、`*.json.gz` を除外している。
  - テストのフィクスチャは**架空のデータ**で作る（実データをコピーしない）。
  - 収集ジョブは Private 側で動かす。Public リポジトリの Actions ログは誰でも見られるため。
- 収集は、データリポジトリの cron から、このリポジトリの再利用可能ワークフローを呼んで動かす。
  - 毎回「`since`〜昨日」のうち未取得の日を、新しい順に最大15日取る（`--max-days 15`。2026-10-04 に10日から変更）。取りこぼした日も次の回に自動で埋まる。
    - データリポジトリの caller では `since` を **2025-12-01 に固定**している（記事の分析で 2026-01 と 2026-07 の期替わりを両方入れるため）。
    - `since` を空にすると「4ヶ月前の月初〜」になる（再利用可能ワークフローの既定）。
  - backfill の見込み: 2026-10-04 時点で残り274日。1回あたり14日ずつ（15日のうち1日は昨日分）進むので、**10/24 ごろに完了**。そのあとは毎日昨日分の1日だけになる。
  - 古いデータは消さずに残る（蓄積する）。
  - データのコミットは GraphQL `createCommitOnBranch` に GITHUB_TOKEN を使って作る。GitHub が署名するので Verified になる（CI に GPG 鍵は置かない）。
    コミットの直前にブランチの先頭を読み直して、その上に積む。そのため、収集中にデータリポジトリへ PR をマージしても大丈夫。
  - データリポジトリ側のファイルの雛形は `infra/data-repo/` にある。変更したらデータリポジトリにも反映する。
- 実測（2026-10-01 分）: 9開催・82レースで 192 リクエスト、約3分。JSJ002 に切り替えてからは約120リクエスト。サイズは raw 190KB + CSV 100KB/日（年に約 105MB）。リポジトリが大きくなりすぎたら、raw の扱い（別ブランチや Release への退避）を見直す。
- Actions の minutes: Private（Free プラン）は月 2,000 分。毎日の収集は1回あたり約3分、backfill 中は1回あたり最大で約35分。
  10/24 ごろからは、オッズパークの並びの backfill（1回50分まで）が加わり、月に約1,700分になる見込み。上限に近いので、ほかに Actions を増やすときは注意する。
  これより大きい backfill はローカルで `keirin collect` を実行して push する。

## データソース

- KEIRIN.JP の内部 JSON API（`https://keirin.jp/pc/json?type=...`）。詳細は [docs/keirin-jp-api.md](docs/keirin-jp-api.md)。
- 取得の流れ: `JSJ057`（日付 → 開催）→ `JSJ001` / `JSJ017` / `JSJ018` / `JSJ002`（開催）→ `JSJ012`（レース）。
  - 2026-10-04 から、出走表は開催ごとの `JSJ002`（1回で全レース分）で取る。それより前の raw はレースごとの `JSJ006` で、パーサーは両方読める。JSJ002 が空なら JSJ006 に戻す。
- **アクセスのマナー**（robots.txt で `/pc/json` が許可されていないため、オーナー了承のうえで控えめに収集する）:
  - 間隔は1秒以上（`--min-interval`）、1件ずつ、1日1回。1回あたり最大15日分（JSJ002 で1日約120リクエストなので、1回あたり約1,800〜2,000リクエスト。以前の10日分と同じくらい）。
  - User-Agent でプロジェクトを名乗る。403 などはすぐ止め、失敗したらその回の収集を打ち切る。
  - 取得済みの日は再取得しない（`--force` を付けない限り）。テーブルは raw から作り直す（`keirin rebuild`）。
  - オッズの時系列収集のように頻度が上がるものは、追加する前に負荷をよく考える。

## 分析するときの前提（車番の付け方）

- **ミッドナイトは、競走得点の高い順に1番車から並ぶ**（オーナー情報。データでも96.2%のレースで車番＝得点順位、1番車は100%得点1位）。
- **4・6・8番車には、同じ枠の相方（5・7・9番車）より実力が劣る選手を置く慣例がある**（枠連の釣り合いのためとみられる。オーナーの指摘）。
  データでは、9車で 4<5、6<7、8<9 がどれも 100%、7車（枠連は売らない）でも 6<7 が 95.7%。
- したがって「1番車有利」「4・6・8番車は来ない」は位置の効果ではなく、実力の配置の効果。車番を分析するときは、得点とミッドナイトかどうかをそろえる。

## 技術スタック

- Python 3.12、[uv](https://docs.astral.sh/uv/)、httpx。lint/format は ruff、テストは pytest。
- データ形式: raw（gzip 圧縮した JSON）＋ 日別 CSV。スキーマは [docs/data-schema.md](docs/data-schema.md)。分析には DuckDB で CSV を直接読む想定。

## コマンド

ローカル環境: uv は `~/.local/bin/uv`。データリポジトリは `../keirin-data` に clone してある。
収集ジョブを手動で動かすときは `gh workflow run collect.yml --repo keirin-platform/keirin-data [-f date=YYYY-MM-DD -f to=...]`。

```bash
uv sync --all-extras                      # 依存のインストール（app extra = streamlit を含む）
KEIRIN_DATA_DIR=../keirin-data uv run streamlit run src/keirin/viewer.py   # ビューア
uv run pytest -q                          # テスト
uv run ruff check . && uv run ruff format --check .

# 収集（デフォルトは昨日 JST。当日以降は結果が確定していないので拒否する）
uv run keirin collect --data-dir ../keirin-data
uv run keirin collect --date 2026-09-01 --to 2026-09-30 --data-dir ../keirin-data   # 期間指定
uv run keirin collect --date 2026-06-01 --to 2026-10-01 --max-days 15 --data-dir ../keirin-data  # 未取得の日を新しい順に最大15日
uv run keirin rebuild --data-dir ../keirin-data      # raw からテーブルを作り直す
uv run keirin corrections --date 2026-09-30 --data-dir ../keirin-data --changed-only  # 補正つき出走表
uv run keirin evaluate --date 2026-09-01 --to 2026-09-30 --data-dir ../keirin-data      # 公式と補正後の精度比較
uv run keirin estimate-deltas --boundary 2026-07-01 --data-dir ../keirin-data           # Δ の推定
uv run keirin rating --as-of 2026-11-01 --top 20 --data-dir ../keirin-data              # レーティング上位
uv run keirin evaluate --date 2026-09-01 --to 2026-09-30 --rating --data-dir ../keirin-data  # レーティングも評価
uv run keirin predict-eval --date 2026-09-28 --to 2026-10-04 --data-dir ../keirin-data   # 予想の評価（前日までで学習）
uv run keirin github-commit --repo-dir ../keirin-data --repo keirin-platform/keirin-data \
  --branch main --message "chore(data): ..."         # CI 用（GITHUB_TOKEN が必要）
```

## ディレクトリ

```
src/keirin/
  client.py         KEIRIN.JP API クライアント（間隔制御、リトライ、UA）
  collect.py        1日分を取ってきて raw bundle にまとめる
  parse.py          raw bundle → テーブル（meetings / races / entries / payouts）
  storage.py        raw と CSV のファイル配置、読み書き
  github_commit.py  GitHub API で署名付きコミットを作る
  score.py          競走得点の昇降級補正（tier、Δ、窓、History）
  analysis.py       補正つき出走表、精度評価（公式、補正後、レーティング）、Δ の推定
  rating.py         相手の強さを考慮したレーティング（Plackett–Luce、時間減衰、得点換算）
  hatena.py         keirin-blog の記事をはてなブログに同期（AtomPub、Fotolife、状態ファイル）
  lines.py          ライン（並び）の取得と解析（keirin.jp の nInfo、オッズパークの並び、raw の統合、lines テーブル）、位置のラベル、
                    府県（地区）からの並びの推定（guess_formation）
  predict.py        予想（Plackett–Luce。特徴、学習、1着・2着以内・3着以内の確率、評価）。docs/prediction.md
  notify.py         注目選手の出走通知（watchlist.toml の照合、Discord の Webhook、送信済みの記録）
  cli.py            `keirin` コマンド
  store.py          ビューア用のデータ取得（収集済みの日は CSV、それ以外は keirin.jp から。キャッシュつき）
  viewer.py         出走表ビューア（Streamlit）。並びの図（車番の色、ラインごと、競りは縦に重ねる）、
                    「出走表」タブ（補正得点、ライン、位置＝先頭／番手／単騎、競り）、「予想」タブ（1着・2着以内・3着以内の確率）、
                    「結果」タブ（着順、着差、上がり、決まり手、得点順位、払戻金）
                    並びは、当日は keirin.jp の最新の nInfo、なければ収集済みの raw/lines を使う
tests/              pytest（conftest.py に架空の API レスポンスがある）
docs/               API 調査メモ、データスキーマ
infra/data-repo/    データリポジトリに置くファイルの雛形
infra/blog-repo/    ブログリポジトリ（keirin-blog）に置くファイルの雛形
.github/workflows/  ci.yml（PR / main）、collect.yml（再利用可能な収集ワークフロー）
```

## 決定ログ

- 2026-10-02 データソースを KEIRIN.JP の内部 JSON API に決定（認証不要、2015年まで遡れる）。
- 2026-10-02 サイトポリシーを確認。私的利用は可、金銭的対価を得ることは禁止、複製は私的使用と引用の範囲まで。
- 2026-10-02 robots.txt で `/pc/json` が許可されていない件をオーナーに相談 → 「控えめな頻度で収集する」に決定。
- 2026-10-02 オーナーの判断で「コードは Public、データは別の Private リポジトリ（keirin-data）」に決定。
- 2026-10-02 言語に Python を採用。データの収集と分析のエコシステムが充実していて、Render でも動かしやすいため。
- 2026-10-02 データ形式は raw（gzip 圧縮した JSON）＋ 日別 CSV に決定。git の差分にやさしく、DuckDB でそのまま読める。SQLite は、バイナリを毎日コミットするとリポジトリが肥大化するので見送った。
- 2026-10-02 収集ジョブはデータリポジトリから再利用可能ワークフローを呼ぶ方式にした。Secret が不要で、ログが Private 側に残り、コミットが Verified になるため。
- 2026-10-02 PR は CI 通過後に Claude がマージする（オーナー指定）。方式は merge commit。
- 2026-10-03 記事のために、収集範囲を 2025-12-01 からに広げた（オーナー判断）。ペースは変えない（1回あたり最大10日分）。
- 2026-10-05 ライン（並び）は、過去分をオッズパーク、これから先を keirin.jp から取る（オーナー判断）。
  オッズパークは robots.txt の Crawl-delay（10秒）を守り、Actions で1回50分まで少しずつ取る（オーナー選択）。
  オッズパークのサイトポリシーは、私的使用以外の複製、**引用**、頒布を禁止しているので、記事には並び予想や短評を載せない（自分たちで集計した統計だけ）。
- 2026-10-05 予想（1着・3着以内の確率）をビューアの「予想」タブに入れた（オーナーの要望）。
  - モデルは Plackett–Luce。級班（S・A・L）ごとに学習し、ライン効果は1着と2・3着で分ける。過去の並びがまだないので、学習では府県（地区）から推定した並びを使う。
  - 学習はビューアの起動時（データが更新されたとき）に、直近 120 日のデータで行ってキャッシュする（今は約3秒）。Actions は使わない（minutes に余裕がないため）。依存に scipy を追加した。
  - ガールズはラインがないので、全員を単騎として扱う（並びを推定しない）。
  - 過去の日の予想は、その日も学習に入っているので参考値（画面に明示）。精度の確認は `keirin predict-eval`（前日までで学習）で行う。
- 2026-10-04 1回あたりの収集日数を10日から15日に増やした（オーナー判断）。JSJ002 で1日のリクエストが減ったので、1回あたりのアクセス量は以前と同じくらい。backfill は11月上旬から10/24ごろに前倒し。
- 2026-10-04 出走表の取得を JSJ002（開催ごとに1回）に切り替えた（オーナー承認）。サイトへのアクセスが約4割減る。GitHub 調査で知り、自分たちで確かめた。
  並び（ライン）の収集は、ほかの投票サイトからも取れるので急がない（オーナー判断）。
- 2026-10-03 相手の強さの考慮は、単純補正ではなく、**レーティング（Plackett–Luce）**で行う（オーナー承認）。
  単純補正は、番組の平均点で反映済みの部分を二重に数えるため採らない。ビューアへの表示は、精度を確かめてから行う。
- 2026-10-03 記事の公開先を、いったん Zenn に選定したあと、**はてなブログ**に変更した。
- 2026-10-03 リポジトリ名に `keirin-` を付けるルールにした（オーナー指定）。ブログのリポジトリは keirin-blog。同期は公式ワークフローではなく自前にした（署名とブランチ保護の理由、オーナー判断）。
  理由は、読者層が競輪の内容に合うことと、オーナーの既存の Zenn 技術アカウントと分けられること。セットアップは記事を書く時期に行う。
- 2026-10-02 backfill は直近4ヶ月（オーナー指定。競輪の予想では直近4ヶ月の成績がよく使われるため）。サイトの負荷を抑えるため、日次ジョブで1回あたり最大10日ずつ埋める。
- 2026-10-02 得点の窓が「当月＋前3ヶ月」だとわかったので、収集範囲を「4ヶ月前の月初から」に変えた（10月なら6/1〜）。窓全体と、前の月の出走表の評価に必要な分を確保するため。
- 2026-10-02 補正方式: 窓内の得点対象レースのうち、別の tier で走ったものを Δ で今の tier に換算する（`score.py`）。
- 2026-10-02 ビューアは最初 Render（FastAPI）で作った。しかし Render の本人確認でオーナーのカードが通らなかったので、
  **Streamlit Community Cloud** に切り替えた（オーナー判断。カード不要で、Private の組織リポジトリから動かせて、閲覧者をメールで制限できる）。
  FastAPI 版と Render 設定は削除した（git の履歴には残っている）。
  - アプリはデータリポジトリから動かす（秘密情報は不要。組織の設定で deploy key は無効）。データのコミットごとに最新になる。
  - **ビューアのコードを変えたら、データリポジトリの `requirements.txt` の SHA を更新する PR も出す**。requirements.txt が変わらないと再インストールされないため。
  - **公開範囲は「Who can view this app」で決まる。必ず「Only specific people can view this app」（Private）のままにする**。
    Public（public and searchable）にすると、収集データが誰でも見られて検索にも載り、サイトポリシーの「私的使用」の範囲を超える。
    閲覧できるのは、招待したメールアドレスと、keirin-data にアクセスできる GitHub ユーザーだけ。無料プランの Private アプリ枠は1つで、これが使っている。
  - **Streamlit には専用アカウント `shimomobot` でログインする**（2026-10-03 に切り替え）。Streamlit の GitHub 連携は OAuth の `repo` 権限で、
    ログインしたアカウントが触れるすべての Private リポジトリが対象になり、絞れない。そのため shimomobot を `keirin-data` だけの外部コラボレーター（admin。
    デプロイと deploy key の登録に必要）にした。オーナーの個人アカウントからは、Streamlit の許可を取り消した（2026-10-03 確認済み。アプリは shimomobot で作り直し、URL と Private 設定はそのまま）。
  - URL: https://keirin.streamlit.app/（Private）。Community Cloud は deploy key で clone するので、組織で deploy key を有効にする必要がある（2026-10-03、オーナーが設定）。
  - 当日の結果は、開催ヘッダ（JSJ001 `C0201race[].rcvKekka == "1"`）でレースが終わったのを確認してから JSJ012 を取る（終わっていないレースにはアクセスしない）。
  - ビューアは keirin.jp を1件ずつ取りに行き、タイムアウトは短め（8秒、2回まで）。失敗したら画面にエラーを出す。API 呼び出しと所要時間は Community Cloud のログ（Manage app）に出る。
  - 未収集の日の出走表は表示するときに keirin.jp から取得する（5分キャッシュ、1件ずつ）。
  Δ はまず遠山競輪研究所030の値を暫定で使い、10月下旬に自分たちのデータで推定し直す。

## 現状と TODO

- [x] API 調査、規約確認
- [x] 収集基盤（collect / rebuild / github-commit、CI、再利用可能ワークフロー）
- [x] keirin-data リポジトリの作成と、caller ワークフローの設置（2026-10-02）
- [x] GitHub Actions（海外の IP）から keirin.jp に届くことを確認（2026-10-02。ローカル実行と出力が完全に一致し、データのコミットは Verified）
- [ ] backfill: 2025-12-01 から（2026-10-02 に開始。日次ジョブが自動で進め、10/24 ごろに完了する見込み）
- [ ] 記事用の分析（backfill の完了後。テーマ案は「オーナーの要望」2. と docs/keirin-theories.md を参照）
- [ ] 分析の準備: 場→都道府県とバンクの諸元（周長、みなし直線）を、コードのデータとして持つ
- [x] ライン（並び）の収集（2026-10-05）。これから先の分は keirin.jp（当日・翌日の JSJ017 の nInfo、毎回と 16:00）、過去分と取りこぼしはオッズパーク（10秒間隔、1回50分まで）。docs/lines.md
- [ ] オッズパークの並びの backfill（メインの backfill が終わる 10/24 ごろに自動で始まり、2027-01 中旬〜下旬にそろう見込み）
- [ ] 検討: オッズの収集（必要になったら相談する）
- [ ] 選手の特徴の検出コード（番手捲り率、共倒れ率、経験ベイズ）。並びがたまってきたら試す。本格的な分析は 2027-02 ごろ（docs/rider-traits.md）
- [x] 注目選手の出走通知の仕組み（2026-10-05。docs/notifications.md）
- [x] Discord の Webhook の作成と Secret `DISCORD_WEBHOOK_URL` の登録（オーナー、2026-10-05）、テスト通知の送信を確認
- [x] 監視リストの最初の1人: 橋本 智昭（014714、番手。オーナー指定。番手捲りが多い印象）。出場予定は小倉F1 10/11〜、大宮F2 10/22〜
- [ ] 選手の特徴の分析で見つかった選手を watchlist.toml に追加する
- [x] 改善: 出走表をレースごとの `JSJ006` から、開催ごとの `JSJ002` に切り替えた（2026-10-04。10/01 分で 192 → 119 リクエスト、表は完全に一致。ビューアの当日分も JSJ002）
- [x] keirin-blog リポジトリと、はてなブログ同期（`hatena.py`、`blog-sync.yml`）
- [x] はてなブログ（keirin-platform.hatenablog.com、はてな ID: keirin-platform）の作成と、keirin-blog の Variables / Secret の登録（オーナー、2026-10-03）
- [x] テスト記事で確認（2026-10-03）: PR で下書きとプレビュー URL、Fotolife への画像アップロード、署名つきの状態コミット、更新、`draft = true` でマージしても下書きのまま。
  公開（`draft = false`）は、最初の本番記事で初めて通る。最初の実行は Fotolife が 403 を返したが、再実行で成功した（API キーを登録した直後だったため、一時的なものと判断）
- [x] レーティングの計算と評価のコマンド（`rating.py`）
- [ ] 2026-10-11 ごろ: レーティングの試作を評価する（級班をまたいでつながったか、半減期）
- [ ] backfill 完了後（10/24 ごろ）: レーティングを本格的に評価する。公式の得点に勝てばビューアに表示する。改善の候補は docs/rating.md
- [x] 補正ロジック（`score.py`）、評価と Δ 推定のコマンド
- [x] 出走表ビューア（`keirin.viewer`、Streamlit）。収集済みの日は CSV から（結果つき）、未収集の日は keirin.jp から取得して、補正得点を表示する
- [ ] Streamlit Community Cloud へのデプロイ（オーナーがアプリを作成する。手順は docs/deploy-streamlit.md）
- [ ] 2026-10 下旬: `estimate-deltas --boundary 2026-07-01` で Δ を推定し直して `score.STEP_DELTAS` を更新する。`evaluate` で 9月分を検証する
- [ ] 期が替わるごと（1月・7月）に Δ を再推定する（4月・10月の下旬）
- [x] 予想タブ（2026-10-05。docs/prediction.md）
- [ ] 予想の改良: ラインの連動（主導権を取るラインを先に決める2段階モデル）、補正得点・レーティング・当所成績を特徴に入れる
- [ ] 2026-10-12 ごろ: 10/5〜10/11 が収集されたら `predict-eval --date 2026-10-05 --to 2026-10-11` で予想を確かめ直す（9/28〜10/4 は特徴を決めるときに見たので、評価がやや甘い）
- [ ] 10/24 ごろ（backfill の完了後）: 予想の学習期間（120 日）を見直す
- [ ] 2027-01 ごろ（オッズパークの並びがそろったら）: 本物の並びで予想を学習し直して、推定の並びとの差を評価する
- [ ] オーナーから機能の要望を受けたら、ここに追記する
