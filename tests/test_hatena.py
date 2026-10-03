import base64
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx
import pytest

from keirin import hatena
from keirin.hatena import Article, ArticleError, Fotolife, HatenaBlog, sync_article

ATOM = "{http://www.w3.org/2005/Atom}"
APP = "{http://www.w3.org/2007/app}"
EDIT = "https://blog.hatena.ne.jp/me/example.hatenablog.com/atom/entry/123"
PNG = b"\x89PNG fake image"


def write_article(root, slug="score-analysis", draft=True, body=None):
    d = root / "articles" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.md").write_text(
        "+++\n"
        'title = "競走得点の分析"\n'
        'categories = ["競輪", "データ分析"]\n'
        f"draft = {'true' if draft else 'false'}\n"
        "+++\n" + (body or '本文です。\n\n![級班別の分布](dist.png "分布")\n'),
        encoding="utf-8",
    )
    (d / "dist.png").write_bytes(PNG)
    return d


class FakeHatena:
    """Answers the blog AtomPub API and Fotolife like Hatena does."""

    def __init__(self):
        self.entries = []  # (method, url, parsed XML)
        self.uploads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "f.hatena.ne.jp":
            self.uploads += 1
            assert request.headers["x-wsse"].startswith('UsernameToken Username="me"')
            return httpx.Response(
                201,
                content=(
                    '<entry xmlns="http://purl.org/atom/ns#" '
                    'xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#">'
                    f"<hatena:syntax>f:id:me:2026110312000{self.uploads}p:image</hatena:syntax>"
                    "</entry>"
                ).encode(),
            )
        sent = ET.fromstring(request.content)
        self.entries.append((request.method, str(request.url), sent))
        draft = sent.findtext(f"{APP}control/{APP}draft")
        links = f'<link rel="edit" href="{EDIT}"/>'
        if draft == "yes":
            links += '<link rel="preview" href="https://example.hatenablog.com/preview/1"/>'
        else:
            links += (
                '<link rel="alternate" type="text/html" '
                'href="https://example.hatenablog.com/entry/2026/11/03/120000"/>'
            )
        return httpx.Response(
            201 if request.method == "POST" else 200,
            content=(
                '<entry xmlns="http://www.w3.org/2005/Atom" xmlns:app="http://www.w3.org/2007/app">'
                f"{links}<app:control><app:draft>{draft}</app:draft></app:control></entry>"
            ).encode(),
        )


@pytest.fixture
def fake():
    return FakeHatena()


def clients(fake):
    transport = httpx.MockTransport(fake)
    blog = HatenaBlog("me", "example.hatenablog.com", "key", transport=transport)
    return blog, Fotolife("me", "key", transport=transport)


def test_load_article_and_validation(tmp_path):
    article = Article.load(write_article(tmp_path))
    assert article.title == "競走得点の分析"
    assert article.categories == ["競輪", "データ分析"]
    assert article.draft is True
    assert article.state == {}

    bad = tmp_path / "articles" / "bad"
    bad.mkdir(parents=True)
    (bad / "index.md").write_text("no front matter", encoding="utf-8")
    with pytest.raises(ArticleError, match="front matter"):
        Article.load(bad)
    (bad / "index.md").write_text("+++\ndraft = true\n+++\nbody", encoding="utf-8")
    with pytest.raises(ArticleError, match="title"):
        Article.load(bad)


def test_preview_creates_a_draft_and_uploads_images_once(tmp_path, fake):
    d = write_article(tmp_path)
    blog, fotolife = clients(fake)

    assert sync_article(Article.load(d), blog, fotolife, "preview")
    method, url, sent = fake.entries[0]
    assert (method, url) == ("POST", blog.collection)
    assert sent.findtext(f"{ATOM}title") == "競走得点の分析"
    content = sent.find(f"{ATOM}content")
    assert content.get("type") == "text/x-markdown"
    assert "[f:id:me:20261103120001p:plain:title=分布:alt=級班別の分布]" in content.text
    assert [c.get("term") for c in sent.findall(f"{ATOM}category")] == ["競輪", "データ分析"]
    assert sent.findtext(f"{APP}control/{APP}draft") == "yes"
    assert sent.findtext(f"{APP}control/{APP}preview") == "yes"
    assert sent.find(f"{ATOM}updated") is None

    state = json.loads((d / "hatena.json").read_text(encoding="utf-8"))
    assert state["edit_url"] == EDIT
    assert state["published"] is False
    assert state["preview_url"] == "https://example.hatenablog.com/preview/1"
    assert state["images"]["dist.png"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    # The Markdown in the repository is not rewritten.
    assert "![級班別の分布](dist.png" in (d / "index.md").read_text(encoding="utf-8")

    # A second sync updates the same entry and reuses the uploaded image.
    sync_article(Article.load(d), blog, fotolife, "preview")
    assert fake.entries[1][:2] == ("PUT", EDIT)
    assert fake.uploads == 1

    (d / "dist.png").write_bytes(PNG + b"v2")
    sync_article(Article.load(d), blog, fotolife, "preview")
    assert fake.uploads == 2


def test_publish_follows_draft_flag_and_keeps_the_post_date(tmp_path, fake):
    d = write_article(tmp_path, draft=False)
    blog, fotolife = clients(fake)
    first = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    sync_article(Article.load(d), blog, fotolife, "publish", now=lambda: first)
    sent = fake.entries[0][2]
    assert sent.findtext(f"{APP}control/{APP}draft") == "no"
    assert sent.findtext(f"{ATOM}updated") == "2026-11-03T12:00:00+00:00"
    state = json.loads((d / "hatena.json").read_text(encoding="utf-8"))
    assert state["published"] is True
    assert state["url"] == "https://example.hatenablog.com/entry/2026/11/03/120000"
    assert "preview_url" not in state

    later = datetime(2026, 11, 10, tzinfo=UTC)
    sync_article(Article.load(d), blog, fotolife, "publish", now=lambda: later)
    assert fake.entries[1][2].findtext(f"{ATOM}updated") == "2026-11-03T12:00:00+00:00"


def test_preview_never_touches_published_articles(tmp_path, fake):
    d = write_article(tmp_path, draft=False)
    blog, fotolife = clients(fake)
    sync_article(Article.load(d), blog, fotolife, "publish")
    assert not sync_article(Article.load(d), blog, fotolife, "preview")
    assert len(fake.entries) == 1


def test_publish_keeps_a_draft_when_draft_is_true(tmp_path, fake):
    d = write_article(tmp_path, draft=True)
    blog, fotolife = clients(fake)
    sync_article(Article.load(d), blog, fotolife, "publish")
    assert fake.entries[0][2].findtext(f"{APP}control/{APP}draft") == "yes"


def test_remote_and_missing_images_are_left_as_is(tmp_path, fake):
    body = "![a](https://example.com/x.png)\n![b](missing.png)\n![c](../../escape.png)\n"
    d = write_article(tmp_path, body=body)
    (tmp_path / "escape.png").write_bytes(PNG)
    blog, fotolife = clients(fake)
    sync_article(Article.load(d), blog, fotolife, "preview")
    assert fake.entries[0][2].findtext(f"{ATOM}content") == body
    assert fake.uploads == 0


def test_wsse_header():
    created = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    fotolife = Fotolife("me", "secret", clock=lambda: created, nonce=lambda: b"n" * 20)
    header = fotolife.wsse()
    digest = hashlib.sha1(b"n" * 20 + b"2026-11-03T12:00:00Z" + b"secret").digest()
    assert header == (
        f'UsernameToken Username="me", PasswordDigest="{base64.b64encode(digest).decode()}", '
        f'Nonce="{base64.b64encode(b"n" * 20).decode()}", Created="2026-11-03T12:00:00Z"'
    )


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_changed_articles(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    a = write_article(tmp_path, "a")
    b = write_article(tmp_path, "b")
    write_article(tmp_path, "_template")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    base = git(tmp_path, "rev-parse", "HEAD")
    (b / "index.md").write_text((b / "index.md").read_text() + "追記\n", encoding="utf-8")
    git(tmp_path, "commit", "-qam", "edit b")

    assert hatena.changed_articles(tmp_path, base) == [b]
    assert hatena.changed_articles(tmp_path, None) == [a, b]  # templates are skipped
    assert hatena.changed_articles(tmp_path, "0" * 40) == [a, b]
    assert hatena.changed_articles(tmp_path, "no-such-rev") == [a, b]


def test_api_errors_include_hatenas_explanation(tmp_path):
    def handler(request):
        return httpx.Response(403, text="<error>Forbidden: invalid credentials</error>")

    d = write_article(tmp_path)
    transport = httpx.MockTransport(handler)
    blog = HatenaBlog("me", "example.hatenablog.com", "key", transport=transport)
    fotolife = Fotolife("me", "key", transport=transport)
    with pytest.raises(
        hatena.HatenaApiError, match="Fotolife: HTTP 403 Forbidden.*invalid credentials"
    ):
        sync_article(Article.load(d), blog, fotolife, "preview")
