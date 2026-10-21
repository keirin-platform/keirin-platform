# 競輪マルチビューアー（公開版）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** keirin.jp を使わず、各場の公式 YouTube ライブを最大4枠で並べる静的ページ「競輪マルチビューアー」を、新しい Public リポジトリ `keirin-platform/keirin-multiviewer` の GitHub Pages で公開する。

**Architecture:** 配信の検索は keirin-platform の `keirin.youtube` に「全場を探す」入り口と JSON の書き出しを足し、`keirin public-streams` コマンドにする。新しいリポジトリの GitHub Actions が、そのコマンドを keirin-platform の SHA 固定で入れて動かし、`streams.json` とページ（HTML・CSS・JavaScript、ライブラリなし）を Pages にデプロイする。ページの判断（枠の割り当て、最初の選択、古さの判定、URL の解釈）は DOM に触れない ES モジュール `logic.js` にまとめ、`node --test` で確かめる。

**Tech Stack:** Python 3.12、httpx、pytest（keirin-platform 側）。素の HTML / CSS / JavaScript（ES modules）、Node 24 の `node:test`、GitHub Actions（`actions/upload-pages-artifact`、`actions/deploy-pages`）、YouTube Data API v3、YouTube IFrame 埋め込み。

**Spec:** [docs/superpowers/specs/2026-10-21-public-multiviewer-design.md](../specs/2026-10-21-public-multiviewer-design.md)

## Global Constraints

- **keirin.jp に一切アクセスしない**。新しいリポジトリのコードも、keirin-platform に足すコードも、`keirin.client` / `keirin.store` を import しない。
- YouTube のページは読まない。API は `playlistItems` と `videos` だけ（`search` は使わない）。
- **API キーをページ、ログ、リポジトリに出さない**。キーは Actions の Secret `YOUTUBE_API_KEY` からだけ読む。
- `streams.json` はリポジトリにコミットしない（`.gitignore` に入れる）。Pages の成果物としてだけ置く。
- 埋め込みの URL は `https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&playsinline=1`（`keirin.youtube.embed_url` と同じ）。音を操作しない。
- 枠は最大4つ（2×2 固定）。場の選び直しやデータの読み込みで、残した枠の iframe を作り直さない。枠の大きさが変わるのは、ページを開いたときとウィンドウの大きさが変わったときだけ。
- 1440×790 の表示域で、縦スクロールなしに4枠が収まる。
- 開始から **7時間**（`STALE_HOURS = 7`）を過ぎた配信は「終わっているかもしれない」扱い。`streams.json` の作成から **24時間**（`OLD_LIST_HOURS = 24`）を過ぎたら、一覧が古いと知らせる。
- 非公式であること、YouTube 利用規約へのリンク（`https://www.youtube.com/t/terms`）、プライバシーポリシーへのリンクを、ページに載せる。JKA や各場のロゴは使わない。
- 両リポジトリとも: PR で進める（新リポジトリの初期コミットだけ main へ直接）。ブランチ名は `feat/...` など。コミットは GPG 署名（`git commit -S`）、メッセージは Conventional Commits の英語。コミット前に CLAUDE.md の手順で GPG のキャッシュを確かめ、切れていたらオーナーにパスフレーズの入力を頼む。CI が通ったら `gh pr merge <n> --merge --delete-branch --subject "<PRタイトル> (#<n>)"` で Claude がマージする。
- Actions の `uses:` はコミット SHA で固定し、末尾にバージョンをコメントで書く（keirin-platform の ci.yml と同じ書き方）。
- テストは架空のデータだけで書く。実際のキー、実在の動画 ID は使わない。
- keirin-platform の lint は `uv run ruff check . && uv run ruff format --check .`、テストは `uv run pytest -q`。

## Review Focus

1. **21〜22時台に、終わりかけのナイターと始まったミッドナイトが両方「ライブ中」**: 最初の4枠は開始が新しい順に入り、開始から7時間を過ぎた配信は入らない（Task 4 の `defaultSelection` のテスト）。
2. **ライブ中も予約中もない時間（深夜・早朝）や、streams.json が空・取得できない**: ページは壊れず、空き枠と「配信が見つかりません」の帯が出る。URL の貼り付けは使える（Task 4 のテスト、Task 5 のブラウザ確認）。
3. **場を外して足す、4場のときに5場目を押す**: 残した枠の位置と iframe はそのまま、5場目は入らない（Task 4 の `assignSlots` / `toggleVenue` のテスト、Task 5 のブラウザ確認）。
4. **貼った URL が YouTube の動画でない、枠がすべて埋まっている**: 動画でなければ何も変えずにエラーを出す。埋まっていれば、どの枠に入れるかを使う人が選ぶ（Task 4 の `videoIdFromUrl` / `slotForPasted` のテスト）。
5. **検索の失敗（キーなし、403、ネットワーク）**: コマンドが 0 以外で終わり、ワークフローはデプロイしないので、前の版が残る（Task 2 のテスト、Task 6 のワークフロー）。

---

### Task 1: 全場の配信を探して JSON にする（keirin-platform `youtube.py`）

**Files:**
- Modify: `keirin-platform/src/keirin/youtube.py`（`_in_window`、`pick_streams` の近く。新しい関数は `find_streams` のあと）
- Test: `keirin-platform/tests/test_youtube.py`

**Interfaces:**
- Produces:
  - `pick_streams(streams, meetings, day, *, any_live: bool = False) -> dict[str, Stream]`。`any_live=True` のとき、`meetings[venue] is None` の場では、ライブ中の配信は開始日が `day` でなくても残す。既定の `False` では今の動きのまま（Private ビューアは変えない）。
  - `find_all_streams(client: YouTubeClient, day: date) -> dict[str, Stream]`: `CHANNELS` の全チャンネルを探し、照合の候補も全場にする（`{code: None for code in CHANNELS}` を `pick_streams(..., any_live=True)` に渡す）。
  - `streams_document(streams: Mapping[str, Stream], generated_at: datetime) -> dict[str, Any]`: 公開版の `streams.json` の中身。形は次のとおり（キー名はページ側と共有するので変えない）。
    ```json
    {"generated_at": "2026-10-21T19:13:05+09:00",
     "streams": [{"venue_code": "47", "venue_name": "松阪", "video_id": "vid00000001",
                  "title": "…", "start": "2026-10-21T20:45:00+09:00", "live": true}]}
    ```
    `streams` は `start` の昇順。`venue_name` は `VENUE_NAMES[code][0]`。時刻は JST の ISO 8601（秒まで）。

- [ ] **Step 1: ブランチを作る**

Run（keirin-platform で）: `git switch main && git pull && git switch -c feat/public-multiviewer`

- [ ] **Step 2: 失敗するテストを書く**（既存の `_api`、`_video`、`TAMANO` などのヘルパーを使う）

```python
def test_pick_any_live_keeps_yesterdays_live_stream():
    s = Stream(
        "vid00000009", TAMANO, "玉野競輪 2日目", datetime(2026, 10, 20, 23, 50, tzinfo=JST), True
    )
    day = date(2026, 10, 21)
    assert pick_streams([s], {"61": None}, day) == {}
    assert pick_streams([s], {"61": None}, day, any_live=True) == {"61": s}


def test_find_all_streams_searches_every_channel():
    transport, calls = _api(
        {
            "vid00000001": _video(
                "vid00000001", TAKAMATSU, "モーニング競輪 in松山 1日目", "2026-10-20T23:30:00Z"
            )
        },
        {TAKAMATSU: ["vid00000001"]},
    )
    got = find_all_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21))
    assert calls.count("playlistItems") == sum(len(c) for c in CHANNELS.values())
    assert set(got) == {"75"}  # 高松のチャンネルの松山の開催は、松山になる


def test_find_all_streams_non_race_live_on_idle_channel():
    # 設計書の既知の弱点: 開催していない場のチャンネルのレース以外のライブ（場名なし）は、
    # そのチャンネルの場として出る。「予想」を含むものだけは除外される。
    maebashi = CHANNELS["22"][0]
    transport, _ = _api(
        {
            "vid00000003": _video(
                "vid00000003", maebashi, "トークショー生配信", "2026-10-21T09:00:00Z"
            ),
            "vid00000004": _video(
                "vid00000004", maebashi, "【競輪予想】今夜の予想", "2026-10-21T10:00:00Z"
            ),
        },
        {maebashi: ["vid00000003", "vid00000004"]},
    )
    got = find_all_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21))
    assert {k: v.video_id for k, v in got.items()} == {"22": "vid00000003"}


def test_streams_document_shape_and_order():
    late = Stream(
        "vid00000002", MATSUSAKA, "松阪", datetime(2026, 10, 21, 20, 45, tzinfo=JST), False
    )
    early = Stream("vid00000001", TAMANO, "玉野", datetime(2026, 10, 21, 10, 0, tzinfo=JST), True)
    doc = streams_document({"47": late, "61": early}, datetime(2026, 10, 21, 19, 13, 5, tzinfo=JST))
    assert doc["generated_at"] == "2026-10-21T19:13:05+09:00"
    assert [s["venue_code"] for s in doc["streams"]] == ["61", "47"]
    assert doc["streams"][1] == {
        "venue_code": "47",
        "venue_name": "松阪",
        "video_id": "vid00000002",
        "title": "松阪",
        "start": "2026-10-21T20:45:00+09:00",
        "live": False,
    }
```

- [ ] **Step 3: テストが失敗することを確かめる**

Run: `uv run pytest tests/test_youtube.py -q`
Expected: FAIL（`find_all_streams` / `streams_document` の ImportError でファイル全体が集められない）。

- [ ] **Step 4: 実装する**

`_in_window` に Stream ごとの判断を渡せるよう、`pick_streams` のループで `any_live and meetings[venue] is None and s.live` なら日付の条件を飛ばす。`find_all_streams` は `find_streams` と同じ形で全チャンネルを渡す。`streams_document` は `isoformat(timespec="seconds")` を使う。

- [ ] **Step 5: テストと lint を通す**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: すべて PASS、lint はエラーなし。

- [ ] **Step 6: コミット**

```bash
git add src/keirin/youtube.py tests/test_youtube.py
git commit -S -m "feat(youtube): search every venue channel for the public multiviewer"
```

---

### Task 2: `keirin public-streams` コマンドと記録（keirin-platform）

**Files:**
- Modify: `keirin-platform/src/keirin/cli.py`（`cmd_discord_test` の近くにコマンド、`build_parser` にサブコマンド）
- Modify: `keirin-platform/CLAUDE.md`（「オーナーの要望」9.、「構成」、「決定ログ」、「ディレクトリ」の youtube.py の行、「現状と TODO」）
- Modify: `keirin-platform/docs/multiview.md`（冒頭に公開版の設計書へのリンクを1行）
- Add to the PR: `docs/superpowers/specs/2026-10-21-public-multiviewer-design.md`、`docs/superpowers/plans/2026-10-21-public-multiviewer.md`
- Test: `keirin-platform/tests/test_storage_and_cli.py`（または新しい `tests/test_public_streams.py`）

**Interfaces:**
- Consumes: Task 1 の `find_all_streams`、`streams_document`、`YouTubeClient`、`YouTubeError`。
- Produces: コマンド `keirin public-streams --out <path>`。環境変数 `YOUTUBE_API_KEY` を読み、JST の今日で `find_all_streams` を呼び、`streams_document` を UTF-8・`ensure_ascii=False` で `--out` に書く。終了コード: 成功 0、キー未設定 2、`YouTubeError` 1（**ファイルを書かない**）。ログにキーを出さない。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_public_streams_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    out = tmp_path / "streams.json"
    assert cli.main(["public-streams", "--out", str(out)]) == 2
    assert not out.exists()


def test_public_streams_writes_document(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    s = Stream(
        "vid00000001", CHANNELS["47"][0], "松阪", datetime(2026, 10, 21, 20, 45, tzinfo=JST), True
    )
    monkeypatch.setattr(cli.youtube, "find_all_streams", lambda client, day: {"47": s})
    out = tmp_path / "streams.json"
    assert cli.main(["public-streams", "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["streams"][0]["venue_name"] == "松阪"


def test_public_streams_api_error_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def fail(client, day):
        raise YouTubeError("videos: HTTP 403")

    monkeypatch.setattr(cli.youtube, "find_all_streams", fail)
    out = tmp_path / "streams.json"
    assert cli.main(["public-streams", "--out", str(out)]) == 1
    assert not out.exists()
```

（`cli` が `from keirin import youtube` で import する前提。モジュール属性を差し替えられる形にする。）

- [ ] **Step 2: 失敗を確かめる** — Run: `uv run pytest -q -k public_streams` / Expected: 3件 FAIL（サブコマンドがない）。

- [ ] **Step 3: `cmd_public_streams(args: argparse.Namespace) -> int` を実装し、`public-streams` を `build_parser` に足す**（help: `"write today's venue live streams for the public multiviewer (YouTube only)"`）。日付は `keirin.timeutil.today_jst()`、作成時刻は `datetime.now(JST)`。

- [ ] **Step 4: テストと lint を通す** — Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` / Expected: すべて PASS。

- [ ] **Step 5: CLAUDE.md と docs を更新する**
  - 「オーナーの要望」9. の下に: 公開版「競輪マルチビューアー」（2026-10-21、オーナーの要望）。keirin.jp を使わず YouTube だけ、GitHub Pages、`keirin-platform/keirin-multiviewer`（Public）、設計書へのリンク。記事からリンクしてよい（「ビューアや Private リポジトリにはリンクしない」の例外）。
  - 「構成」に `keirin-platform/keirin-multiviewer (Public)` の行。
  - 「決定ログ」2026-10-21: 静的ページにした理由（Streamlit が眠る、余白、クォータが訪問者数に比例しない）、streams.json をコミットしない理由（YouTube の開発者ポリシー）、公開版専用のキー、Claude が repo と Pages を作り、オーナーがキーと Secret を担当。
  - 「コマンド」に `YOUTUBE_API_KEY=... uv run keirin public-streams --out streams.json`。
  - 「現状と TODO」に未完了の項目: 公開版の公開（Task 6 の完了で [x]）、オーナーによるキーの発行と Secret 登録、組織のプロフィールに載せるかをオーナーに聞く。

- [ ] **Step 6: コミット、PR、マージ**

```bash
git add src/keirin/cli.py tests/ CLAUDE.md docs/multiview.md docs/superpowers/specs/2026-10-21-public-multiviewer-design.md docs/superpowers/plans/2026-10-21-public-multiviewer.md
git commit -S -m "feat(cli): add public-streams for the public multiviewer"
git push -u origin feat/public-multiviewer
gh pr create --title "feat: public multiviewer stream search" --body "<設計書へのリンクと変更の要約>"
```
CI が通ったらマージし、**main のマージコミットの SHA を控える**（Task 6 で使う）。

---

### Task 3: 新しいリポジトリと Pages の準備

**Files（新リポジトリ `keirin-platform/keirin-multiviewer`、clone 先は `/home/shimomo/github/keirin-platform/keirin-multiviewer`）:**
- Create: `README.md`、`.gitignore`

**Interfaces:**
- Produces: Public リポジトリ、Pages（ソースは GitHub Actions）、main の初期コミット。

- [ ] **Step 1: リポジトリを作る**

```bash
gh repo create keirin-platform/keirin-multiviewer --public \
  --description "競輪マルチビューアー: 各場の公式 YouTube ライブを1画面に並べる非公式ページ"
cd /home/shimomo/github/keirin-platform && gh repo clone keirin-platform/keirin-multiviewer
```

- [ ] **Step 2: 初期コミット（README と .gitignore）を main に push する**
  - `README.md`: 何をするページか、URL `https://keirin-platform.github.io/keirin-multiviewer/`、**非公式**（JKA・各場とは無関係、各場の公式 YouTube 配信を埋め込んで並べるだけ）、keirin.jp は使わないこと、配信の検索は Actions が YouTube Data API で1日数回行うこと、キーは Secret `YOUTUBE_API_KEY`、検索のコードは keirin-platform の `keirin.youtube`。Private リポジトリや Private ビューアには触れない。
  - `.gitignore`: `site/streams.json`、`node_modules/`、`.superpowers/`。
  - `git commit -S -m "chore: initial commit"` → `git push origin main`。

- [ ] **Step 3: Pages を有効にする**

Run: `gh api -X POST repos/keirin-platform/keirin-multiviewer/pages -f build_type=workflow`
Expected: 201。`gh api repos/keirin-platform/keirin-multiviewer/pages --jq .build_type` が `workflow`。

- [ ] **Step 4: オーナーに Secret の登録を頼む**（ここでは待たない。Task 6 の手動実行の前までに済めばよい）
  - 公開版専用のキーを発行し、YouTube Data API v3 だけに制限する。
  - `https://github.com/keirin-platform/keirin-multiviewer/settings/secrets/actions` で `YOUTUBE_API_KEY` を登録する。

---

### Task 4: ページの判断ロジック（`site/logic.js`）と CI

**Files（keirin-multiviewer、ブランチ `feat/page`）:**
- Create: `site/logic.js`、`tests/logic.test.js`、`package.json`、`.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 1 の streams.json の形（`generated_at`、`streams[]` の `venue_code`、`venue_name`、`video_id`、`title`、`start`、`live`）。
- Produces（すべて named export、DOM に触れない純粋関数。`now` は `Date`）:
  - `MAX_TILES = 4`、`STALE_HOURS = 7`、`OLD_LIST_HOURS = 24`
  - `isLive(stream, now): boolean` — `Date(stream.start) <= now`（データの `live` フラグではなく時刻で決める）。
  - `isStale(stream, now): boolean` — 開始から `STALE_HOURS` 時間以上たった。
  - `defaultSelection(streams, now): string[]` — 場コードを最大4つ。まずライブ中で古くないものを**開始が新しい順**、足りなければ予約中を**開始が近い順**。古いものは入れない。
  - `assignSlots(previous: (string|null)[], selected: string[]): (string|null)[]` — 長さ4。残した場は同じ位置、新しい場は空いた位置の先頭から（`keirin.multiview.assign_slots` と同じ動き）。
  - `toggleVenue(slots, code): (string|null)[]` — 入っていれば外す（その位置を null）、なければ最初の空きに入れる。空きがなければ `slots` をそのまま返す。
  - `videoIdFromUrl(url: string): string | null` — watch / youtu.be / live / embed の URL から11文字の ID（`keirin.youtube._VIDEO_ID` と同じ正規表現）。
  - `slotForPasted(slots): number | null` — 最初の空きの位置。空きがなければ null（そのときページは使う人に枠を選ばせる）。
  - `isOldList(generatedAt: string, now): boolean` — 作成から `OLD_LIST_HOURS` 時間以上。
  - `embedUrl(videoId): string` — Global Constraints の URL。

- [ ] **Step 1: ブランチと package.json**

`git switch -c feat/page`。`package.json` は `{"private": true, "type": "module", "scripts": {"test": "node --test tests/"}}` だけ（依存なし）。

- [ ] **Step 2: 失敗するテストを書く**（`tests/logic.test.js`、`node:test` と `node:assert/strict`）

架空の配信を作るヘルパー `s(code, start)` を置き、次を確かめる。時刻はすべて `+09:00` で書く。
```js
const NOW = new Date("2026-10-21T21:30:00+09:00");
// ナイター（15:00 開始 → 6.5時間、古くない）、ミッドナイト3場（21:00, 21:10, 21:20）、昼（09:30 開始 → 12時間、古い）
test("defaultSelection: newest live first, stale excluded", () => {
  const streams = [s("71", "2026-10-21T15:00:00+09:00"), s("47", "2026-10-21T21:00:00+09:00"),
    s("13", "2026-10-21T21:10:00+09:00"), s("22", "2026-10-21T21:20:00+09:00"),
    s("81", "2026-10-21T09:30:00+09:00")];
  assert.deepEqual(defaultSelection(streams, NOW), ["22", "13", "47", "71"]);
});
test("defaultSelection: fills with upcoming, soonest first", () => {
  const streams = [s("47", "2026-10-21T22:00:00+09:00"), s("13", "2026-10-21T21:45:00+09:00"),
    s("22", "2026-10-21T21:20:00+09:00")];
  assert.deepEqual(defaultSelection(streams, NOW), ["22", "13", "47"]);
});
test("defaultSelection: empty list", () => assert.deepEqual(defaultSelection([], NOW), []));
test("isStale boundary", () => {
  assert.equal(isStale(s("81", "2026-10-21T14:30:00+09:00"), NOW), true);   // ちょうど7時間
  assert.equal(isStale(s("81", "2026-10-21T14:31:00+09:00"), NOW), false);
});
test("assignSlots keeps kept venues in place", () => {
  assert.deepEqual(assignSlots(["47", "13", "22", null], ["22", "47", "71"]), ["47", "71", "22", null]);
});
test("toggleVenue: add, remove, fifth is ignored", () => {
  assert.deepEqual(toggleVenue(["47", null, null, null], "13"), ["47", "13", null, null]);
  assert.deepEqual(toggleVenue(["47", "13", null, null], "47"), [null, "13", null, null]);
  const full = ["47", "13", "22", "71"];
  assert.deepEqual(toggleVenue(full, "81"), full);
});
test("videoIdFromUrl", () => {
  assert.equal(videoIdFromUrl("https://www.youtube.com/watch?v=abcdefghijk&t=1"), "abcdefghijk");
  assert.equal(videoIdFromUrl("https://youtu.be/abcdefghijk"), "abcdefghijk");
  assert.equal(videoIdFromUrl("https://www.youtube.com/live/abcdefghijk?si=x"), "abcdefghijk");
  assert.equal(videoIdFromUrl("https://example.com/watch?v=abcdefghijk"), null);
  assert.equal(videoIdFromUrl("abc"), null);
});
test("slotForPasted", () => {
  assert.equal(slotForPasted(["47", null, "22", null]), 1);
  assert.equal(slotForPasted(["47", "13", "22", "71"]), null);
});
test("isOldList boundary", () => {
  assert.equal(isOldList("2026-10-20T21:30:00+09:00", NOW), true);
  assert.equal(isOldList("2026-10-20T21:31:00+09:00", NOW), false);
});
test("embedUrl starts muted", () => {
  assert.equal(embedUrl("abcdefghijk"),
    "https://www.youtube.com/embed/abcdefghijk?autoplay=1&mute=1&playsinline=1");
});
```

- [ ] **Step 3: 失敗を確かめる** — Run: `npm test` / Expected: FAIL（`site/logic.js` がない）。

- [ ] **Step 4: `site/logic.js` を実装する。**

- [ ] **Step 5: テストを通す** — Run: `npm test` / Expected: すべて PASS。

- [ ] **Step 6: CI を書く**（`.github/workflows/ci.yml`: `pull_request` と main への push。`permissions: contents: read`。`actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`（`persist-credentials: false`）、`actions/setup-node`（SHA 固定、node 24）、`npm test`）。

- [ ] **Step 7: コミット**

```bash
git add site/logic.js tests/logic.test.js package.json .github/workflows/ci.yml
git commit -S -m "feat(page): add the tile and selection logic"
```

---

### Task 5: ページ（`index.html`、`style.css`、`app.js`、`privacy.html`）

**Files（keirin-multiviewer、同じブランチ `feat/page`）:**
- Create: `site/index.html`、`site/style.css`、`site/app.js`、`site/privacy.html`

**Interfaces:**
- Consumes: Task 4 の `logic.js` の全関数。同じディレクトリの `streams.json`（`fetch("streams.json", {cache: "no-store"})`）。

**画面（設計書「画面」の A 案）:**
- `<title>競輪マルチビューアー</title>`、`lang="ja"`。
- 1行の帯: 左にタイトル「競輪マルチビューアー」、続けて streams.json の配信ごとに場のボタン（場名と、予約中なら「HH:MM〜」、ライブ中なら「LIVE」の印）。枠に入っている場のボタンは色を変える。古い配信（`isStale`）のボタンは薄く表示する（押せば入る）。4枠が埋まっているとき、入っていない場のボタンは押せない見た目にする（`toggleVenue` がそのまま返す）。右端に「URL を貼る」、その隣に小さく「非公式 ・ 利用規約 ・ プライバシー」のリンク（利用規約は `https://www.youtube.com/t/terms`、プライバシーは `privacy.html`）。
- 帯に小さく出す知らせ: streams.json が取れない、または配信が0件のとき「配信が見つかりません。URL を貼ると表示できます」。`isOldList` のとき「配信の一覧が古い可能性があります（最終更新 HH:MM）」。ふだんは「最終更新 HH:MM」を `title` 属性などで目立たせずに出す。
- 帯の下は 2×2 グリッド。枠は 16:9。大きさは「帯を除いた表示域の高さ」と「幅」の両方に収まる最大の大きさで、中央寄せ（CSS の `min()` と `aspect-ratio` でよい）。
- 枠の中: 場が入っていれば `<iframe src="embedUrl(id)" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen>`、左上に場名の小さなラベル（`pointer-events: none`）。空き枠には「上の場を押すと入る」。
- 「URL を貼る」: 入力欄（ポップオーバーかダイアログ）。`videoIdFromUrl` が null なら「YouTube の動画の URL ではありません」と出して何も変えない。`slotForPasted` が null なら、どの枠に入れるかを選ばせる（枠1〜4のボタン）。貼った動画のラベルは「URL」。保存しない。

**作り直さないための約束（app.js）:**
- 4つの枠の要素はページを開いたときに一度だけ作る。`render(slots)` は、枠の内容（場コードか動画 ID）が**変わった枠だけ** iframe を差し替え、変わらない枠には触れない。
- 場のボタンの並びは、ページを開いたときの streams.json の順（開始順）で固定する。streams.json は読み直さない（設計書で「任せる」とされた点を、読み直さない方に決める。Claude の判断）。

- [ ] **Step 1: `privacy.html` を書く**（短い文: このページは個人情報やクッキーを集めない。アクセス解析をしない。埋め込みの YouTube プレーヤーは YouTube の利用規約と Google のプライバシーポリシー `https://policies.google.com/privacy` に従う。非公式であること。問い合わせ先は GitHub リポジトリ）。

- [ ] **Step 2: `index.html`、`style.css`、`app.js` を書く。**

- [ ] **Step 3: 架空の streams.json でブラウザ確認をする**（スクリプトはスクラッチパッドに置き、コミットしない）
  - 架空の `site/streams.json` を作る（場4つ以上、ライブ中・予約中・古い配信を混ぜる。`video_id` は架空の11文字。埋め込みは YouTube のエラー表示になってよい）。
  - `python3 -m http.server -d site 8000` で配信し、インストール済みの Playwright の Chromium（`~/.cache/ms-playwright/chromium-1234`）で 1440×790 を開く。
  - 確かめること（すべて満たせば合格）:
    1. `document.documentElement.scrollHeight <= 790`（縦スクロールなし）、4枠とも表示域に入っている。
    2. 開いた直後の4枠が `defaultSelection` のとおり。
    3. 枠1の iframe に印を付け（`dataset.mark = "x"`）、別の場を外す・足す・URL を貼る、のあとも、印が残っている（同じ要素）。
    4. 4枠のときに5場目を押しても枠が変わらない。
    5. streams.json を消して開くと、帯に「配信が見つかりません…」が出て、URL を貼ると枠に入る。
    6. コンソールにエラーがない。
  - 架空の `site/streams.json` を消す（`.gitignore` で除外済みでも、残さない）。

- [ ] **Step 4: コミット**

```bash
git add site/index.html site/style.css site/app.js site/privacy.html
git commit -S -m "feat(page): add the multiviewer page"
```

---

### Task 6: 検索とデプロイのワークフロー、公開

**Files（keirin-multiviewer、同じブランチ `feat/page`）:**
- Create: `.github/workflows/deploy.yml`
- Modify: `README.md`（ワークフローの説明を足す）

**Interfaces:**
- Consumes: Task 2 の `keirin public-streams --out`、マージ後の keirin-platform の SHA（Task 2 Step 6 で控えたもの）、Task 3 の Pages 設定、Task 5 の `site/`。

**ワークフローの中身:**
- トリガー: `schedule`、`workflow_dispatch`、`push`（main）。cron は UTC で書き、JST の 07:13、12:13、16:13、19:13、20:13、21:13、22:13、23:13 に当たるもの:
  `13 22 * * *`、`13 3 * * *`、`13 7 * * *`、`13 10-14 * * *`。
- `concurrency: {group: pages, cancel-in-progress: false}`。
- job `build`（`permissions: contents: read`）:
  1. checkout（SHA 固定、`persist-credentials: false`）、`astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0`。
  2. `uv tool run --python 3.12 --from "keirin @ git+https://github.com/keirin-platform/keirin-platform.git@<SHA>" keirin public-streams --out site/streams.json`、`env: YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}`。失敗したらジョブが失敗し、デプロイしない（前の版が残る）。
  3. `actions/upload-pages-artifact`（SHA 固定、`path: site`）。
- job `deploy`（`needs: build`、`permissions: pages: write, id-token: write`、`environment: github-pages`）: `actions/deploy-pages`（SHA 固定）。
- job `keepalive`（`if: github.event_name == 'schedule'`、`permissions: actions: write`）: `gh api -X PUT repos/${{ github.repository }}/actions/workflows/deploy.yml/enable`（`env: GH_TOKEN: ${{ github.token }}`）。週1回で足りるが、毎回でも害はない。
  - **注意**: これで60日の自動停止が防げるかは GitHub の仕様に頼る。実装時に GitHub のドキュメントか、同じ方式の既存アクション（API 方式の keepalive）の説明で確かめ、防げないと分かったら、オーナーに相談する（コミットを作る方式は署名のルールとぶつかる）。
- keirin-platform を更新したら、この SHA を PR で上げる（README に書く）。

- [ ] **Step 1: `deploy.yml` を書き、`actionlint` があれば通す**（なければ `gh` で PR の CI と Step 4 の手動実行で確かめる）。

- [ ] **Step 2: README に、検索の時刻、SHA の上げ方、Secret、keepalive を足す。**

- [ ] **Step 3: コミット、push、PR、CI 通過後にマージ**

```bash
git add .github/workflows/deploy.yml README.md
git commit -S -m "ci: search streams and deploy the page to GitHub Pages"
git push -u origin feat/page
gh pr create --title "feat: multiviewer page and Pages deployment" --body "<設計書へのリンク、画面とワークフローの要約、ブラウザ確認の結果>"
```
マージで `push` が走るが、Secret がまだなら build が失敗する（想定どおり。デプロイされない）。

- [ ] **Step 4: Secret の登録を確かめてから、手動実行で確かめる**
  - `gh secret list --repo keirin-platform/keirin-multiviewer` に `YOUTUBE_API_KEY` があるか確かめる。なければオーナーに頼んで待つ（ここで止まる）。
  - `gh workflow run deploy.yml --repo keirin-platform/keirin-multiviewer` → `gh run watch` で build・deploy・（schedule ではないので keepalive はスキップ）を確かめる。
  - ログにキーが出ていないことを確かめる（`gh run view --log | grep -c AIza` が 0）。
  - `https://keirin-platform.github.io/keirin-multiviewer/streams.json` を開き、今日の配信が入っていることを確かめる。夜なら、ミッドナイト・ナイターの場が正しい場名で出ているかを見る。レース以外の配信が混ざっていたら記録する（設計書: 除外語は後で足す）。
  - ページを 1440×790 で開き、Task 5 Step 3 の 1・2・6 を本物のデータでもう一度確かめる。

- [ ] **Step 5: 記録**（keirin-platform、ブランチ `docs/public-multiviewer-launch`、PR → マージ）
  - CLAUDE.md の「現状と TODO」: 公開版の公開を [x] にし、本番で確かめたこと（日付、出た場、外れがあればその内容）を書く。keepalive を確かめた方法も書く。
  - 組織のプロフィール（`.github` の profile/README.md）に載せるかは、オーナーに聞く（載せるなら別の PR）。
