# KEIRIN.JP 内部 JSON API 調査メモ

調査日: 2026-10-02。公式サイト [KEIRIN.JP](https://keirin.jp/)（JKA）のフロントエンドJSを読んで、実際にリクエストを送って確かめた。
非公開のAPIなので、予告なく変わることがある。

## 基本

- エンドポイント: `GET https://keirin.jp/pc/json?type=<画面ID>&<パラメータ>`
  - `commonJSON.js` の `Com.getRequestGet(reqType, params)` が `type=reqType` を付けて叩いている。
  - 認証・Cookie・Referer は不要。レスポンスは `application/json`。
- レスポンス共通: `resultCd`（`0` = 正常、`-1` = エラー/データなし）、`writeANA`（アクセス解析用のフラグで、無視してよい）。
- 開催とレースは `encp` という不透明トークンで指定する（例: `CpPQzSRN4GOX-...`）。
  トークンは自分で組み立てず、上の階層のレスポンスに入っている値をそのまま使う。

## 収集に使っているエンドポイント

| type | パラメータ | 元の画面 | 内容 |
|---|---|---|---|
| `JSJ057` | `kday=YYYYMMDD` | トップ（開催情報） | その日の開催一覧 `kInfo[]`: `jyoName` 場名、`KeirinCd` 場コード、`gradeIconChar` グレード、`nitijiIconChar` 初日/2日目/最終日、`kaisaiIconChar` 開催区分、`encPrm` 開催トークン |
| `JSJ001` | `encp=<開催>` | 共通ヘッダ | `C0201data.raceName` 開催タイトル、`joName` |
| `JSJ017` | `encp=<開催>` | 出走表一覧 (PJ0305) | `kaisaihi`、`rInfo[]`: `raceNo`、`syumoku` 種目、`denTime` 締切、`stTime` 発走、`sInfo[]`（`syaban`、`senNo`、`senName`、`huken`、`kyaku`） |
| `JSJ018` | `encp=<開催>` | 結果一覧 (PJ0306) | `kday`、`resultList[]`: `rclblRaceNo`、1〜3着、2車単/3連単の払戻、**`raceRVPrm` = レーストークン** |
| `JSJ006` | `encp=<レース>` | 出走表 (PJ0315) | `sensyuTypeInfo[]`: 登録番号、級班、脚質、期別、年齢、`heikinTokuten` 競走得点、逃/捲/差/マーク回数、B/H/S回数、勝率、2連対率、3連対率、今場所と直近4場所の成績 |
| `JSJ012` | `encp=<レース>` | 結果 (PJ0326) | `tenki` 天候、`husoku` 風速、`tyakujyunItemSubData[]`（着、車番、着差、上がり、決まり手、B/H、個人状況）、`haraiGakuSubData`（全券種の払戻） |

`haraiGakuSubData` のキーと券種の対応:
`WH2`=2枠複、`WT2`=2枠単、`SH2`=2車複、`ST2`=2車単、`W`=ワイド、`RH3`=3連複、`RT3`=3連単（いずれも末尾に `HaraiGakuDispItemSubData` が付く）。

`kaisaiIconChar` の対応（推定）: `1`=デイ、`3`=ナイター、`5`=ミッドナイト、`8`=モーニング。

## 未使用だが存在を確認したもの

| type | 内容 |
|---|---|
| `JSJ014` | 開催のレース番号一覧とレーストークン。古い開催では `resultCd=-1` になるので使っていない |
| `JSJ015` | 開催の番組情報（ランキングなど） |
| `JSJ016` | 場外の発売状況 |
| `JSJ048` | 共通ヘッダ |
| `JSJ078`〜`082` | トピックス、お知らせ、ピックアップなど |
| `JSJ007`〜`011`、`JSJ013` | レース詳細の各タブ（`commonRace.js` の rl/rw/rs/rt/ri/rc コントローラ） |
| `JST010`〜`017` | オッズ（`commonRace.js` の bc/ot/op/kr コントローラ）。中身は未調査 |

## 過去データ

- `JSJ057` は少なくとも2015年まで遡れる。
- 2025-01 の開催で `JSJ017` / `JSJ018` / `JSJ006` / `JSJ012` が取れることを確認した。

## 利用条件（サイトポリシー / robots.txt）

- [サイトポリシー](https://keirin.jp/pc/dfw/portal/guest/policy/index.html)
  - 著作権: 「私的使用のための複製」と「引用」の範囲を除き、複製・転用は禁止。
  - 禁止事項: 私的利用の範囲を超えて無断で使い、第三者から金銭的対価を得ること。
  - スクレイピングや自動アクセスを直接禁止する条文はない。→ **個人の私的利用は可能と判断した**。
- [robots.txt](https://keirin.jp/robots.txt): `Disallow: /` で全体を禁止し、一部のページだけ Allow している。
  `/pc/json`、`/pc/racelist`、`/pc/racelive` は Allow に入っていない。
  法的な拘束力はないが運営側の意向なので、**控えめな頻度で収集する**ことにした（オーナー確認済み、2026-10-02）。
  - リクエストは1件ずつ、間隔は1秒以上。1日1回の定期実行で、1日分あたり約200リクエスト。
    backfill は1回あたり最大10日分（約2,000リクエスト）に分けて、日をまたいで進める。
  - User-Agent でプロジェクトを名乗る（`keirin-platform/0.1 (personal use; +<repo URL>)`）。
  - 5xx/429 は少しだけリトライし、403 などはすぐに止める。失敗したらその回の収集を打ち切る。
  - 取得したデータは Private リポジトリにだけ置き、公開しない。
