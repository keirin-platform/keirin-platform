# GitHub 上の競輪関連リポジトリ調査

調査日: 2026-10-04。`gh search repos`（keirin、競輪、kdreams、winticket など14語）と、`gh search code`（主要なデータ源の URL）で探し、README、構成、CLAUDE.md を読んだ。

## 全体の傾向
- 見つかったのは約80件。ほとんどが**スター0〜1の個人プロジェクト**。スターがあるのは takeruts/keirin_ai（★9、2018年）と apiss2/scraping（★6、競馬・競輪・競艇）くらい。
- **2026年に急増**していて、「keirin-ai」系の予想アプリが多い。CLAUDE.md や AGENTS.md を置いた、AI エージェントで開発・運用しているものが目立つ。
- **ライセンスを付けているものは1件もない**。コードは流用できない（参考にするのは手法と公開情報だけ）。
- 名前が同じだけで無関係なものもある（rachbowyer/keirin は Clojure のベンチマークライブラリ、jpld/Keirin は Objective-C のライブラリ）。
- いくつかの Public リポジトリは、収集したデータや学習済みモデルをそのままコミットしている（数十〜百数十MB）。私たちは、データを Private リポジトリに分けている。

## データ源ごとの利用状況
| データ源 | 取り方 | 使っているリポジトリ |
|---|---|---|
| **KEIRIN.JP（公式）** | `/pc/json?type=JSJxxx`（純 JSON）と、HTML に埋め込まれた `jsonData['PJ0305']` など | Tower2007/keirin-ai、（私たち）。nakasan3156-star は公式の印刷用 PDF（並び予想、2車単オッズを含む5種類）を使う |
| netkeirin（netkeiba 競輪） | HTML スクレイピング。オッズ、配当、データベースページ | Tower2007（発走前オッズ）、haruqube、apiss2、nyanta-kun |
| WINTICKET | HTML の `window.__PRELOADED_STATE__` から JSON を取り出す。3連単の全オッズも | squidattacker、hiraiyou1217-pixel、GOTO-TSL（ライン予想）、nyanta-kun、v9d9tcb85c-ux |
| Kドリームス | HTML スクレイピング（requests、BeautifulSoup、Selenium） | takeruts、GOTO-TSL、ytkg、nau539、tatsuki817 |
| オッズパーク、競輪ステーション | HTML | nyanta-kun/kiseki（の keirin ディレクトリ）、keirinjingle |

## カテゴリ別の主なリポジトリ

### 1. データ収集・蓄積の基盤
| リポジトリ | 内容 |
|---|---|
| **Tower2007/keirin-ai**（2026-04〜、更新中） | **私たちに最も近い**。<br>・keirin.jp から開催、出走表、**ライン（並び）**、選手成績、結果、払戻を CSV に蓄積し、netkeirin の発走前オッズ（5、3、2、1、0.5分前）を記録する。<br>・LightGBM の週次再学習に品質ゲートを付け、**購入はせず shadow 評価だけ**（予測を時点固定で保存して後から検証）。<br>・Windows のタスクスケジューラで動かす。リクエスト間隔は0.8秒。<br>・CLAUDE.md、AGENTS.md、GEMINI.md で複数の AI エージェントに運用させている。姉妹プロジェクトにオートレース版、競艇版がある |
| squidattacker/keirin-prediction | WINTICKET から収集し、DuckDB と dbt の bronze／silver／gold の3層で特徴量を作り、機械学習で勝率を予測する |
| nau539/keirin_odds | Kドリームスのオッズと払戻を取得して、GUI（tkinter）で監視・分析する。SQLite に保存し、Discord に通知する |
| apiss2/scraping | netkeirin のレース情報とオッズを取るスクレイパー（競馬・競艇と共通） |
| keirinjingle（ユーザー） | 競輪・競艇・オートの出走表、レース一覧、発走タイマー、オッズの追跡ツール |

### 2. 機械学習による予想
| リポジトリ | 内容 |
|---|---|
| takeruts/keirin_ai（2018） | Kドリームスの2008〜2018年、146,536レースで深層学習（TensorFlow、Keras）。README では「実用的な結果は得られない」 |
| GOTO-TSL/keirin-prediction（2021） | Kドリームスのデータで多層パーセプトロン（入力29、出力2）。WINTICKET からライン予想も取る |
| ytkg/gk-yosoku（2026） | **ガールズ専用**。Ruby と LightGBM で「1着」と「3着以内」の2つのモデルを作り、合成して2連単・3連単の候補を出す。2連単専用のモデルもある。データは Kドリームス |
| haruqube/keirin-ai-predictor（2026） | LightGBM の LambdaRank で着順を予測し、自動賭け戦略まで作る。**バックテストで ROI 219% と主張**しているが、過学習やバックテストの楽観の可能性がある（実運用の成績ではない） |
| nyanta-kun/keirin（アーカイブ済み） → nyanta-kun/kiseki | LightGBM による競輪予想（netkeirin と WINTICKET の特徴量）。ブランチ保護をかけて PR 必須で運用。現在は競馬システム kiseki の中に keirin ディレクトリとして移っている |
| tatsuki817/keirin-ai-complete | 複数の予想サイトと AI のデータを統合して買い目を出す（Node.js と XGBoost） |
| ほか多数の「keirin-ai」 | Web アプリやバックエンド、PWA など。中身が空のものも多い |

### 3. 期待値・オッズ分析
| リポジトリ | 内容 |
|---|---|
| hiraiyou1217-pixel/keirin-value-app | WINTICKET の3連単の全オッズを Playwright で取得して期待値を計算する Streamlit アプリ。オッズを使わない AI 予測も入っている |
| nakasan3156-star/shogo-keirin-2shatan | **KEIRIN.JP 公式の PDF 5種類**（基本情報・並び予想、直近成績2種、着度数・H・S回数、2車単オッズ）を照合し、能力確率と市場確率（オッズ）を `市場確率 × (能力確率 ÷ 市場確率)^λ` で混ぜて、期待値の上位を返す |

### 4. 展開（ライン）を扱うツール
| リポジトリ | 内容 |
|---|---|
| naoto-web/tenkai、datemakix/keirin-board | 隊列（ライン）を画面上で並べる「展開ボード」 |
| yahikeirin-ops/keirin-yosoku-tool | 展開予想の文章を自動生成する |
| shiro-aiLAB/project-ykp | ラインの人数や B・H 回数から主導権を評価する展開シミュレーター（研究用、サンプル CSV） |
| ebisu7024-code/zen-keirin-lab | 個人用の予想ノート。ライン隊列の変更、能力・展開・心理の評価、収支の記録 |

### 5. その他
- fujikongu/keirin-vision-bot: 出走表の画像を Google Cloud Vision と OpenAI で読み取る LINE Bot。
- KotaroAtsu/keirinGame: 競輪のゲーム。

## 私たちのプロジェクトとの比較
| 観点 | ほかのリポジトリ | 私たち |
|---|---|---|
| 主な目的 | 着順予想、買い目、期待値 | **競走得点の昇降級補正**、**相手の強さを考慮したレーティング**、通説の検証と記事 |
| データ源 | Kドリームス、WINTICKET、netkeirin が多い。公式は少数 | KEIRIN.JP（公式）の内部 JSON |
| データの置き場 | Public リポジトリにデータやモデルをコミットしているものもある | コードは Public、データは Private に分離（サイトポリシーの私的使用に合わせる） |
| 運用 | 手元の PC（cron、タスクスケジューラ）が多い | GitHub Actions（サーバー不要）と、Streamlit Community Cloud |
| 検証 | バックテストの収支を前面に出すものが多い | 未来のデータを使わない評価（as_of、ペアの的中率）で、公式の得点と比べる |

## 取り入れたいこと
1. **JSJ002 で、1開催1回で全レースの出走表を取る**（2026-10-04 に確認）。12レース分の `sensyuTypeInfo` が1回で返り、選手の項目は JSJ006 と同じで、枠番・距離・周回数も付く。
   JSJ006 をレースごとに呼ぶのをやめれば、1日あたりのリクエストが約190件から約110件に減る（Tower2007 の CLAUDE.md で知った）。
2. **ライン（並び）は発走前だけ取れる**。出走表（`JSJ017`、HTML の `PJ0305`）の `nInfo`（narabiX、narabiY）は、レースが終わると空になる（Tower2007 の記述。私たちの過去データでも空だった）。
   ライン関係の通説を検証するなら、当日の朝に並びを集める必要がある。ほかに、公式の印刷用 PDF にも並び予想がある（nakasan3156-star）。
3. **オッズ**は、netkeirin の発走前スナップショット（Tower2007）や、WINTICKET の全オッズ（hiraiyou1217）で集めている例がある。私たちがオッズを扱うなら、公式の JST 系 API を先に調べる。
4. **評価の規律**: 予測を時点固定で保存して後から照合する shadow 評価と、採用の品質ゲート（Tower2007）。バックテストの収支は楽観しやすい（haruqube の ROI 219% は、その典型の可能性）。
5. **モデルの組み方**: 「1着」「3着以内」を別のモデルにして合成する（ytkg）、LambdaRank で着順を直接学習する（haruqube）。レーティングを予想に使うときの参考になる。
6. **市場とモデルの混合**: `市場確率 × (能力確率 ÷ 市場確率)^λ`（nakasan3156-star）。オッズを使う分析をするときの参考になる。
7. **展開ボード**: ラインを画面に並べる UI。並びのデータを集めるようになったら、ビューアに取り入れられる。

## 注意
- すべてライセンスなしなので、コードは使わない。参考にするのは考え方と、サイトの公開仕様（API の挙動など）だけ。サイトの挙動は、自分たちで確かめてから使う。
- README の主張（精度、ROI など）は検証していない。
