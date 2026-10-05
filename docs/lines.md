# ライン（並び）データの取得

ライン関係の通説（番手有利、3番手、単騎、競り、ラインワンツー。docs/keirin-theories.md）を検証するには、各レースの並びが必要。
keirin.jp の結果データには並びが残らない（レース後は空になる）ので、取得元を調べた（2026-10-05）。

## 取得元
| | オッズパーク | keirin.jp（公式） |
|---|---|---|
| 場所 | `https://www.oddspark.com/keirin/RaceList.do?joCode={場コード}&kaisaiBi={YYYYMMDD}&raceNo={R}` の `<ul class="keirinRyosouline">` | 出走表一覧 `JSJ017`（`encp=<開催>`）の `rInfo[].nInfo`、`line`、`seri` |
| 過去のレース | **取れる**（2025-01-05 の立川1R でも確認） | 取れない（レース後は `nInfo` が空） |
| 単位 | 1レース1ページ（全レースの出走表ページ `AllRaceList.do` には並びがない） | 1開催1回 |
| アクセスの間隔 | robots.txt の **Crawl-delay は10秒** | 1秒（これまでどおり） |
| 場コード | `joCode` は keirin.jp の場コードと同じ（武雄 84、立川 28、奈良 53 で確認） | — |
| 一致の確認 | 2026-10-05 の奈良1R・2R で、両方とも同じ並び（`156/42/7/3`、`415/37/26`） | |

### オッズパークの形式
```html
<ul class="keirinRyosouline">
  <li class="sirusi"><span class="hidari">←</span></li>     <!-- 向き（使わない） -->
  <li><span class="no2">2</span>逃捲</li>                    <!-- noN = 車番 N、続く文字はオッズパークの戦法 -->
  <li><span class="no0">(</span></li>                        <!-- no0 は記号: "(" と ")" で囲まれた選手どうしは競り -->
  <li><span class="no1">1</span>追込</li>
  <li><span class="no3">3</span>追込</li>
  <li><span class="no0">)</span></li>
  <li><span class="no5">5</span>追込</li>
  <li><span class="no6">6</span>追込</li>
  <li><span class="no0">&nbsp;</span></li>                   <!-- ラインの区切り -->
  <li><span class="no7">7</span>自在</li>
  <li><span class="no4">4</span>自在</li>
</ul>
<div class="keirinRyosousouhyo">短評（使わない）</div>
```
→ `2(13)56 / 74`（2026-10-04 の武雄3R。1と3が2の番手を競る、二分戦）。

### keirin.jp の形式
- `nInfo[]`: `{"syaban": 車番, "narabiX": 横位置, "narabiY": 段}`。横位置が連続していれば同じライン、間が空けば別ライン。
- 競りは同じ横位置に2段（`narabiY`）で表すとみられる（`seri` フラグ、`narabiYCnt` = 段数）。実例で確かめてから使う。
- `line`: ライン構成の名前（「二分戦」「三分戦」「コマ切れ」など）。
- 当日 00:05 の時点で、その日のレースの `nInfo` が入っていた。前日のどの時点から入るかは未確認。

## 利用条件
- オッズパークの[サイトポリシー](https://www.oddspark.com/service/sitepolicy.html)（2025-12-16 改定）
  - 著作権について: 「いかなる目的であれ無断での複製、**引用**、転送、頒布、上演、改変、修正、追加など一切の行為を禁止」。使えるのは「私的使用のための複製」などの場合に限る。
  - [免責事項](https://www.oddspark.com/service/rule.html)に、スクレイピングを直接禁止する条文はない。会員規約は、ログインして投票する会員向け。
- [robots.txt](https://www.oddspark.com/robots.txt): `User-agent: *` は `Crawl-delay: 10`。`RaceList.do` は Disallow されていない（`Noindex` は検索エンジン向けの指定）。AI 系のクローラー（GPTBot、ClaudeBot など）は全面禁止。
- 私たちの方針:
  - 私的利用として Private リポジトリにだけ保存する。間隔は **10秒以上**。User-Agent でプロジェクトを名乗る。失敗したら止める。
  - 保存するのは並びの部分（`ul.keirinRyosouline`）だけ。ページ全体や短評は保存しない。
  - **記事にオッズパークの並び予想や短評は載せない**（引用も禁止のため）。載せるのは、自分たちで集計した統計だけ。

## 実装（`src/keirin/lines.py`、2026-10-05）
- **これから先の分（keirin.jp）**: `keirin collect-lines` で、当日と翌日の `JSJ017` から `nInfo` を取る（1開催1回、1秒間隔）。日次ジョブの毎回の実行と、16:00 JST の「並びだけ」の実行で動く。
  - 2026-10-05 00:05 の時点で、当日のデイ・ナイター系の開催には `nInfo` が入っていたが、遅い時間帯の開催（3場）はまだ空だった。翌日分は、まだ出走表自体が出ていなかった。そのため 16:00 にもう一度取る。
- **取りこぼしと過去分（オッズパーク）**: `keirin backfill-lines` で、races テーブルにあるのに並びがないレースだけを、新しい日付から取る（10秒間隔）。
  - 1回あたり最大50分。**メインの収集に欠けている日がある間は待つ**（レースの一覧が必要なため。メインの backfill は 10/24 ごろに終わる）。
  - 並びがないページ（中止など）は「なし」として記録し、取り直さない。403 などが返ったら止める。
  - 約300日 × 約85レース ≈ 2万5千ページなので、**2027年1月中旬〜下旬にそろう見込み**。
- **raw**: `raw/lines/YYYY/YYYY-MM-DD.json.gz` = `{"date", "races": {"<場コード>-<R>": {"source", "fetched_at", "data"}}}`。
  - data は、keirin.jp なら `nInfo`、`line`、`seri`、`narabiYCnt`。オッズパークなら `ul.keirinRyosouline` の HTML（ページ全体や短評は保存しない）。
  - 最初に取れた並びを残し、空の結果では上書きしない。
- **テーブル** `tables/lines/YYYY/YYYY-MM-DD.csv`（1行 = 1選手）: スキーマは docs/data-schema.md。

## 並びの推定（予想用）
- 並びが分からないレース（過去分で並びが未収集のもの、当日の未公開のもの）は、予想のために府県（地区）と脚質から並びを推定する（`lines.guess_formation`）。規則と精度は [prediction.md](prediction.md)。
