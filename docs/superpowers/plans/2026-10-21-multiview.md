# マルチビュー Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 各競輪場の公式 YouTube ライブを、ビューアの1ページに最大4枠（2×2 固定）で並べ、次に発走する場を色で示す。

**Architecture:** 配信の検索と場の照合は新モジュール `youtube.py`（純粋な関数と httpx クライアント）にまとめる。発走予定からの状態計算と枠の割り当ては `multiview.py` の純粋な関数で行い、同じファイルに Streamlit のページを置く。`viewer.py` の `main()` は `st.navigation` で「出走表」と「マルチビュー」の2ページにする。

**Tech Stack:** Python 3.12、Streamlit 1.64（`st.iframe`、`st.fragment`、`st.navigation`）、httpx、YouTube Data API v3、pytest（`streamlit.testing.v1.AppTest`）。

**Spec:** [docs/multiview.md](../../multiview.md)

## Global Constraints

- ブランチは `feat/multiview`。コミットは GPG 署名つき（`git commit -S`）で、メッセージは Conventional Commits 形式の英語にする。コミットの前に CLAUDE.md の手順で GPG のキャッシュを確かめ、切れていたらオーナーにパスフレーズの入力を頼む。
- lint は `uv run ruff check . && uv run ruff format --check .`（行の長さは100）。テストは `uv run pytest -q`。
- テストは**架空の API レスポンス**だけで書く。実際の API キー、実データ、実在の動画 ID は使わない。チャンネル ID の表は公開情報なので、コードに入れてよい。
- YouTube のページ（`youtube.com/channel/...` などの HTML）は読まない。使うのは `https://www.googleapis.com/youtube/v3/` の `playlistItems` と `videos` だけで、`search` は使わない。
- 配信の検索結果は15分キャッシュ、発走予定は開催ごとに1日1回取得する。
- 音を操作しない。埋め込みの URL は `https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&playsinline=1`。
- 枠は最大4つ。位置と大きさは変えない。
- 強調を移すのは、予定の発走時刻から **5分**（`WAIT_MINUTES = 5`）たったとき。

## Review Focus

1. **時刻の境界**: 予定時刻ちょうど、予定＋5分ちょうど、全レースが終わったあと。どの場も強調されない状態や、例外が出る状態にならない（Task 2 のテスト）。
2. **選択の入れ替え**: 4枠のうち1場を外して別の場を足しても、残した場の枠の位置（slot）が変わらない（Task 2 の `assign_slots` のテスト）。
3. **タイトルに別の場名が混ざる**: 解説者の名前（「小倉」「松山」など）や「高松宮記念杯」がタイトルに入っていても、チャンネル自身の場、または今日開催している場が優先される（Task 1 のテスト）。
4. **API の失敗**: キーが未設定、403（クォータ切れ）、一部のチャンネルだけ失敗した場合でも、ページは壊れない。見つからない枠は手入力に回る（Task 1・3 のテスト）。
5. **発走予定の取得失敗**: 1開催だけ keirin.jp から取れないとき、その場も選べ、映像も出る。強調と残り時間だけが出ない（Task 3 のテスト）。

---

### Task 1: YouTube の配信検索と場の照合（`youtube.py`）

**Files:**
- Create: `src/keirin/youtube.py`
- Test: `tests/test_youtube.py`

**Interfaces:**
- Produces:
  - `CHANNELS: dict[str, tuple[str, ...]]`: 場コードからチャンネル ID への対応。値は docs/multiview.md の表のとおり。場コードは 函館11 青森12 いわき平13 弥彦21 前橋22 取手23 宇都宮24 大宮25 西武園26 京王閣27 立川28 松戸31 千葉32 川崎34 平塚35 小田原36 伊東37 静岡38 名古屋42 岐阜43 大垣44 豊橋45 富山46 松阪47 四日市48 福井51 奈良53 向日町54 和歌山55 岸和田56 玉野61 広島62 防府63 高松71 小松島73 高知74 松山75 小倉81 久留米83 武雄84 佐世保85 別府86 熊本87。立川・岸和田・広島は2つ。
  - `VENUE_NAMES: dict[str, tuple[str, ...]]`: 場コードからタイトル中の表記（先頭が正式名）への対応。別名に 伊東→("伊東温泉", "伊東")、向日町→("京都向日町", "向日町")、岸和田→("ブッキースタジアム岸和田", "岸和田")、千葉→("千葉", "JPFドーム", "JPF ドーム")、いわき平→("いわき平",) を含める。
  - `@dataclass(frozen=True) class Stream: video_id: str; channel_id: str; title: str; start: datetime; live: bool`。`start` は実際の開始時刻、なければ予定開始時刻（tz つき）。
  - `match_venue(title: str, channel_venue: str, venues_today: set[str]) -> str | None`
  - `pick_streams(streams: list[Stream], meetings: dict[str, tuple[datetime, datetime] | None], day: date) -> dict[str, Stream]`。`meetings` は場コードから（最初のレースの予定発走, 最終レースの予定発走）への対応で、発走予定が取れなかった場は None。
  - `class YouTubeClient: __init__(self, api_key: str, transport: httpx.BaseTransport | None = None)`、`recent_streams(self, channel_ids: Iterable[str], per_channel: int = 8) -> list[tuple[str, Stream]]`。戻り値は（チャンネル ID, Stream）の組。
  - `find_streams(client: YouTubeClient, day: date, meetings: dict[str, tuple[datetime, datetime] | None]) -> dict[str, Stream]`。`meetings` に入っている場（今日開催している場）だけについて探す。
  - `video_id_from_url(url: str) -> str | None`、`embed_url(video_id: str) -> str`。
  - `class YouTubeError(RuntimeError)`

- [ ] **Step 1: ブランチを作り、`.gitignore` に `.superpowers/` を足す**

Run: `git switch -c feat/multiview`。そのあと `.gitignore` の末尾に `.superpowers/` を1行足す。

- [ ] **Step 2: 照合の失敗するテストを書く**

`tests/test_youtube.py` に書く。タイトルは実例の形を架空の値にしたもの。

```python
TODAY = {"61", "47", "13", "11", "75", "71", "87", "81"}

@pytest.mark.parametrize(("title", "channel_venue", "expected"), [
    ("高松市営玉野競輪 F2 初日", "71", "61"),               # 借り上げ: 施行者を無視
    ("【富山市営】松阪競輪F2ミッドナイト 初日", "47", "47"),
    ("【函館inいわき平 実況中継】1日目", "13", "13"),         # X in Y は Y
    ("【競輪予想】熊本G3 決勝 前日予想", "11", None),          # 予想は除外
    ("前検日コメント杯 F2【1日目】", "44", "44"),              # 場名なし → チャンネルの場
    ("モーニング競輪 ラブ杯 in松山 1日目", "71", "75"),
    ("熊本けいりんLIVE 初日 解説 小倉太郎", "87", "87"),        # チャンネル自身の場を優先
    ("高松宮記念杯競輪 岸和田 G1 初日", "56", "56"),            # 高松宮は高松ではない
    ("四日市KEIRIN LIVE 初日", "48", "48"),                    # KEIRIN の in は区切りではない
])
def test_match_venue(title, channel_venue, expected):
    assert match_venue(title, channel_venue, TODAY | {"44", "56"}) == expected
```

規則（テストで決まらない部分）: 「予想」を含むなら None。施行者の表記 `[^\s【】\[\]]{1,6}?(?:市営|県営|町営)` と「高松宮」を取り除く。前後が英字でない「in」（`(?<![A-Za-z])in(?![A-Za-z])`、大文字小文字を問わない）があれば、その後ろだけを見る。残りにチャンネル自身の場名があればそれを返す。なければ、`venues_today` の場のうち、タイトル中で最も前に現れるものを返す。どれもなければ、チャンネル自身の場を返す。

- [ ] **Step 3: テストが失敗することを確かめる**

Run: `uv run pytest tests/test_youtube.py -q`
Expected: FAIL（`ImportError`）

- [ ] **Step 4: `CHANNELS`、`VENUE_NAMES`、`match_venue` を実装する**

- [ ] **Step 5: `pick_streams` の失敗するテストを書く**

```python
def test_pick_prefers_own_channel_and_window():
    jst = timezone(timedelta(hours=9))
    window = (datetime(2026, 10, 21, 21, 0, tzinfo=jst), datetime(2026, 10, 21, 23, 50, tzinfo=jst))
    own = Stream("own00000001", CHANNELS["61"][0], "玉野競輪 初日", window[0] - timedelta(minutes=30), False)
    other = Stream("oth00000001", CHANNELS["71"][0], "高松市営玉野競輪 初日", window[0] - timedelta(minutes=30), True)
    yesterday = Stream("old00000001", CHANNELS["61"][0], "玉野競輪 最終日", window[0] - timedelta(days=1), False)
    early = Stream("erl00000001", CHANNELS["61"][0], "玉野競輪 モーニング", window[0] - timedelta(hours=14), False)
    got = pick_streams([other, own, yesterday, early], {"61": window}, date(2026, 10, 21))
    assert got == {"61": own}

def test_pick_without_schedule_accepts_any_stream_today():
    ...  # meetings={"61": None} のとき、JST で今日に始まる配信なら採る
```

時間の窓は「最初のレースの4時間前〜最終レース」。複数の候補が残ったら、実際の場のチャンネル、ライブ中、開始が早い、の順で選ぶ。

- [ ] **Step 6: `pick_streams` を実装して、ここまでのテストを通す**

Run: `uv run pytest tests/test_youtube.py -q` → PASS

- [ ] **Step 7: `YouTubeClient` と `find_streams` の失敗するテストを書く**

`httpx.MockTransport` で、`playlistItems`（`playlistId` は `"UU" + channel_id[2:]`）と `videos`（`part=snippet,liveStreamingDetails`、`id` はカンマ区切りで50件ずつ）に架空の JSON を返す。確かめること:
- `test_find_streams_request_count`: 今日の場が2つで、合わせてチャンネルが3つなら、playlistItems が3回、videos が1回。全チャンネルで約50ユニットという見積もりに対応する。
- `test_find_streams_skips_finished_and_vod`: `liveStreamingDetails` がない動画（通常の動画）と、`actualEndTime` がある動画（終わったライブ）は候補にしない。
- `test_find_streams_partial_failure`: 1チャンネルの playlistItems が 404 でも、ほかのチャンネルの結果は返る。
- `test_find_streams_quota_error`: videos が 403 なら `YouTubeError` を投げる（ページ側で「見つからない」に回す）。
- `test_video_id_from_url`: `watch?v=`、`youtu.be/`、`/live/`、`/embed/` の4形と、それ以外は None。

- [ ] **Step 8: `YouTubeClient`、`find_streams`、`video_id_from_url`、`embed_url` を実装して、テストを通す**

キーはクエリの `key=` で渡す。タイムアウトは10秒。ログに出す URL にキーを含めない。

Run: `uv run pytest tests/test_youtube.py -q && uv run ruff check . && uv run ruff format --check .` → PASS

- [ ] **Step 9: Commit**

```bash
git add .gitignore src/keirin/youtube.py tests/test_youtube.py
git commit -S -m "feat(multiview): find venue live streams with the YouTube Data API"
```

---

### Task 2: 発走予定からの状態計算と枠の割り当て（`multiview.py` の純粋な関数）

**Files:**
- Create: `src/keirin/multiview.py`
- Test: `tests/test_multiview.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `WAIT_MINUTES = 5`、`MAX_TILES = 4`
  - `@dataclass(frozen=True) class VenueStatus: venue_code: str; race_no: int | None; start: datetime | None; state: Literal["upcoming", "waiting", "finished", "unknown"]`。`unknown` は発走予定がないとき。
  - `start_datetimes(day: date, races: list[dict]) -> list[tuple[int, datetime]]`: `races` は `Store.races` の行（`race_no`、`start_time` が "HH:MM"）。JST の datetime にして、発走予定が空の行は飛ばす。
  - `venue_status(code: str, starts: list[tuple[int, datetime]] | None, now: datetime) -> VenueStatus`
  - `highlighted(statuses: list[VenueStatus]) -> str | None`: 状態が upcoming か waiting の場のうち、`start` が最も早い場。
  - `default_selection(statuses: list[VenueStatus], n: int = 3) -> list[str]`
  - `assign_slots(previous: list[str | None], selected: list[str]) -> list[str | None]`: 長さは常に4。

- [ ] **Step 1: 失敗するテストを書く**

```python
NOW = datetime(2026, 10, 21, 23, 40, tzinfo=JST)

def test_venue_status_boundaries():
    starts = [(9, NOW), (10, NOW + timedelta(minutes=11))]
    assert venue_status("22", starts, NOW).state == "waiting"                      # 予定ちょうど
    assert venue_status("22", starts, NOW + timedelta(minutes=4, seconds=59)).race_no == 9
    s = venue_status("22", starts, NOW + timedelta(minutes=5))                     # 5分ちょうどで次へ
    assert (s.race_no, s.state) == (10, "upcoming")
    assert venue_status("22", starts, NOW + timedelta(minutes=16)).state == "finished"
    assert venue_status("22", None, NOW).state == "unknown"

def test_highlighted_skips_finished_and_unknown(): ...
    # finished・unknown しかないなら None

def test_default_selection_orders_by_next_start():
    # 4場（うち1つは finished）から、発走が近い順に3場。finished は選ばない

def test_assign_slots_keeps_positions():
    assert assign_slots([None] * 4, ["22", "47", "13"]) == ["22", "47", "13", None]
    assert assign_slots(["22", "47", "13", None], ["22", "13", "81"]) == ["22", "81", "13", None]
    assert assign_slots(["22", "81", "13", None], ["22", "81", "13", "11"]) == ["22", "81", "13", "11"]
```

- [ ] **Step 2: テストが失敗することを確かめる**

Run: `uv run pytest tests/test_multiview.py -q` → FAIL（`ImportError`）

- [ ] **Step 3: 関数を実装する**（Streamlit はまだ import しない。Task 3 で同じファイルに足す）

- [ ] **Step 4: テストを通す**

Run: `uv run pytest tests/test_multiview.py -q && uv run ruff check . && uv run ruff format --check .` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/keirin/multiview.py tests/test_multiview.py
git commit -S -m "feat(multiview): compute next starts and stable tile slots"
```

---

### Task 3: マルチビューのページとナビゲーション

**Files:**
- Modify: `src/keirin/multiview.py`（ページを追加）
- Modify: `src/keirin/viewer.py`（`main()` を `st.navigation` に）
- Create: `tests/multiview_script.py`
- Test: `tests/test_multiview.py`（AppTest を追加）

**Interfaces:**
- Consumes: Task 1 の `find_streams`、`YouTubeClient`、`YouTubeError`、`Stream`、`video_id_from_url`、`embed_url`。Task 2 の全部。既存の `Store.meetings(day)`、`Store.races(day, venue_code)`、`TIME_SLOTS`（viewer.py）。
- Produces:
  - `render_multiview(store: Store, now: datetime, streams: Callable[[dict[str, tuple[datetime, datetime] | None]], dict[str, Stream]] | None) -> None`。`streams` が None なのは API キーがないとき。
  - `viewer.main()` は「出走表」（既存の `render`）と「マルチビュー」の2ページを持つ。

- [ ] **Step 1: AppTest の失敗するテストを書く**

`tests/multiview_script.py` は `viewer_script.py` と同じ作りにする。`FakeClient`（conftest）の開催を使い、時刻は環境変数で固定し、`streams` は架空の関数（環境変数で、1場だけ見つかる・`YouTubeError` を投げる・None のいずれかにする）を渡す。テスト:
- `test_page_defaults_and_missing_stream`: 例外なし。multiselect の初期値は発走が近い順の場。見つからない場の枠に「配信が見つかりません」と URL 入力欄がある。
- `test_page_without_api_key`: `streams=None` で「API キーが未設定」と出て、URL 入力欄は使える。
- `test_page_api_error_falls_back`: `YouTubeError` でも例外にならず、全枠が手入力に回る。
- `test_page_schedule_failure`: 1開催の `races` が `KeirinApiError` を投げても、その場は選べて、場を選ぶ欄に「発走予定を取得できません」と出る。
- `test_manual_url_overrides`: 見つかった枠の「配信を差し替え」に URL を入れると、その枠の埋め込み URL が貼った動画になる。配信の検索を15分ごとにやり直しても（`streams` を呼び直しても）戻らない。

- [ ] **Step 2: テストが失敗することを確かめる**

Run: `uv run pytest tests/test_multiview.py -q` → FAIL

- [ ] **Step 3: `render_multiview` を実装する**

決めてあること:
- 場を選ぶ欄は、ページ上部の `st.multiselect(max_selections=MAX_TILES)`。各場のラベルは「場名 時間帯 次 nR HH:MM」、取得に失敗した場は「発走予定を取得できません」。
- 発走予定は `@st.cache_data(ttl=86400)` で、`(day, venue_code)` ごとに1回取る（引数の store は `_store` にしてハッシュしない）。例外はキャッシュされないので、失敗した場は次の再実行で取り直す。
- 配信は `@st.cache_data(ttl=900)` で `find_streams` を包む。`YouTubeError` はログに出して空の結果にする。
- 枠の割り当ては `st.session_state["mv_slots"]` に `assign_slots` で保つ。手で貼った URL は `st.session_state["mv_manual"][venue_code]` に保ち、自動の結果より優先する。
- グリッドは `st.columns(2)` を2段。枠ごとに、見出し（`@st.fragment(run_every=30)` の関数。場名、時間帯、R、「予定 HH:MM」、残り時間か「発走待ち」。`highlighted` の場は背景色を変える）、`st.iframe(embed_url(...), height=...)`、`st.popover("配信を差し替え")` の中の URL 入力。3場以下なら、空いた枠に「次の発走」の一覧（映している場だけ。これも fragment）を置く。4場なら、一覧をグリッドの上に置く。
- **映像の要素は fragment の外に置き**、見出しの更新で作り直されないようにする。

- [ ] **Step 4: `viewer.main()` を `st.navigation` にする**

`st.Page(lambda: render(...), title="出走表", default=True)` と `st.Page(..., title="マルチビュー", url_path="multiview")`。API キーは `st.secrets.get("YOUTUBE_API_KEY")`（secrets.toml がないときの例外は握りつぶす）、なければ環境変数 `YOUTUBE_API_KEY`。既存の `tests/test_viewer.py` が変わらず通ること。

- [ ] **Step 5: テストを通す**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` → PASS（既存のテストも含めて全部）

- [ ] **Step 6: Commit**

```bash
git add src/keirin/multiview.py src/keirin/viewer.py tests/multiview_script.py tests/test_multiview.py
git commit -S -m "feat(viewer): add the multiview page for venue live streams"
```

---

### Task 4: 実機での確認、ドキュメント、PR

**Files:**
- Modify: `CLAUDE.md`（要望の9番目、決定ログ、ディレクトリ、TODO）
- Modify: `docs/multiview.md`（冒頭の「実装はこれから」を実装済みに。確認の結果を追記）
- Modify: `docs/deploy-streamlit.md`（Secrets に `YOUTUBE_API_KEY` を登録する手順）

- [ ] **Step 1: 実際のキーで配信の検索を確かめる**

オーナーにキーを環境変数で渡してもらい（チャットには貼らない）、ローカルで起動する:
`KEIRIN_DATA_DIR=../keirin-data YOUTUBE_API_KEY=… uv run streamlit run src/keirin/viewer.py`
確かめること: (a) 予約中の配信がアップロード再生リストに出てくるか（出てこなければ、Spec の方法の見直しをオーナーに相談する）、(b) 今日の開催の場がそれぞれ正しい配信に対応しているか、(c) API の使用量（Google Cloud のコンソールで、ページを1回開くと約50ユニット）。

- [ ] **Step 2: ブラウザで「やってはいけないこと」を確かめる**

Chrome で、3場を映した状態で次を確かめる。(1) 30秒以上待っても、見出しの更新で映像が止まらない。(2) 1場を外して別の場を足しても、残した2場の映像が止まらない。(3) どの枠もミュートで始まり、アプリが音を変えない。止まる場合は Task 3 の作りを直してから進む。

- [ ] **Step 3: ドキュメントを更新する**

- [ ] **Step 4: Commit、push、PR**

```bash
git add CLAUDE.md docs/multiview.md docs/deploy-streamlit.md
git commit -S -m "docs: record the multiview feature"
git push -u origin feat/multiview
gh pr create --title "feat(viewer): add the multiview page for venue live streams" --body "..."
```

CI が通ったら、CLAUDE.md の手順でマージする。

- [ ] **Step 5: データリポジトリの requirements.txt の SHA を更新する PR を出し、マージする**

docs/deploy-streamlit.md の手順に従う。オーナーに、Streamlit の Secrets に `YOUTUBE_API_KEY` を登録してもらう。
