# 注目選手の出走通知（Discord）

オーナーの要望（2026-10-05）: 特徴的な選手（docs/rider-traits.md）が、条件に合うレース（番手など）に出るときに Discord へ通知してほしい。

## 仕組み
- **監視リスト**: データリポジトリ（Private）の `watchlist.toml`。選手と条件を書く（下の書式）。分析で見つかった選手を、オーナーか Claude が PR で追加する。
- **照合**: 日次ジョブの並びの収集（`keirin collect-lines --watchlist ...`）の中で行う。03:30 JST（当日・翌日）と 16:00 JST（当日）。
  - 当日・翌日の出走表（`JSJ017`）を、並びの収集と共用するので、**追加のアクセスはない**。
  - 位置の条件がある場合は、並び予想が公開されてから照合する（公開前は次の実行で再挑戦する）。
- **通知**: Discord の Webhook（Secret `DISCORD_WEBHOOK_URL`）に、レース（場、R、種目、日付、発走時刻）、選手（車番、名前、脚質）、位置、並び、理由を送る。1通に最大10件。
- **重複防止**: 同じ（レース、選手、条件）は一度だけ通知する。送った記録は `notify/sent.json`（7日より前は消す）。Webhook が未設定のときはログに出すだけで、送ったことにはしない。

## watchlist.toml の書式
```toml
[[watch]]
racer_id = "014779"                      # 登録番号（必須）
name = "名川 豊"                          # 表示名
reason = "決勝以外で番手捲り率が高い"       # 通知に載せる理由
positions = ["番手"]                      # 先頭 / 番手 / 3番手 … / 単騎 / 競り。省略すると位置を問わない
leader_styles = ["逃", "両"]              # 自分のラインの先頭の脚質（省略可）
exclude_finals = true                     # 決勝を除く（省略すると false）
```
- `positions` の「番手」は、競りの番手にも合う。競りだけを見たいときは「競り」と書く。

## セットアップ（オーナーの作業）
1. Discord で通知したいチャンネルの「設定 → 連携サービス → ウェブフック → 新しいウェブフック」で Webhook を作り、URL をコピーする。
2. keirin-data の Settings → Secrets and variables → Actions → New repository secret で、`DISCORD_WEBHOOK_URL` に URL を登録する。
3. Actions の collect ワークフローを `discord_test` にチェックを入れて手動実行すると、テスト通知が届く（Claude が実行してもよい）。

## 注意
- Webhook の URL は、知られると誰でも投稿できる。Secret 以外の場所（コード、ログ、チャット）に書かない。
- 通知の内容は個人用。Discord のチャンネルは、オーナーだけが見られる場所にする。
