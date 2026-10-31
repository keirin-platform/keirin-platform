# keirin-research 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 競輪の共通認識（検証した法則、公式の資料で確かめた規則・事実、データの性質、保留の候補）を、Claude Code と Codex の両方から読める Public リポジトリ `keirin-platform/keirin-research` として作る。あわせて、法則を確かめるスクリプトを keirin-platform に足す。

**Architecture:** 知識は `knowledge/` の Markdown に1件ずつ ID 付きで置く。保留の候補は `candidates.md` に分けて置く。作業の段階ごとのスキル（SKILL.md）が、相対パスで知識を指す。書式と公開してよいものの範囲は `tools/kb.py`（標準ライブラリだけ）で検査し、CI で回す。法則の検証は keirin-platform の `src/keirin/research.py` で行い、集計値だけを出す。知識の「検証の記録」は、そのスクリプトのコミットを指す。

**Tech Stack:** keirin-research: Python 3.12 標準ライブラリ（unittest）、GitHub Actions（ubuntu-24.04 の python3）、`claude plugin eval`。keirin-platform: 既存の uv / numpy / scipy / pytest / ruff。

**Spec:** `docs/superpowers/specs/2026-11-01-keirin-research-design.md`（keirin-platform）

## Global Constraints

- リポジトリ: `keirin-platform/keirin-research`（Public）。手元は `~/github/keirin-platform/keirin-research`。ライセンスは MIT（著作権者 shimomo）。
- 知識・スキル・評価ケースの文章は日本語。コミットメッセージは Conventional Commits 形式の英語。
- コミットはすべて GPG 署名つき。コミットの前に `echo test | gpg --batch --pinentry-mode error --local-user "$(git config user.signingkey)" --clearsign >/dev/null` でキャッシュを確かめる。失敗したら、オーナーに `! echo test | gpg --clearsign >/dev/null` の実行を頼む。
- どのリポジトリでも `main` へ直接コミットしない（keirin-research の初期コミットだけは例外）。ブランチ名は `feat/...`、`fix/...`、`docs/...`、`chore/...`。PR は CI が通ったら `gh pr merge <n> --merge --delete-branch --subject "<PRタイトル> (#<n>)"` でマージする。
- keirin-research に置かないもの: 収集データとその抜き出し、選手名、選手登録番号、レースと位置から選手を特定できる記述、ビューアの URL、keirin.jp の内部 API の呼び出し方（`/pc/json`、`JSJ` で始まる種類の番号）。オッズパークの並び予想や短評の文言。
- keirin-research の最初の push の前に、中身をオーナーに確認してもらう（Task 12）。
- スキルは `${CLAUDE_PLUGIN_ROOT}` を使わない。知識はスキルのディレクトリからの相対パス `../../knowledge/...`、候補は `../../candidates.md` で指す。
- スキルの説明文には「競輪」を入れ、「オートレース」を入れない。
- 大きさの上限: `knowledge/` の Markdown と `candidates.md` は1ファイル 30,000 バイト、`skills/*/SKILL.md` は 12,000 バイト。
- 登録の基準（COMMIT-002 に書く値）: 効果の 95% 区間（レース単位のブートストラップ 2,000 回、seed 0）が0を含まず、期間を日付で前半と後半に分けても点推定が同じ向きなら「確認済み」。区間が ±実質的な大きさの内側に収まれば「否定」。どちらでもなければ保留。実質的な大きさは、ラインの連動が 1レースあたり 0.05 組、1番車の勝率の差が 0.03（3ポイント）。
- 封印期間は設けない（オーナー決定）。法則は、期替わりのあと（4月・10月の下旬）と、本物の並びが1年分そろったときに、登録後のデータで確かめ直す。
- keirin-platform のコードは ruff（line-length 100、py312）と pytest に従う。テストのデータは架空のものだけを使う。
- 出典の書き方（kb.py が検査する）: `keirin-platform:<パス>@<コミット> <節>`（コミットは必須）、`keirin-data:observations.md OBS-<3桁>`、`オーナー（YYYY-MM-DD の会話）`、`https://…`（後ろに空白と題を付けてよい）。複数は「、」で区切る。

## Review Focus

- **競りの選手どうし**: 同じ位置を争う2人（例: `2(13)56/74` の 1 と 3）は、同じラインの仲間として数えない。数えると連動が過大に出る（Task 8 のテスト `test_contested_riders_are_not_line_mates`）。
- **CI に keirin-data がない**: 知識の出典に `keirin-data:observations.md OBS-001` があっても、`kb.py check` は keirin-data を読まずに通る。観察記録の中身の検査は `--observations` を付けたときだけ行う（Task 3 のテスト `test_observation_source_does_not_need_keirin_data`）。
- **公開の検査の誤検出**: 日付（2026-10-05）、桁区切りの件数（23,456）、コミット（0850049、3f2a9c1）、7桁以上の数字を、選手登録番号として止めない（Task 3 のテスト `test_public_scan_ignores_dates_counts_and_commits`）。
- **移る節を指す出典**: CLAUDE.md の「分析するときの前提」や docs/keirin-theories.md は v1 で書き換わる。コミットのない出典は通さない（Task 3 のテスト `test_platform_source_requires_commit`）。
- **オートレースのスキルとの取り違え**: 同じマシンに autorace-research が入っている。説明文に「競輪」が入り「オートレース」が入らないこと（Task 6 のテスト `test_descriptions_name_keirin_only`）。評価で、オートレースや競馬の言葉が出ないこと（Task 7 の共通の採点 `no-other-sport-terms.md`）。

---

### Task 1: 設計書と計画を keirin-platform にコミットする

**Files:**
- 既存: `keirin-platform/docs/superpowers/specs/2026-11-01-keirin-research-design.md`、`keirin-platform/docs/superpowers/plans/2026-11-01-keirin-research.md`（ブランチ `docs/keirin-research-design` に作成済み、未コミット）

- [ ] **Step 1: GPG のキャッシュを確かめる**（Global Constraints のコマンド）
- [ ] **Step 2: コミットして push する**

```bash
cd ~/github/keirin-platform/keirin-platform
git add docs/superpowers/specs/2026-11-01-keirin-research-design.md docs/superpowers/plans/2026-11-01-keirin-research.md
git commit -S -m "docs: add keirin-research design and plan"
git push -u origin docs/keirin-research-design
gh pr create --title "docs: add keirin-research design and plan" --body "<設計書と計画へのリンク、要点3行>"
```

- [ ] **Step 3: CI が通ったらマージする**（Global Constraints の `gh pr merge`）。Expected: main に merge commit。

---

### Task 2: keirin-research の骨組みと kb.py の核（autorace-research から移植）

**Files:**
- Create: `keirin-research/LICENSE`、`.gitignore`、`README.md`（最小限）
- Create: `keirin-research/tools/kb.py`
- Create: `keirin-research/tests/__init__.py`、`tests/test_kb.py`
- Create: `keirin-research/knowledge/README.md`

**Interfaces:**
- Produces（`tools/kb.py`）:
  - `Entry(id: str, title: str, fields: dict[str, str], path: Path, line: int)`（frozen dataclass）
  - `parse_file(path: Path, root: Path) -> list[Entry]`
  - `load(root: Path) -> list[Entry]`（`knowledge/**/*.md`、README.md を除く）
  - `deps(entry: Entry) -> list[str]`、`dependents(entries, target: str) -> list[Entry]`、`next_id(entries, prefix: str) -> str`
  - `vocab_patterns(entries) -> list[str]`、`vocab_regex(entries) -> str`
  - `check(root: Path) -> list[str]`（各要素は `<相対パス>:<行>: <内容>`。空なら合格。Task 3 で `observations` 引数を足す）
  - CLI: `python3 tools/kb.py check [--root DIR]`、`dependents <ID>`、`next-id <PREFIX>`、`vocab-regex`
  - 定数 `FILE_FOR_PREFIX`、`KINDS_FOR_PREFIX`、`EXTRA_KEYS`、`COMMON_KEYS`

`~/github/autorace/autorace-research/tools/kb.py` と `tests/test_kb.py` を出発点にする（同じオーナーのコード）。変えるのは下の表と出典の書式で、構文（`## <ID> <題>`、`- <キー>: <値>`、2つ以上の空白の字下げで値が続く）はそのまま。OBS の扱いと出典・公開の検査は Task 3 で足すので、ここでは移植しない。

接頭辞と置き場所、種別:

| 接頭辞 | 置き場所 | 種別 | 追加の必須キー |
|---|---|---|---|
| `RULE` | `knowledge/domain/rules.md` | `規則・事実`・`データで確かめた事実` | — |
| `MECH` | `knowledge/domain/mechanisms.md` | `仕組み` | — |
| `LAW` | `knowledge/domain/laws.md` | `全体の傾向`・`個人差の有無` | `状態`・`検証の記録`・`判断が変わりうる条件`（`個人差の有無` はさらに `選手あたりの件数`・`検出力`） |
| `TERM` | `knowledge/domain/vocabulary.md` | `用語` | `避ける言い方` |
| `METH` | `knowledge/methodology.md` | `方法論` | — |
| `COMMIT` | `knowledge/commitments.md` | `取り決め` | `適用範囲` |
| `SRC-KJ` | `knowledge/sources/keirin-jp.md` | `規則・事実`・`データで確かめた事実` | — |
| `SRC-OP` | `knowledge/sources/oddspark.md` | 同上 | — |
| `DATA-KD` | `knowledge/data/keirin-data.md` | 同上 | — |
| `FIELD` | `knowledge/field-dictionary.md` | 同上 | `ファンの言葉`・`項目`・`測っているもの`・`区別できないこと`・`照合` |
| `BLIND` | `knowledge/blind-spots.md` | 同上 | `歪みの向き` |

- 共通の必須キー: `内容`・`種別`・`根拠`・`成立期間`・`出典`・`最終確認日`・`依存`。
- `状態`（LAW）は `確認済み`・`否定`。`照合` は `照合済み`・`未照合`。
- 出典の検査は、この Task では「空でないこと」だけ（書式は Task 3）。

- [ ] **Step 1: リポジトリを作る**

```bash
mkdir -p ~/github/keirin-platform/keirin-research && cd ~/github/keirin-platform/keirin-research
git init -b main
```
LICENSE（MIT、`Copyright (c) 2026 shimomo`）、`.gitignore`（`__pycache__/`、`evals/results/*.json`）、README.md（1行の説明だけ）を置き、署名つきで初期コミット `chore: initial commit` を main に作る。そのあと `git switch -c feat/v1`。以降のコミットはすべて feat/v1 に積む。

- [ ] **Step 2: 移植したテストを、上の表に合わせて書き換える**（`tests/test_kb.py`）。少なくとも次を含める:
  - `test_law_requires_state_record_and_conditions`: `## LAW-001 x` に共通キーだけ → `必須キー「状態」`、`必須キー「検証の記録」`、`必須キー「判断が変わりうる条件」` の3件のエラー。
  - `test_individual_law_requires_counts_and_power`: 種別 `個人差の有無` で `選手あたりの件数` と `検出力` がない → その2件のエラー。`全体の傾向` なら出ない。
  - `test_law_state_choices`: `状態: 未検証` → `状態「未検証」は不正（確認済み・否定 のどれか）`。
  - `test_prefix_file_mismatch`: `LAW-001` を `knowledge/domain/rules.md` に置く → `LAW の知識は knowledge/domain/laws.md に置く`。
  - 移植元の、ID の重複・依存の欠落・日付・サイズ・避ける言い方・`dependents`・`next-id` のテスト。
- [ ] **Step 3: 実行して失敗を確かめる** — Run: `python3 -m unittest discover -s tests -v` / Expected: FAIL（`kb` がない、または表が違う）
- [ ] **Step 4: `tools/kb.py` を移植して表を差し替える**。docstring と CLI の説明は「keirin-research の知識の検査と問い合わせ」にする。
- [ ] **Step 5: `knowledge/README.md` を書く**。移植元の README を土台に、上の表、キー、種別、状態、出典の書き方（Global Constraints）、避ける言い方、大きさの上限、公開してよいものの範囲を書く。この README 自身にも Task 3 の公開の検査がかかるので、禁止の対象をそのままの文字列で書かない（例: 「keirin.jp の内部 API の種類の番号」と書く）。
- [ ] **Step 6: テストが通ることを確かめる** — Run: `python3 -m unittest discover -s tests -v` / Expected: OK
- [ ] **Step 7: コミット** — `feat(kb): port the knowledge checker for keirin`

---

### Task 3: kb.py に競輪向けの検査を足す（候補、出典、公開の範囲、観察記録）

**Files:**
- Modify: `keirin-research/tools/kb.py`
- Modify: `keirin-research/tests/test_kb.py`
- Modify: `keirin-research/knowledge/README.md`（候補と観察記録の書式を足す）

**Interfaces:**
- Consumes: Task 2 の `Entry`、`parse_file`、`load`、`check`
- Produces:
  - `CANDIDATES_FILE = "candidates.md"`、`load_candidates(root: Path) -> list[Entry]`（接頭辞 `CAND`）
  - `CANDIDATE_KEYS = ("内容", "文献の根拠", "保留の理由", "前回の試算", "判断に要るデータ", "見直しの時期", "最終確認日", "関係する知識")`
  - `OBS_KEYS = ("日付", "場", "レース", "内容", "関係する知識", "記録日")`、`check_observations(path: Path, known_ids: set[str]) -> list[str]`
  - `PUBLIC_PATTERNS: dict[str, str]`（説明 → 正規表現）、`public_scan_files(root: Path) -> list[Path]`
  - `next_id(…, "CAND")` は candidates.md から次の ID を出す。CLI の `next-id CAND` も同じ。
  - `check(root: Path, observations: Path | None = None) -> list[str]`。CLI は `python3 tools/kb.py check [--root DIR] [--observations PATH]`。
  - 避ける言い方の検査の対象に `candidates.md` を足す（移植元は knowledge と skills だけ。evals は採点に禁止語の正規表現そのものを書くので対象にしない）。

決まり:
- `candidates.md` はリポジトリの根に置く。`CAND-<3桁>` 以外の ID や、`CANDIDATE_KEYS` の欠けはエラー。`最終確認日` は日付。`関係する知識` は `なし` か、knowledge の ID の並び（実在すること）。
- knowledge の `依存` に `CAND-` の ID があればエラー: `依存「CAND-001」は候補（候補には依存できない）`。
- 出典の各項目は Global Constraints の4つの形のどれか。`keirin-platform:` の形はコミット（16進7〜40桁）が必須。`keirin-data:observations.md OBS-NNN` は形だけを見て、keirin-data は読まない。
- 公開の検査は、`knowledge/**/*.md`、`candidates.md`、`skills/**/*.md`、`evals/**/*.md`、`AGENTS.md`、`CLAUDE.md`、`README.md` の各行に、次の正規表現が当たればエラー（`<パス>:<行>: 公開しないもの（<説明>）「<当たった文字列>」`）:
  - `選手登録番号に見える6桁の数字`: `(?<![0-9A-Za-z])[0-9]{6}(?![0-9A-Za-z])`
  - `ビューアの URL`: `streamlit\.app`
  - `内部 API の場所`: `/pc/json`
  - `内部 API の種類の番号`: `JSJ[0-9]{3}`
- `check(root, observations=path)` のときだけ、`check_observations` で観察記録を検査する。OBS の ID の形、重複、`OBS_KEYS`、`日付`・`記録日` の日付、`関係する知識` の ID（knowledge か CAND に実在、または `なし`）。

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_kb.py`）
  - `test_candidates_are_checked`: `candidates.md` に `## CAND-001 x` だけ → `必須キー「文献の根拠」` など8件。
  - `test_knowledge_cannot_depend_on_candidate`: knowledge の `依存: CAND-001` → 上のエラー文。
  - `test_candidate_related_knowledge_must_exist`: `関係する知識: LAW-009`（ない）→ `関係する知識「LAW-009」が見つからない`。
  - `test_platform_source_requires_commit`: `出典: keirin-platform:CLAUDE.md 分析するときの前提` → エラー。`keirin-platform:CLAUDE.md@0850049 分析するときの前提` → 通る。
  - `test_source_forms`: `オーナー（2026-11-01 の会話）`、`https://keirin.jp/pc/static/beginner/basics/lines.html 入門`、`keirin-data:observations.md OBS-001` が通る。`オーナー（2026-13-01 の会話）` と `ユーザー（2026-11-01 の会話）` はエラー。
  - `test_observation_source_does_not_need_keirin_data`: 根に keirin-data がない状態で、出典 `keirin-data:observations.md OBS-001` の知識だけ → `check(root) == []`。
  - `test_public_scan_flags_registration_numbers_and_urls`: knowledge の本文に `014714`、`https://keirin.streamlit.app/`、`/pc/json`、`JSJ002` を1行ずつ → 4件のエラー。
  - `test_public_scan_ignores_dates_counts_and_commits`: `2026-10-05`、`23,456 レース`、`@0850049`、`@3f2a9c1`、`1234567` → エラーなし。
  - `test_public_scan_covers_skills_and_evals`: `skills/x/SKILL.md` と `evals/t1/prompt.md` に `014714` → それぞれエラー。
  - `test_observations_checked_only_when_given`: 壊れた observations.md を `check(root)` に渡さない → 影響なし。`check(root, observations=path)` → `必須キー「記録日」` など。
  - `test_next_id_for_candidates`: CAND-001・CAND-003 がある → `CAND-004`。
- [ ] **Step 2: 実行して失敗を確かめる** — Run: `python3 -m unittest tests.test_kb -v` / Expected: 上のテストが FAIL
- [ ] **Step 3: kb.py に実装する**（上の Interfaces と決まりのとおり）
- [ ] **Step 4: テストが通ることを確かめる** — Run: `python3 -m unittest discover -s tests -v` / Expected: OK
- [ ] **Step 5: `knowledge/README.md` に、候補（candidates.md）と観察記録（keirin-data の observations.md）の書式を足す。** Run: `python3 tools/kb.py check` / Expected: `OK`
- [ ] **Step 6: コミット** — `feat(kb): check candidates, sources, public scope and observations`

---

### Task 4: 知識の最初の中身（1）ドメイン知識・取り決め・方法論

**Files:**
- Create: `knowledge/domain/rules.md`、`knowledge/domain/mechanisms.md`、`knowledge/domain/laws.md`（見出しと説明だけ。法則は Task 10 で入れる）、`knowledge/domain/vocabulary.md`、`knowledge/commitments.md`、`knowledge/methodology.md`
- Create: `tests/test_seed.py`

**Interfaces:**
- Consumes: Task 2〜3 の書式と `kb.load`
- Produces: 下の ID（Task 6 のスキルと Task 7 の評価が引く）

書き方: 各ファイルの冒頭に、何を置くファイルかを2〜3行で書く。値は spec §8 の「抜き出すときの決まり」に従い、新しい値を採り、データの数値には期間と件数を付ける。出典の keirin-platform のコミットは `0850049`（v1 で書き換わる前の main）。`最終確認日` は書いた日。
表の「設計書」は `keirin-platform:docs/superpowers/specs/2026-11-01-keirin-research-design.md@<Task 1 のマージ後の main のコミット> <節>` のこと。設計者の判断から来たもの（オーナーは設計書の承認で了承）は、オーナーの会話ではなく設計書を出典にする。

| ID | 題 | 中身の要点 | 出典 |
|---|---|---|---|
| RULE-001 | 級班と種別 | S級（SS・S1・S2）、A級（A1・A2・A3＝チャレンジ）、L級（ガールズ）。S1↔S2、A1↔A2 は同じ番組を走る | keirin-platform:docs/score-correction.md@0850049 §1、業務方法規程の URL（同 §1 の出典） |
| RULE-002 | 競走得点の窓と期替わり | 窓は当月＋前3ヶ月の得点対象レース。昇降級は1月・7月の期替わり | keirin-platform:docs/score-correction.md@0850049 §1・§3 |
| RULE-003 | 昇降級の換算（Δ） | 種別 `データで確かめた事実`。A12→S +6.18、S→A12 −5.37、A3→A12 +9.13、A12→A3 −9.38。2026-01 と 2026-07 の期替わりを誤差の逆数の二乗で合わせた。成立期間は 2026-06 の点数表の改定後。期替わりごとに推定し直す。依存 RULE-001、RULE-002 | keirin-platform:docs/score-correction.md@0850049 §3 |
| RULE-004 | ミッドナイトの車番は得点順 | 種別 `データで確かめた事実`。競走得点の高い順に1番車から並ぶ。導入は 2020-10 説と 2021-10 説がある。数値は Task 10 で今のデータに更新するまで、元の値（96.2%、1番車は 100% 得点1位）と、その集計期間を書く | keirin-platform:CLAUDE.md@0850049 分析するときの前提、keirin-platform:docs/keirin-theories.md@0850049 前提、オーナー（2026-10-03 の会話） |
| RULE-005 | 4・6・8番車の慣例 | 種別 `データで確かめた事実`。4・6・8番車には、同じ枠の相方（5・7・9番車）より実力が劣る選手を置く（枠連の釣り合いのためとみられる）。9車の 4<5・6<7・8<9、7車の 6<7 の割合（Task 10 で更新） | RULE-004 と同じ |
| RULE-006 | 車立て | 7車立てと9車立て。2020年後期から7車が主流。枠連は9車だけ | keirin-platform:docs/keirin-theories.md@0850049 前提（遠山030 の URL） |
| RULE-007 | 決まり手と B・H・S | 決まり手は1・2着の選手に付く（逃げ・捲り・差し・マーク）。B は最終周回のバックを先頭で通過、H はホームを先頭で通過、S はスタートで先頭を取った回数 | keirin-platform:docs/rider-traits.md@0850049、keirin-platform:docs/data-schema.md@0850049 entries |
| RULE-008 | 払戻率 | 約75%。回収率は75%前後が基準になる | keirin-platform:docs/keirin-theories.md@0850049 そのほかの注意（公式の根拠が見つかれば URL を足す） |
| RULE-009 | ガールズにはラインがない | L級は全員が単騎として走る。横の動きに制限がある | keirin-platform:docs/prediction.md@0850049 モデル・試したこと |
| MECH-001 | ライン | 同じ地区・府県の選手が隊列を組む。先頭（自力で風を受ける）、番手（先頭の後ろ。差し、番手捲り）、3番手以降、単騎。並びは選手のコメントからレース前に予想として出る。競り（番手を2人で争う）。地区のまとまり（近い地区の連携） | https://keirin.jp/pc/static/beginner/basics/lines.html 入門、keirin-platform:docs/lines.md@0850049 |
| MECH-002 | 主導権と決まり方 | 先行（逃げ）、捲り（後方から外を一気に）、差し（直線で前を抜く）、マーク、番手捲り。主導権を取ったラインの番手は差しやすく、捲られたラインは共倒れしやすい（傾向そのものの確かさは LAW と候補で扱う）。依存 MECH-001 | 同上、keirin-platform:docs/rider-traits.md@0850049 |
| MECH-003 | 番組編成と実力の配置 | 車番には実力の情報が入っている（RULE-004、RULE-005）。「1番車有利」「4・6・8番車は来ない」は位置の効果と実力の配置の区別がつかない。依存 RULE-004、RULE-005 | keirin-platform:CLAUDE.md@0850049 分析するときの前提 |
| TERM-001 | 競輪の言い方 | 「1番車」「車番」「9車立て」「車身」。避ける言い方: `号車、試走、ハンデ、騎手、/[0-9０-９一二三四五六七八九]頭/、頭立て、出走馬、馬券、馬場、斤量、馬身` | 設計書 §5 |
| TERM-002 | 連対率と3連対率 | 勝率＝1着の割合、2連対率＝2着以内の割合、3連対率＝3着以内の割合。出走表の値（`win_rate` など）は期間が決まった集計で、自分で数えた率と同じではない。避ける言い方 `なし` | keirin-platform:docs/data-schema.md@0850049 entries |
| TERM-003 | 並び・脚質・戦法 | 並び＝ラインの構成（レース前の予想）。脚質（逃・両・追）は keirin.jp の出走表の分類。オッズパークの戦法（逃捲・追込・自在）とは別の分類。避ける言い方 `なし` | keirin-platform:docs/lines.md@0850049 |
| COMMIT-001 | 予想は確率で出す | 予想は各選手の1着・2着以内・3着以内の確率で出す。車券や回収率では出さない。適用範囲: 予想を扱うすべての作業 | オーナー（2026-10-05 の会話）、keirin-platform:CLAUDE.md@0850049 オーナーの要望 8. |
| COMMIT-002 | 登録・否定・保留の基準 | Global Constraints の基準（95% 区間、ブートストラップ 2,000 回、前半と後半、実質的な大きさ 0.05 組・0.03）。否定も登録する。判断がつかなければ候補（candidates.md）に置く。実質的な大きさは測る前に決めて、法則の「検証の記録」に書く。適用範囲: keirin-research | オーナー（2026-11-01 の会話）、設計書 §7.3 |
| COMMIT-003 | 封印期間を設けず、確かめ直す | 封印期間は設けない。登録後に入ってきたデータで、期替わりのあと（4月・10月の下旬）と本物の並びが1年分そろったときに確かめ直す。基準を満たさなくなったら候補に戻し、`kb.py dependents` で依存を洗い出す。適用範囲: keirin-research | オーナー（2026-11-01 の会話）、設計書 §7.3 |
| COMMIT-004 | 公開してよいもの | Global Constraints の「置かないもの」と、置いてよいもの（集計した統計、スキーマ）。個別のレースは最小限で出典「KEIRIN.JP」。観察記録は keirin-data に置く。適用範囲: keirin-research | オーナー（2026-11-01 の会話）、設計書 §6 |
| COMMIT-005 | 個人差は有無だけ | 個人差の知見は「存在するかどうか」だけを登録し、選手の名前を出さない。誰がどうかは keirin-data の watchlist.toml と keirin-platform の docs/rider-traits.md の側。適用範囲: keirin-research | オーナー（2026-11-01 の会話） |
| METH-001 | 有意でないことは差がないことではない | 検出力（選手あたりの件数など）を書く。例: 雨・風の個人差（CAND で保留） | keirin-platform:CLAUDE.md@0850049 オーナーの要望 10. |
| METH-002 | 比べる前に実力をそろえる | 得点（または得点順位）で層を分けるか、得点で期待値を出して差を見る | keirin-platform:docs/keirin-theories.md@0850049 前提 |
| METH-003 | 構成をそろえる | 種目（予選・決勝など）、車立て、時間帯、級班、グレード、開催の何日目かの構成。例: 初日の逃げ切りの多さは予選の多さで説明できる | keirin-platform:docs/keirin-theories.md@0850049 3. 選手の属性 |
| METH-004 | 少ない件数は縮小推定で扱う | 選手ごとの率は経験ベイズ（ベータ二項）で全体に寄せ、件数で足切りし、区間も出す | keirin-platform:docs/rider-traits.md@0850049 少ない件数への対処 |
| METH-005 | その時点より前のデータだけを使う | 選手の性質の判定や予想の学習に、評価する日以降のデータを入れない。過去の日の予想をその日を含めて学習したものは参考値 | keirin-platform:docs/prediction.md@0850049 ビューア・評価 |
| METH-006 | 同じレースの着順は独立でない | 着順は同じレースの選手どうしで制約し合い、同じラインの選手は連動しうる。選手を1人ずつ独立に扱う率や予想は、ラインの上位独占を表せない | keirin-platform:docs/prediction.md@0850049 試作と検証 |
| METH-007 | 個人差の検定 | 実力（得点順位と出走数）で決まる期待との差を選手ごとに出し、条件のラベルをレースの間で入れ替える並べ替え検定で偶然の幅と比べる | keirin-platform:CLAUDE.md@0850049 オーナーの要望 10. |
| METH-008 | 同じデータで通説を次々に確かめるとき | 偶然の当たりを避けるため、確認には前半と後半の再現を求める（COMMIT-002）。依存 COMMIT-002 | 設計書 §7.3 |

`knowledge/domain/laws.md` は冒頭の説明だけにする（法則は「自分たちのデータで確かめた傾向」。状態は確認済みか否定）。

- [ ] **Step 1: 失敗する seed テストを書く**（`tests/test_seed.py`。移植元の `matching()` と同じ書き方）
  - `test_vocab_regex_matches_other_sport_terms`: `kb.vocab_regex` が `1号車`・`試走`・`ハンデ`・`騎手`・`9頭`・`頭立て`・`馬券`・`馬身`・`斤量` に当たる。
  - `test_vocab_regex_has_no_false_positives`: `先頭`・`番手`・`1番車`・`9車立て`・`車身`・`頭で買う`・`冒頭` に当たらない。
  - `test_midnight_rule`: RULE で `内容` に `ミッドナイト`・`得点`・`1番車` を含むものがある。
  - `test_line_mechanism`: MECH で `内容` に `番手`・`単騎`・`競り` を含むものがある。
  - `test_probability_commitment`: COMMIT で `内容` に `1着`・`3着以内`・`車券` を含み、`適用範囲` に `予想` を含むものがある。
  - `test_registration_criteria`: COMMIT で `内容` に `95%`・`前半`・`0.05`・`0.03` を含むものがある。
- [ ] **Step 2: 実行して失敗を確かめる** — Run: `python3 -m unittest tests.test_seed -v` / Expected: FAIL
- [ ] **Step 3: 上の表のとおりに6つのファイルを書く**（元の docs を読んで、要点を正確に写す）
- [ ] **Step 4: 検査とテスト** — Run: `python3 tools/kb.py check && python3 -m unittest discover -s tests -v` / Expected: `OK` と OK
- [ ] **Step 5: コミット** — `docs(knowledge): add domain rules, mechanisms, vocabulary, commitments and methods`

---

### Task 5: 知識の最初の中身（2）取得元・データ・対応表・見えないもの・候補

**Files:**
- Create: `knowledge/sources/keirin-jp.md`、`knowledge/sources/oddspark.md`、`knowledge/data/keirin-data.md`、`knowledge/field-dictionary.md`、`knowledge/blind-spots.md`、`candidates.md`
- Modify: `tests/test_seed.py`

**Interfaces:**
- Consumes: Task 4 の ID
- Produces: 下の ID と CAND の一覧

| ID | 題 | 中身の要点 | 出典 |
|---|---|---|---|
| SRC-KJ-001 | 遡れる範囲 | 2015年まで遡れる | keirin-platform:CLAUDE.md@0850049 決定ログ 2026-10-02 |
| SRC-KJ-002 | 並びは予想で、レース後は空 | 並びはレース前の予想で、レース後は空になる。当日の深夜の時点で、遅い時間帯の開催はまだ空のことがある | keirin-platform:docs/lines.md@0850049 |
| SRC-KJ-003 | 周回ごとの位置は出ない | 一般のレースでは、打鐘・ホーム・バックの位置が出ない。レース中の動きは決まり手と B・H で推すしかない | keirin-platform:docs/rider-traits.md@0850049 使えるデータ |
| SRC-KJ-004 | 利用条件 | 複製は私的使用と引用の範囲。記事は集計だけ、出典「KEIRIN.JP」 | keirin-platform:CLAUDE.md@0850049 決定ログ 2026-10-02 |
| SRC-OP-001 | 過去分の並び | 過去のレースの並びが取れる。間隔は10秒以上。2026-10-24 から Actions の IP に 403 が返り、取得は止まっている | keirin-platform:docs/lines.md@0850049、keirin-platform:CLAUDE.md@0850049 現状と TODO |
| SRC-OP-002 | 引用も禁止 | 並び予想や短評の文言は載せない。集計した値だけ | keirin-platform:docs/lines.md@0850049 利用条件 |
| DATA-KD-001 | 表と列 | meetings・races・entries・payouts・lines の表と主な列（docs/data-schema.md の要約。キーは date＋venue_code＋race_no(＋car_no)） | keirin-platform:docs/data-schema.md@0850049 |
| DATA-KD-002 | 収集の期間 | 2024-01-01 まで遡っている途中（2026-12 下旬に完了の見込み）。それより前は持っていない。期間を決める前に、実際にある日を確かめる | keirin-platform:CLAUDE.md@0850049 構成・決定ログ 2026-10-25 |
| DATA-KD-003 | lines 表は本物の並びだけ | lines 表は keirin.jp（2026-10-05 以降）とオッズパークで取れた分だけ。府県から推定した並びは表に入らず、予想の学習のときだけ作る（規則と、14レースでの位置の一致 82%） | keirin-platform:docs/lines.md@0850049、keirin-platform:docs/prediction.md@0850049 モデル |
| DATA-KD-004 | 着順の列 | `finish` は元の文字列（失格なども入る）、`finish_pos` は数値にできたものだけ。同着は車番の順に並べて扱う。欠場は `notes` | keirin-platform:docs/data-schema.md@0850049、keirin-platform:docs/prediction.md@0850049 |
| DATA-KD-005 | 時間帯の列 | `time_slot` は推定の対応表から作った値（day・night・midnight・morning） | keirin-platform:docs/data-schema.md@0850049 meetings |
| DATA-KD-006 | 人気と払戻 | 払戻の人気は的中した組番の人気順。選手ごとのオッズは持っていない | keirin-platform:docs/keirin-theories.md@0850049 そのほかの注意 |
| DATA-KD-007 | 天候と風 | レースごとの weather・wind_speed。風速はほとんどが 0〜2m/s で、強風は少ない | keirin-platform:CLAUDE.md@0850049 オーナーの要望 10. |
| FIELD-001 | 先行 | ファンの言葉「先行」。項目: 脚質 `style`=逃、決まり手 `kimarite`=逃げ、`bh` の H。脚質は出走表の分類、決まり手は1・2着だけ、H は最終ホームの先頭。区別できないこと: 逃げて3着以下に沈んだ選手（決まり手が付かない）。照合: 未照合 | DATA-KD-001 と同じ |
| FIELD-002 | 番手 | 項目: lines の `line_pos`=2。レース前の予想の位置で、実際の隊列ではない。競り（`contested`）。照合: 未照合 | keirin-platform:docs/data-schema.md@0850049 lines |
| FIELD-003 | 捲り | 項目: `kimarite`=捲り（1・2着だけ）、`bh` の B。捲って3着以下は区別できない。照合: 未照合 | 同上 |
| FIELD-004 | 連対率 | 項目: 出走表の `top2_rate`（期間の決まった集計）と、自分で数えた2着以内の割合。依存 TERM-002。照合: 照合済み | keirin-platform:docs/data-schema.md@0850049 entries |
| FIELD-005 | 強さ | 項目: `score`（競走得点）。級班をまたぐと尺度が違い、窓に前の級班の成績が入る。依存 RULE-002、RULE-003。照合: 照合済み | keirin-platform:docs/score-correction.md@0850049 |
| BLIND-001 | 周回ごとの位置 | 打鐘・ホーム・バックの隊列が分からない。歪みの向き: 主導権や展開の効果を、決まり手と B・H だけで測ると、3着以下の動きを取りこぼす | SRC-KJ-003 と同じ |
| BLIND-002 | 実際の並び | 並びは予想で、実際は単騎が付く、ラインが崩れる。歪みの向き: ラインの効果を薄める（予想と実際がずれた分、効果が小さく出る） | keirin-platform:docs/rider-traits.md@0850049 |
| BLIND-003 | レース中の動き | 仕掛けのタイミング、横の動き、ブロック。歪みの向き: 選手の「クセ」を結果だけで判定すると、展開の偶然と区別できない | keirin-platform:docs/rider-traits.md@0850049 |
| BLIND-004 | 選手のコメントと調子 | コメントは当日分しか取れず、集めていない。歪みの向き: 調子や作戦の違いが、実力や個人差に混ざる | keirin-platform:CLAUDE.md@0850049 現状と TODO |
| BLIND-005 | 選手ごとのオッズ | 持っていない。歪みの向き: 人気の割れ方や「1番人気の選手」を扱う問いは判定できない | keirin-platform:docs/keirin-theories.md@0850049 6. 配当・人気 |

`candidates.md`: 冒頭に「判断がつかず保留にしたもの。結論の根拠に使わない。設計の段階で、確かめるべき要因の候補として読む」と書く。docs/keirin-theories.md（@0850049）の 1.〜6. の表の通説を、1行の通説につき1件の CAND にする（「前提」の節は RULE-004・RULE-005・MECH-003 に入れたので除く）。各件の `文献の根拠` は元の表の「文献の根拠」と URL、`前回の試算` は「私たちのデータ（22日分）」の値（なければ `なし`）、`保留の理由` は「22日分の簡易集計で、COMMIT-002 の検証をしていない」など、`判断に要るデータ`・`見直しの時期` は元の「検証の優先順位」と「今後」から書く。バンクの諸元の表は、CAND「333mは先行有利、500mは差し有利」の `判断に要るデータ` に「場ごとの周長・みなし直線（出典 netkeiba の全国バンクデータ）」として要約し、表そのものは写さない。

これに加えて、次の CAND を置く:
- 雨・強風が苦手な選手がいるか（個人差）。前回の試算: 2025-12-09〜2026-10-22、約2.4万レース（雨 約2,600、風速3m/s 以上 約1,200）、並べ替え検定で雨 p=0.65、風 p=0.32、選手あたりの雨は十数走で検出力不足。見直しの時期: 2024-01 までの遡りが終わる 2026-12 下旬。関係する知識: METH-001、METH-007、DATA-KD-007。
- ラインの連動（Task 10 で LAW-001 にできたら、この CAND は消す）。

- [ ] **Step 1: 失敗する seed テストを足す**
  - `test_lines_table_period`: DATA-KD で `内容` に `2026-10-05` と `推定` を含むものがある。
  - `test_blind_spot_of_laps`: BLIND で `内容` に `周回` を含むものがある。
  - `test_weather_candidate`: `kb.load_candidates` に `内容` が `雨` を含み、`見直しの時期` が `2026-12` を含むものがある。
  - `test_line_linkage_is_somewhere`: LAW か CAND の `内容` に `ライン` と `連動` を含むものがある。
- [ ] **Step 2: 実行して失敗を確かめる** — Run: `python3 -m unittest tests.test_seed -v` / Expected: 上の4件が FAIL
- [ ] **Step 3: 6つのファイルを書く**
- [ ] **Step 4: 検査とテスト** — Run: `python3 tools/kb.py check && python3 -m unittest discover -s tests -v` / Expected: `OK` と OK
- [ ] **Step 5: コミット** — `docs(knowledge): add sources, data notes, field dictionary, blind spots and candidates`

---

### Task 6: スキル5つと入口（AGENTS.md、CLAUDE.md、プラグイン宣言、README）

**Files:**
- Create: `skills/keirin-knowledge/SKILL.md`、`skills/designing-keirin-analysis/SKILL.md`、`skills/concluding-keirin-analysis/SKILL.md`、`skills/handling-keirin-observation-conflicts/SKILL.md`、`skills/registering-keirin-knowledge/SKILL.md`
- Create: `AGENTS.md`、`CLAUDE.md`、`.claude-plugin/plugin.json`
- Modify: `README.md`
- Create: `tests/test_skills.py`

**Interfaces:**
- Consumes: Task 4〜5 の知識のファイルと ID
- Produces: スキル名（Task 7 の評価が `Skill` の発火を見る）

各スキルの front matter（`name` と `description` はこのとおり）:

| name | description |
|---|---|
| `keirin-knowledge` | 競輪のデータ・選手・レース・ライン・予想・分析・記事について話すとき、または競輪のデータを扱うコードや SQL を書くときに使う。 |
| `designing-keirin-analysis` | 競輪について何かを調べる・確かめる・集計する、指標や特徴を作る、予想を組み立てる、レースや選手を評価する作業を始める前に使う。 |
| `concluding-keirin-analysis` | 競輪の集計・検証の結果から結論や解釈を書くとき、まとめを書くとき、過去の結論を引用するときに使う。 |
| `handling-keirin-observation-conflicts` | 競輪で、ユーザーが映像や現地で見たことが、データの集計や AI の答えと食い違うと言ったときに使う。 |
| `registering-keirin-knowledge` | 競輪について新しい事実や法則が確かめられたとき、ユーザーから新しい知識を聞いたとき、映像での観察を残すとき、知識の誤りや訂正が分かったときに使う。 |

本文は autorace-research の同名のスキル（`~/github/autorace/autorace-research/skills/`）の構成を下敷きにし、spec §7.4 の手順を書く。事実はスキルに書かず、ファイルと ID で引く。冒頭に「知識のファイルは、このスキルのディレクトリから見て `../../knowledge/` にある（リポジトリの根の knowledge/）」と書く。競輪向けに変えるところ:

- `keirin-knowledge`: 姿勢（データは唯一の材料だが絶対ではない、ユーザーの観察は正当な根拠、候補は結論に使わない）。最初に読むもの（`../../knowledge/commitments.md`、`../../knowledge/domain/vocabulary.md`）。知識の地図（全ファイルと `../../candidates.md`）。段階ごとのスキルの表。
- `designing-keirin-analysis`: 設計メモの7項目。2. の要因は、ライン（位置・人数・単騎・競り）、主導権、車番の配置（RULE-004・RULE-005・MECH-003）、得点の尺度（RULE-002・RULE-003）、構成（METH-003）、バンク、候補リストの関係する CAND を1つずつ当てはめる。3. は並びが本物か推定か（DATA-KD-003）。5. は COMMIT-001（予想なら確率）。6. は COMMIT-002 の基準と実質的な大きさを測る前に書く、METH-005、METH-006、個人差なら METH-004・METH-007 と検出力。「1つのレース・1人の選手を評価するとき」と「軽い問いのとき」の短い道（封印期間の記述は持ち込まない）。
- `concluding-keirin-analysis`: 方法論で見直す。有意でなければ検出力（METH-001）。見えないものを添える。COMMIT-002 で登録・否定・保留を判断し、registering-keirin-knowledge を提案する。過去の結論を使うときは期間と件数まで遡る。候補は根拠にしない。
- `handling-keirin-observation-conflicts`: 反論しない。BLIND と FIELD を疑う。判定できなければ、観察を `keirin-data` の observations.md（手元では keirin-research の隣の `../keirin-data/observations.md`）に PR で残すことを提案する。一般化できる知見は registering へ。
- `registering-keirin-knowledge`: 下書きを見せて承認を得てから書く。法則は COMMIT-002 で状態を決め、`検証の記録` にスクリプトのパスとコミット、期間、件数、実質的な大きさを書く。保留は CAND に。名前・登録番号・レースを特定できる記述を入れない（COMMIT-004・COMMIT-005）。`python3 tools/kb.py next-id <PREFIX>`、`python3 tools/kb.py check`、観察記録を変えたら `python3 tools/kb.py check --observations ../keirin-data/observations.md`。訂正したら `python3 tools/kb.py dependents <ID>` で洗い出す。PR で進める。

`AGENTS.md`: リポジトリの目的（spec §1 の3行要約）、構成、知識の地図（keirin-knowledge と同じ表）、スキルが使えない環境では `skills/keirin-knowledge/SKILL.md` から読むこと、知識の足し方（registering-keirin-knowledge と `knowledge/README.md`）、公開してよいものの範囲（COMMIT-004 を指す）、コマンド（`python3 tools/kb.py check`、`python3 -m unittest discover -s tests -v`）、開発ルール（Global Constraints の PR・署名・コミットメッセージ）。`CLAUDE.md` は `@AGENTS.md` の1行だけ。

`.claude-plugin/plugin.json`:

```json
{
  "name": "keirin-research",
  "version": "0.1.0",
  "description": "競輪の検証と予想で、ドメイン知識と方法論を共通認識として使うためのプラグイン",
  "author": { "name": "shimomo" }
}
```

README: 目的、Claude Code での導入（`ln -s ~/github/keirin-platform/keirin-research ~/.claude/skills/keirin-research`、`claude plugin list` で `keirin-research@skills-dir`）、Codex での導入（Task 11 で確かめた内容を書く。それまでは見出しだけ）、構成、検査と評価のコマンド。

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_skills.py`）
  - `test_frontmatter_name_matches_directory`: 5つとも `name` がディレクトリ名と同じ。
  - `test_descriptions_name_keirin_only`: `description` に `競輪` を含み、`オートレース` を含まない。
  - `test_no_plugin_root_variable`: どの SKILL.md にも `${CLAUDE_PLUGIN_ROOT}` がない。
  - `test_knowledge_paths_are_relative_and_exist`: `knowledge/` を含む行はすべて `../../knowledge/` の形で、指すファイル（またはディレクトリ）が実在する。`../../candidates.md` も実在する。
  - `test_designing_lists_keirin_factors`: designing の設計メモに `ライン`・`車番`・`得点`・`構成`・`COMMIT-001`・`COMMIT-002` がある。
  - `test_registering_keeps_names_out`: registering に `kb.py check`・`名前`・`COMMIT-004` がある。
  - `test_conflicts_point_to_keirin_data`: handling に `keirin-data` と `observations.md` がある。
  - `test_claude_md_imports_agents`: `CLAUDE.md` の中身が `@AGENTS.md` だけ。
- [ ] **Step 2: 実行して失敗を確かめる** — Run: `python3 -m unittest tests.test_skills -v` / Expected: FAIL
- [ ] **Step 3: 5つのスキルと AGENTS.md・CLAUDE.md・plugin.json・README を書く**
- [ ] **Step 4: 検査とテスト** — Run: `python3 tools/kb.py check && python3 -m unittest discover -s tests -v && claude plugin validate .` / Expected: `OK`、OK、validate が通る
- [ ] **Step 5: コミット** — `feat(skills): add the five keirin skills and the AGENTS.md entry point`

---

### Task 7: 評価ケース T1〜T8 と CI

**Files:**
- Create: `evals/t01-line-linkage/`〜`evals/t08-register/`（各 `prompt.md` と `graders/*.md`）、`evals/results/.gitkeep`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 6 のスキル名
- Produces: `claude plugin eval` で回せる評価ケース

書式は autorace-research の `evals/t01-vehicle-class/` と同じ（prompt.md の front matter: `description`、`tags`、`max_turns: 20`、`timeout_seconds: 600`、`allowed_tools: [Read, Glob, Grep, Skill]`。採点は `type: llm`・`regex`・`tool_used`）。どのケースにも次の2つの採点を置く:
- `skill-fired.md`: `type: tool_used`、`tool: Skill`
- `no-other-sport-terms.md`: `type: regex`、`match: not_contains`、`pattern: "号車|試走|ハンデ|騎手|[0-9０-９一二三四五六七八九]頭|頭立て|出走馬|馬券|馬場|斤量|馬身"`

| ケース | 依頼文（prompt.md の本文） | llm の採点（weight 2 は要の基準） |
|---|---|---|
| t01-line-linkage | 各選手の過去の成績から、次のレースで各選手が1着になる確率と3着以内に入る確率を出したいです。どう組み立てますか？（この環境にデータはないので、進め方を具体的に） | (2) 連対率などの選手ごとの成績だけで組み立てず、ライン（位置・人数・単騎）を要因に挙げる。(2) 同じラインの選手の着順は独立でなく連動することに触れる。(1) 車券や回収率ではなく確率として出す |
| t02-car-number | 1番車が有利かどうか調べたいです。どう進めますか？ | (2) ミッドナイトは得点順に1番車から並ぶことに触れる。(1) 4・6・8番車の慣例に触れる。(2) 得点と時間帯をそろえて比べることを提案する |
| t03-score-scale | S級に上がったばかりの選手と、もともと S級の選手の強さを、競走得点で比べたいです。注意点は？ | (2) 窓（当月＋前3ヶ月）に前の級班の成績が入っていることに触れる。(1) 昇降級の換算（Δ）に触れる |
| t04-fatigue | 連日の疲労で、3日目以降は先行選手が苦しくなるのか調べたいです。どう進めますか？ | (2) 開催の日ごとの種目の構成（初日は予選が多い）をそろえることを提案する |
| t05-rain | 雨に弱い選手がいるか調べたいです。どう進めますか？ | (2) 得点などの実力で期待を出してから比べる。(2) 選手あたりの雨の走数と検出力に触れ、有意でなくても差がないとは言えないと述べる。(1) 前回の試算（保留）に触れる |
| t06-banote-makuri | 番手捲りをよくする選手を見つけたいです。どう調べますか？ | (2) 並びはレース前の予想で実際の隊列ではないことに触れる。(1) 本物の並びがある期間が限られることに触れる。(1) 周回ごとの位置がなく、B・H と決まり手で推すことに触れる |
| t07-video-conflict | （前提を本文に書く）あなたは先ほど「この選手は先行して粘れるタイプ」と答えました。それに対してユーザーが「映像で見ると、早めに捲られていた」と返しました。どう応じますか？ | (2) 反論しない。(2) データから見えないもの（実際の展開・隊列）か言葉と項目のずれを疑う。(1) 判定できなければ keirin-data の観察記録に残すことを提案する |
| t08-register | 次の検証結果を知識として登録してください: 「架空の選手A（山田 一郎）と架空の選手B（佐藤 次郎）は、雨のレースで平均着順が1.2悪かった。雨のレースは2人で9走」 | (2) 選手名を知識に書かない下書きにする。(2) 件数が少なく判断がつかないので、登録ではなく候補（保留）にすることを提案する。(1) 書き込む前に承認を求める |

`.github/workflows/ci.yml`:

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - run: python3 --version
      - run: python3 tools/kb.py check
      - run: python3 -m unittest discover -s tests -v
```

- [ ] **Step 1: 8つのケースと採点のファイルを書く。** 依頼文と採点に6桁の数字や実在の選手名を入れない（公開の検査がかかる）。
- [ ] **Step 2: ci.yml を書く**
- [ ] **Step 3: 検査** — Run: `python3 tools/kb.py check && python3 -m unittest discover -s tests -v` / Expected: `OK` と OK
- [ ] **Step 4: コミット** — `test(evals): add keirin eval cases and CI`

---

### Task 8: keirin-platform — ラインの連動の検証（`research.py`）

**Files:**
- Create: `keirin-platform/src/keirin/research.py`
- Create: `keirin-platform/tests/test_research.py`
- Modify: `keirin-platform/src/keirin/cli.py`（`line-linkage` を足す）

**Interfaces:**
- Consumes: `predict.fit`、`predict.load_races`、`predict.feature_matrix`、`predict.group_of`、`predict._scratched`、`predict._close_gaps`、`predict._utilities`、`predict.MIN_RACES`、`predict.TRAINING_DAYS`、`storage.table_path`
- Produces（Task 9 も使う）:
  - `bootstrap_ci(values: np.ndarray, *, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]`（平均の 95% 区間。レースを単位に復元抽出）
  - `decide(ci: tuple[float, float], halves: tuple[float, float], practical: float) -> str`。`"正の効果"`（区間の下限 > 0 かつ前半・後半の点推定 > 0）、`"負の効果"`（上限 < 0 かつ両方 < 0）、`"実質的な差なし"`（−practical < 下限 かつ 上限 < practical）、それ以外は `"保留"`。判定はこの順。
  - `LINKAGE_PRACTICAL = 0.05`
  - `pair_top3_probabilities(theta: np.ndarray, x: np.ndarray) -> np.ndarray`: (n, n)。i と j がともに3着以内に入る確率（得点だけのモデル）。対称、対角は0。`predict.race_probabilities` と同じ段の確率（1着 u1、2着 t2·u23、3着 t3·u23）で、上位3人の順列を数え上げる。
  - `LinkageRace(day: date, group: str, pair_prob: np.ndarray, same_line: np.ndarray, top3: np.ndarray)`（frozen dataclass。same_line は (n, n) の bool で、同じ line_no の2人。ただし同じ位置を争う2人（競り）は False。top3 は (n,) の bool）
  - `load_linkage_races(data_dir: Path, start: date, end: date, model: predict.Model) -> list[LinkageRace]`: start <= day < end。lines 表に行があるレースだけ（本物の並び）。group が model.params にあるもの（L級は除く）。欠場を除き（`_scratched`）、`_close_gaps` で位置を詰める。得点が空の選手がいるレースと、着順の付いた選手が3人未満のレースは除く。top3 は `(finish_pos, car_no)` の順で最初の3人（`predict.load_races` と同じ）。x は `predict.feature_matrix(riders, {})`。
  - `linkage_excess(race: LinkageRace) -> float`: 同じラインの組（i<j）について、Σ(両方3着以内なら1) − Σ pair_prob。
  - `placebo_excess(race: LinkageRace, rng: np.random.Generator) -> float`: 選手の並びをレースの中で並べ替えた same_line（`same_line[p][:, p]`）で同じ量を出す。
  - `linkage_summary(races: list[LinkageRace], *, n_boot: int = 2000, seed: int = 0) -> dict[str, Any]`: `{"groups": {"all"|"A"|"S": {"races": int, "pairs": int（同じラインの組の数）, "observed": float, "expected": float, "excess_per_race": float, "ci95": [float, float], "first_half": float, "second_half": float（どちらも excess_per_race）, "placebo_excess_per_race": float, "placebo_ci95": [float, float], "decision": str}}}`。前半と後半は、日付の中央値で分ける（中央値の日は前半）。decision は `decide(ci95, (first_half, second_half), LINKAGE_PRACTICAL)`。レースが1件もない群は出さない。
- CLI: `keirin line-linkage --date D --to T [--days 120] [--min-races 100] [--boot 2000] [--data-dir DIR]`。`--date` の前の `--days` 日で `predict.fit(…, columns=("score", "no_score"))` を学習し、`--date`〜`--to`（両端を含む）で集計して、`{"period", "model": {"fitted_on", "races"}, **linkage_summary(...)}` を JSON（`ensure_ascii=False, indent=2`）で標準出力に出す。選手ごとの値は出さない。

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_research.py`。表の作成は `tests/test_predict.py` の `full_card`・`write_day` と同じ形のヘルパーを、このファイルにも置く）
  - `test_pair_probabilities_match_top3`: 乱数の theta（N_PARAMS）と x（7人）で、`P` が対称、対角0、`P[np.triu_indices(7, 1)].sum() ≈ 3`、`P.sum(axis=1) ≈ 2 * race_probabilities(theta, x)[:, 2]`（abs 1e-9）。
  - `test_pair_probabilities_of_three_riders_are_one`: 3人なら対角以外がすべて 1。
  - `test_load_uses_real_lines_only`: 同じ日に、lines 表に行があるレースとないレース、L級のレースを置く → 1件だけ読まれる。
  - `test_contested_riders_are_not_line_mates`: 並び `2(13)56/74` の行（1 と 3 が line_pos 2 で contested）→ same_line で (2,1)・(2,3)・(5,6) は True、(1,3) は False、(2,5) は False（行・列は車番から引く）。
  - `test_scratched_rider_is_left_out`: 3番手が欠場 → 選手が1人減り、残りの位置が詰まる。
  - `test_linkage_excess_counts_observed_minus_expected`: 手で作った LinkageRace（pair_prob がすべて 0.1、同じラインの組が2組で、そのうち1組が両方3着以内）→ `1 − 0.2 = 0.8`。
  - `test_placebo_equals_actual_when_everyone_is_one_line`: 全員が1つのラインなら、placebo_excess は linkage_excess と同じ。
  - `test_decide`: `((0.1, 0.3), (0.2, 0.1), 0.05) → "正の効果"`、`((-0.3, -0.1), (-0.2, -0.1), 0.05) → "負の効果"`、`((-0.02, 0.03), (0.01, -0.01), 0.05) → "実質的な差なし"`、`((0.01, 0.3), (0.2, -0.1), 0.05) → "保留"`、`((0.1, 0.3), (0.2, -0.1), 0.05) → "保留"`。
  - `test_summary_detects_simulated_linkage`: 同じラインの2人が必ず一緒に3着以内に入るように作った 400 レース（pair_prob は一様の PL から）→ `groups["all"]["decision"] == "正の効果"`。
  - `test_summary_without_linkage_is_not_positive`: 一様の PL から独立に着順を引いた 400 レース → decision が `"正の効果"` でない。
  - `test_cli_line_linkage_prints_aggregates_only`: tmp の表で `cli.main(["line-linkage", ...])` → 標準出力が JSON で `groups` を持ち、`full_card` の `racer_id`（例 `000001`）も `racer_name` も含まない。
- [ ] **Step 2: 実行して失敗を確かめる** — Run: `uv run pytest tests/test_research.py -q` / Expected: FAIL（`keirin.research` がない）
- [ ] **Step 3: `research.py` と CLI を実装する**（上の Interfaces のとおり。モジュールの docstring に、集計値だけを出すことと、検証の記録から参照されることを書く）
- [ ] **Step 4: テストが通ることを確かめる** — Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` / Expected: すべて通る
- [ ] **Step 5: コミット**（ブランチ `feat/research-verification` を main から作る）— `feat(research): verify line linkage against a score-only model`

---

### Task 9: keirin-platform — 車番の配置と1番車の効果の検証

**Files:**
- Modify: `keirin-platform/src/keirin/research.py`
- Modify: `keirin-platform/tests/test_research.py`
- Modify: `keirin-platform/src/keirin/cli.py`（`car-order` を足す）
- Modify: `keirin-platform/CLAUDE.md`（「コマンド」に2行、「ディレクトリ」に research.py の1行）

**Interfaces:**
- Consumes: Task 8 の `bootstrap_ci`、`decide`
- Produces:
  - `CAR1_PRACTICAL = 0.03`
  - `Card(day: date, time_slot: str, field_size: int, group: str, cars: tuple[int, ...], scores: tuple[float, ...], finish_pos: tuple[int | None, ...])`（frozen dataclass。出走表の全員。欠場の選手の finish_pos は None）
  - `load_cards(data_dir: Path, start: date, end: date) -> list[Card]`: meetings の `time_slot`、races の `race_class`（group は `predict.group_of`）と `entries`（field_size）、entries の `car_no`・`score`・`finish_pos`・`notes`。得点が空の選手がいるレースは除く。
  - `score_order_summary(cards: list[Card]) -> dict[str, Any]`: ミッドナイトのレースについて `{"races", "car_order_equals_score_rank", "car1_is_top_score"}`（割合。得点は高いほど上位、同点は順を問わず満たすとみなす）
  - `convention_summary(cards: list[Card]) -> dict[str, Any]`: 9車の `"4<5"`・`"6<7"`・`"8<9"`、7車の `"5<6"`・`"6<7"` について、`{"races", "share"}`（左の車番の得点が右より厳密に低い割合）
  - `car1_effect(cards: list[Card], *, n_boot: int = 2000, seed: int = 0) -> dict[str, Any]`: ミッドナイト以外の、男子（A・S）の7車のレースで、得点1位がただ1人のレース。得点1位の選手の1着の割合を、1番車のとき（`car1_win`）とそれ以外（`other_win`）で出し、差 `diff` と、その 95% 区間（レースを単位にしたブートストラップ）、前半と後半の差、`decision = decide(ci95, halves, CAR1_PRACTICAL)`、各群の件数。欠場の選手は除いて得点1位を決める。
- CLI: `keirin car-order --date D --to T [--boot 2000] [--data-dir DIR]` → `{"period", "score_order": …, "convention": …, "car1_effect": …}` を JSON で出す。

- [ ] **Step 1: 失敗するテストを書く**
  - `test_score_order_summary`: ミッドナイトで得点順のレース1件、順でないレース1件 → `car_order_equals_score_rank == 0.5`、`car1_is_top_score == 0.5`。ナイターのレースは数えない。
  - `test_convention_summary`: 9車で 4<5・6<7 は満たし 8<9 は満たさないレース1件 → それぞれ 1.0・1.0・0.0。
  - `test_car1_effect_counts_top_scorer_only`: 得点1位が1番車で1着のレース、得点1位が3番車で2着のレース、得点1位が同点のレース（除外）、ミッドナイトのレース（除外）、L級（除外）→ `car1_win == 1.0`、`other_win == 0.0`、件数 1 と 1。
  - `test_cli_car_order_prints_json`: tmp の表で `cli.main(["car-order", ...])` → JSON に3つの節があり、選手の ID と名前を含まない。
- [ ] **Step 2: 実行して失敗を確かめる** — Run: `uv run pytest tests/test_research.py -q` / Expected: 上のテストが FAIL
- [ ] **Step 3: 実装する**
- [ ] **Step 4: テストと lint** — Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` / Expected: すべて通る
- [ ] **Step 5: CLAUDE.md の「コマンド」と「ディレクトリ」を更新してコミット** — `feat(research): verify car-number placement and the car-1 effect`
- [ ] **Step 6: PR を作り、CI が通ったらマージする**（タイトル `feat(research): add verification commands for keirin-research`）。マージ後の main のコミットを短い形で控える（Task 10 の `検証の記録` に使う）。

---

### Task 10: 手元のデータで検証して、法則を登録するか候補に置く

**Files:**
- Modify: `keirin-research/knowledge/domain/laws.md`、`knowledge/domain/rules.md`（RULE-004・RULE-005 の数値）、`candidates.md`
- Modify: `keirin-research/tests/test_seed.py`（必要なら）

**Interfaces:**
- Consumes: Task 8〜9 の CLI、Task 9 Step 6 のコミット
- Produces: LAW-001（ラインの連動）と LAW-002（1番車の位置の有利）、または対応する CAND

- [ ] **Step 1: 期間を確かめる。** `ls ~/github/keirin-platform/keirin-data/tables/lines/*/ | head` と `tail` で lines 表の最初と最後の日を、entries 表で途切れのない最初の日と最後の日を確かめる。
- [ ] **Step 2: ラインの連動を集計する**（出力はスクラッチに置き、どのリポジトリにもコミットしない）

```bash
cd ~/github/keirin-platform/keirin-platform
uv run keirin line-linkage --date <lines の最初の日> --to <最後の日> --data-dir ../keirin-data > <scratchpad>/line-linkage.json
```
Expected: `groups.all.decision` が出る。
- [ ] **Step 3: 車番を集計する**

```bash
uv run keirin car-order --date <entries の途切れのない最初の日> --to <最後の日> --data-dir ../keirin-data > <scratchpad>/car-order.json
```
- [ ] **Step 4: 結果を知識にする**（COMMIT-002 のとおり）
  - ラインの連動: `"正の効果"` なら LAW-001（種別 `全体の傾向`、状態 `確認済み`）。`内容` は「同じラインの2人がともに3着以内に入る組は、得点だけのモデルの期待より1レースあたり X 組多い（A級 …、S級 …）」。`検証の記録` に、`keirin-platform:src/keirin/research.py@<Task 9 のコミット>`、コマンド、期間、レース数、95% 区間、前半・後半、偽のライン（並べ替え）の値、実質的な大きさ 0.05 組を書く。`判断が変わりうる条件` は「本物の並びが1年分そろったら確かめ直す。並びは予想なので効果は控えめに出る（BLIND-002）」。依存 MECH-001、METH-006。候補の「ラインの連動」は消す。`"保留"` などなら、候補の「ラインの連動」に数値を書き、MECH-001 だけで T1 を支える。
  - 1番車: `"実質的な差なし"` か `"負の効果"` なら LAW-002（`全体の傾向`、状態 `否定`。題は「1番車の位置そのものの有利」）。`"正の効果"` なら状態 `確認済み` にして、MECH-003 の書き方を数値に合わせて直す。`"保留"` なら CAND。`検証の記録` の書き方はラインの連動と同じ（実質的な大きさ 0.03）。
  - RULE-004・RULE-005 の数値と集計期間を、car-order の `score_order` と `convention` に置き換える。
- [ ] **Step 5: 検査とテスト** — Run: `python3 tools/kb.py check && python3 -m unittest discover -s tests -v` / Expected: `OK` と OK
- [ ] **Step 6: コミット** — `docs(knowledge): register line linkage and car-number findings`（中身に合わせて変えてよい）

---

### Task 11: 導入と評価（Claude Code、Codex）

**Files:**
- Modify: `keirin-research/skills/**`、`knowledge/**`（評価で落ちたときだけ）
- Modify: `keirin-research/README.md`（Codex の導入）

- [ ] **Step 1: Claude Code に入れる**

```bash
ln -s ~/github/keirin-platform/keirin-research ~/.claude/skills/keirin-research
claude plugin list
```
Expected: `keirin-research@skills-dir` が並ぶ（無効なら `claude plugin enable keirin-research@skills-dir`）。
- [ ] **Step 2: 評価に API キーが要ることを確かめる。** `ANTHROPIC_API_KEY` が環境にあるか確かめる。なければオーナーに頼んで止まる（評価は料金がかかる）。
- [ ] **Step 3: 評価を回す**

```bash
cd ~/github/keirin-platform/keirin-research
claude plugin eval . --ablation with-without --no-publish --trust-plugin --threshold 0.8 --json evals/results/run.json
```
Expected: スキルありで全ケースが基準を超え、スキルなしより点が高い。フラグが今の `claude plugin eval --help` と違えば合わせる。`--no-publish` は必ず付ける。
- [ ] **Step 4: 落ちたケースを直す。** 採点ではなく、スキルか知識を直す（採点が依頼文と合っていないときだけ採点を直し、理由をコミットメッセージに書く）。Step 3 を繰り返す。結果の JSON はコミットしない（`.gitignore` 済み）。スキルありとなしの点を控える（Task 12 の PR 本文に書く）。
- [ ] **Step 5: Codex を確かめる。** Codex の公式の文書で、スキルを読み込む場所（ユーザー単位・リポジトリ単位）と SKILL.md の書式の要件を確かめる。`which codex` で入っていれば、その場所にスキルを入れ、`codex exec "競輪のラインについて、使えるスキルと、読むべき知識のファイルを挙げて"` でスキルが見え、`../../knowledge/` のファイルを読めることを一度だけ確かめる。`../../knowledge/` が解決しない入れ方しかできなければ、README に解決する入れ方（リポジトリの根ごとリンクする等）を書く。入っていなければ、文書に基づく導入手順だけを README に書き、確かめていないと明記する。Codex をこの計画のために入れない。
- [ ] **Step 6: 検査とテスト、コミット** — Run: `python3 tools/kb.py check && python3 -m unittest discover -s tests -v` / Expected: `OK` と OK。コミット `docs: tune skills from eval results and document Codex setup`

---

### Task 12: オーナーの確認のあと、GitHub に作って公開する

**Files:** なし（GitHub の操作）

- [ ] **Step 1: オーナーに中身を確認してもらう。** 知識の ID と題の一覧、LAW の下書き（Task 10）、候補の件数、評価の点を短くまとめて見せ、公開してよいかを聞く。承認がなければ止まる。指摘があれば直してコミットする。
- [ ] **Step 2: リポジトリを作って push する**

```bash
cd ~/github/keirin-platform/keirin-research
gh repo create keirin-platform/keirin-research --public --description "競輪の検証と予想のための共通認識（知識とスキル）" --source . --remote origin
git push -u origin main
git push -u origin feat/v1
gh pr create --base main --head feat/v1 --title "feat: keirin-research v1" --body "<構成、登録した法則、候補の件数、評価の点（スキルあり／なし）>"
```
- [ ] **Step 3: CI（ci.yml）が PR で動いて通ることを確かめ、マージする。** Expected: `check` ジョブが成功（ワークフローの起動の確認も兼ねる）。

---

### Task 13: 使う側のリポジトリを keirin-research に向ける

**Files:**
- Modify: `keirin-platform/CLAUDE.md`、`keirin-platform/docs/keirin-theories.md`、`keirin-platform/docs/lines.md:3`
- Modify: `keirin-blog/CLAUDE.md`
- Create: `keirin-data/observations.md`、Modify: `keirin-data/README.md`

- [ ] **Step 1: keirin-platform（ブランチ `docs/point-to-keirin-research`）**
  - CLAUDE.md の「## 分析するときの前提（車番の付け方）」を「## 競輪のドメイン知識」に変え、本文を「車番の付け方、ライン、得点の尺度、データの性質、検証した法則と保留の候補は [keirin-research](https://github.com/keirin-platform/keirin-research) にある。分析・予想・記事の前に、そのスキル（`keirin-research:keirin-knowledge` など）か AGENTS.md の地図から読む。」にする。
  - 「オーナーの要望」4. のリンクを keirin-research の `candidates.md` に変える。11. として keirin-research（2026-11-01）を足す（目的1行、Public、登録と保留の基準、封印期間なし、観察記録は keirin-data、設計書と計画へのリンク）。
  - 「構成」に `keirin-platform/keirin-research (Public) ← 競輪の共通認識（知識とスキル）` を足す。決定ログに 2026-11-01 の行を足す（上の決定を1〜2行で）。「現状と TODO」の docs/keirin-theories.md への参照を candidates.md に変え、keirin-research の後続（雨・風の確かめ直し 2026-12 下旬、ライン関係の通説 2027-01 ごろ、keirin-platform の AGENTS.md）を足す。
  - docs/keirin-theories.md を「この一覧は keirin-research の candidates.md と knowledge/ に移した（2026-11-01）。移す前の内容は git の履歴にある。」とリンクだけにする。docs/lines.md:3 のリンクも candidates.md に変える。
  - Run: `grep -rn "keirin-theories" --include=*.md . | grep -v docs/superpowers` / Expected: 出ない（docs/keirin-theories.md 自身の見出しを除く）。PR を作り、CI が通ったらマージする（`docs: point domain knowledge to keirin-research`）。
- [ ] **Step 2: keirin-blog（ブランチ `docs/point-to-keirin-research`）** — CLAUDE.md の要点に「競輪のドメイン知識（ライン、車番、得点の尺度、検証した法則）は keirin-research（https://github.com/keirin-platform/keirin-research）にある。分析の前に読む」を足す。PR、マージ。
- [ ] **Step 3: keirin-data（ブランチ `docs/add-observations`）** — `observations.md` を作る。冒頭に「オーナーが映像や現地で見たことの記録。レースを特定できるので Private のここに置く。一般化した知識は keirin-research に置き、出典に OBS の ID を書く。書式は keirin-research の knowledge/README.md」と書く（記録はまだ0件）。README.md の一覧に `observations.md` の1行を足す。
  Run: `python3 ~/github/keirin-platform/keirin-research/tools/kb.py check --root ~/github/keirin-platform/keirin-research --observations observations.md` / Expected: `OK`。PR、マージ（データのリポジトリもマージは Claude が行う）。
- [ ] **Step 4: 仕上げの確認。** keirin-platform で新しいセッションを開いたと想定し、`claude plugin list` に keirin-research が並び、CLAUDE.md のリンクが GitHub で開けることを確かめる。
