# データスキーマ

データは Private リポジトリ `keirin-platform/keirin-data` に置いている（このリポジトリには置かない）。

```
raw/YYYY/YYYY-MM-DD.json.gz            その日のAPIレスポンス（生データ、全部入り）
tables/<table>/YYYY/YYYY-MM-DD.csv     raw から作った正規化テーブル（UTF-8、ヘッダ付き）
```

- raw が正本。テーブルは `keirin rebuild` でいつでも作り直せる（サイトへのアクセスは発生しない）。
- 出力は決定的（行はソート済み、gzip の mtime は 0 固定）なので、同じ日を取り直しても差分は出ない。
- キー: `date` + `venue_code` + `race_no` (+ `car_no`)。

## meetings（開催日）

| 列 | 例 | 説明 |
|---|---|---|
| date | 2026-10-01 | 開催日 |
| venue_code | 25 | 場コード（KEIRIN.JP の KeirinCd） |
| venue_name | 大宮 | 場名 |
| grade | F1 | GP / G1 / G2 / G3 / F1 / F2 |
| day_label | 初日 | 初日 / 2日目 / 最終日 など |
| time_slot | day | day / night / midnight / morning（推定の対応表） |
| title | スポーツニッポン新聞社杯 | 開催タイトル |

## races（レース）

| 列 | 説明 |
|---|---|
| date, venue_code, race_no | キー |
| race_class | 種目（例: Ａ級予選、Ｓ級決勝） |
| close_time / start_time | 締切 / 発走（HH:MM） |
| weather / wind_speed | 天候 / 風速 (m/s) |
| entries | 出走数 |

## entries（出走表と着順。1行 = 1選手 × 1レース）

| 列 | 説明 |
|---|---|
| date, venue_code, race_no, car_no | キー（car_no = 車番） |
| racer_id / racer_name | 選手登録番号 / 選手名 |
| prefecture / age / term | 府県 / 年齢 / 期別 |
| class / prev_class | 級班 / 前期級班 |
| style | 脚質（逃 / 両 / 追） |
| score | 競走得点 |
| nige, makuri, sashi, mark | 決まり手の回数（逃げ / 捲り / 差し / マーク） |
| back, home, start | B回数 / H取り回数 / S取り回数 |
| win_rate, top2_rate, top3_rate | 勝率 / 2連対率 / 3連対率（%） |
| finish / finish_pos | 着順（元の文字列。「失」なども入る）/ 数値にできたものだけ整数 |
| margin / last_lap | 着差 / 上がりタイム（秒） |
| kimarite / bh | 決まり手 / B・H |
| notes | 個人状況（失格や落車など、`/` 区切り） |

詳細出走表（JSJ006）がないレースでは、`score` などの成績列は空になる（出走表一覧 JSJ017 の情報で補っている）。

## payouts（払戻）

| 列 | 説明 |
|---|---|
| date, venue_code, race_no | キー |
| bet_type | 2枠複 / 2枠単 / 2車複 / 2車単 / ワイド / 3連複 / 3連単 |
| combination | 組番（例: `1-4-2`、`1=4`） |
| payout | 100円あたりの払戻金（円） |
| popularity | 人気順 |

未発売の券種は出力しない。同着の場合は同じ券種が複数行になる。

## DuckDB での読み方の例

```sql
SELECT * FROM read_csv('tables/entries/*/*.csv', union_by_name = true);
```
