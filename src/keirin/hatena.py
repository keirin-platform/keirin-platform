"""Publish articles of the blog repository (keirin-blog) to Hatena Blog.

Repository layout:

    articles/<slug>/index.md      TOML front matter (+++) and the Markdown body
    articles/<slug>/*.png         images referenced as ![alt](chart.png)
    articles/<slug>/hatena.json   state managed by the workflow (never edit by hand)

Flow (mirrors the PR workflow of the other repositories):
- pull request -> ``mode="preview"``: every changed article that is not published
  yet is pushed as a **draft** with a preview URL; published articles are left
  alone so that unmerged edits never reach the live blog.
- merge to main -> ``mode="publish"``: changed articles are pushed with the
  front matter's ``draft`` flag (``draft = false`` publishes / updates live).

Local images are uploaded to Hatena Fotolife (once per content hash) and the
Markdown image references are replaced by Fotolife syntax in the text sent to
Hatena; the files in the repository are not rewritten. The state file is
committed back through the GitHub API by the workflow, so commits are signed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
import secrets
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger(__name__)

ATOM = "http://www.w3.org/2005/Atom"
APP = "http://www.w3.org/2007/app"
HATENA = "http://www.hatena.ne.jp/info/xmlns#"
ARTICLES_DIR = "articles"
STATE_FILE = "hatena.json"
IMAGE_REF = re.compile(r'!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+"(?P<title>[^"]*)")?\)')


class ArticleError(ValueError):
    pass


# --- articles ----------------------------------------------------------------------


@dataclass
class Article:
    dir: Path
    title: str
    categories: list[str]
    draft: bool
    body: str
    state: dict[str, Any]

    @classmethod
    def load(cls, article_dir: Path) -> Article:
        text = (article_dir / "index.md").read_text(encoding="utf-8")
        m = re.match(r"\A\+\+\+\n(.*?)\n\+\+\+\n?(.*)\Z", text, re.S)
        if not m:
            raise ArticleError(f"{article_dir}: index.md must start with TOML front matter (+++)")
        try:
            meta = tomllib.loads(m.group(1))
        except tomllib.TOMLDecodeError as e:
            raise ArticleError(f"{article_dir}: invalid front matter: {e}") from e
        if not str(meta.get("title", "")).strip():
            raise ArticleError(f"{article_dir}: front matter needs a title")
        state_path = article_dir / STATE_FILE
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        return cls(
            article_dir,
            str(meta["title"]).strip(),
            [str(c) for c in meta.get("categories", [])],
            bool(meta.get("draft", True)),
            m.group(2),
            state,
        )

    def save_state(self) -> None:
        text = json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (self.dir / STATE_FILE).write_text(text, encoding="utf-8")

    def render(self, upload: Callable[[Path], str]) -> str:
        """The body with local images replaced by Fotolife syntax (uploading new ones)."""
        images = self.state.setdefault("images", {})

        def replace(m: re.Match) -> str:
            ref = m.group("path")
            path = (self.dir / ref).resolve()
            if "://" in ref or not path.is_file() or self.dir.resolve() not in path.parents:
                return m.group(0)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            cached = images.get(ref)
            if not cached or cached.get("sha256") != digest:
                cached = {"sha256": digest, "syntax": upload(path)}
                images[ref] = cached
            options = "".join(
                f":{key}={value}"
                for key, value in (("title", m.group("title")), ("alt", m.group("alt")))
                if value
            )
            return f"[{cached['syntax']}{options}]"

        return IMAGE_REF.sub(replace, self.body)


def changed_articles(repo_dir: Path, base: str | None) -> list[Path]:
    """Article directories touched since `base` (all articles when base is unknown)."""
    root = repo_dir / ARTICLES_DIR
    every = sorted(
        p.parent for p in root.glob("*/index.md") if not p.parent.name.startswith(("_", "."))
    )
    if not base or set(base) == {"0"}:
        return every
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--name-only", base, "HEAD", "--", ARTICLES_DIR],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        log.warning("cannot diff against %s, syncing every article", base)
        return every
    touched = {repo_dir / ARTICLES_DIR / Path(line).parts[1] for line in out.split() if line}
    return [d for d in every if d in touched]


# --- Hatena APIs ---------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    edit_url: str
    url: str
    preview_url: str
    draft: bool


class HatenaBlog:
    """AtomPub client of one blog (Basic auth with the owner's API key, https only)."""

    def __init__(
        self,
        hatena_id: str,
        blog_domain: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.hatena_id = hatena_id
        self.collection = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_domain}/atom/entry"
        self._http = httpx.Client(auth=(hatena_id, api_key), timeout=60, transport=transport)

    def save(
        self,
        *,
        title: str,
        body: str,
        categories: list[str],
        draft: bool,
        edit_url: str | None = None,
        updated: str | None = None,
    ) -> Entry:
        ET.register_namespace("", ATOM)
        ET.register_namespace("app", APP)
        entry = ET.Element(f"{{{ATOM}}}entry")
        ET.SubElement(entry, f"{{{ATOM}}}title").text = title
        ET.SubElement(
            ET.SubElement(entry, f"{{{ATOM}}}author"), f"{{{ATOM}}}name"
        ).text = self.hatena_id
        ET.SubElement(entry, f"{{{ATOM}}}content", type="text/x-markdown").text = body
        if updated:
            ET.SubElement(entry, f"{{{ATOM}}}updated").text = updated
        for category in categories:
            ET.SubElement(entry, f"{{{ATOM}}}category", term=category)
        control = ET.SubElement(entry, f"{{{APP}}}control")
        ET.SubElement(control, f"{{{APP}}}draft").text = "yes" if draft else "no"
        ET.SubElement(control, f"{{{APP}}}preview").text = "yes" if draft else "no"
        payload = ET.tostring(entry, encoding="utf-8", xml_declaration=True)
        headers = {"Content-Type": "application/xml; charset=utf-8"}
        if edit_url:
            resp = self._http.put(edit_url, content=payload, headers=headers)
        else:
            resp = self._http.post(self.collection, content=payload, headers=headers)
        resp.raise_for_status()
        return _parse_entry(resp.content)


def _parse_entry(xml: bytes) -> Entry:
    root = ET.fromstring(xml)
    links = {link.get("rel"): link.get("href", "") for link in root.findall(f"{{{ATOM}}}link")}
    draft = root.findtext(f"{{{APP}}}control/{{{APP}}}draft", default="no").strip() == "yes"
    return Entry(links.get("edit", ""), links.get("alternate", ""), links.get("preview", ""), draft)


class Fotolife:
    """Image upload to Hatena Fotolife (legacy AtomPub with WSSE authentication)."""

    URL = "https://f.hatena.ne.jp/atom/post"

    def __init__(
        self,
        hatena_id: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        nonce: Callable[[], bytes] = lambda: secrets.token_bytes(20),
    ) -> None:
        self.hatena_id = hatena_id
        self._api_key = api_key
        self._clock = clock
        self._nonce = nonce
        self._http = httpx.Client(timeout=120, transport=transport)

    def wsse(self) -> str:
        nonce = self._nonce()
        created = self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = hashlib.sha1(nonce + created.encode() + self._api_key.encode()).digest()
        return (
            f'UsernameToken Username="{self.hatena_id}", '
            f'PasswordDigest="{base64.b64encode(digest).decode()}", '
            f'Nonce="{base64.b64encode(nonce).decode()}", Created="{created}"'
        )

    def upload(self, path: Path) -> str:
        """Upload an image and return its Fotolife syntax, e.g. f:id:user:20261103120000p:plain."""
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        entry = ET.Element("entry", xmlns="http://purl.org/atom/ns#")
        ET.SubElement(entry, "title").text = path.name
        ET.SubElement(entry, "content", mode="base64", type=mime).text = base64.b64encode(
            path.read_bytes()
        ).decode()
        ET.SubElement(entry, "generator").text = "keirin-platform"
        resp = self._http.post(
            self.URL,
            content=ET.tostring(entry, encoding="utf-8", xml_declaration=True),
            headers={"X-WSSE": self.wsse(), "Content-Type": "application/xml"},
        )
        resp.raise_for_status()
        syntax = ET.fromstring(resp.content).findtext(f"{{{HATENA}}}syntax")
        if not syntax:
            raise RuntimeError(f"Fotolife returned no syntax for {path.name}")
        return re.sub(r":image$", ":plain", syntax.strip())


# --- sync ----------------------------------------------------------------------------


def sync_article(
    article: Article,
    blog: HatenaBlog,
    fotolife: Fotolife,
    mode: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """Push one article; returns True when Hatena was updated (state may have changed)."""
    state = article.state
    if mode == "preview":
        if state.get("published"):
            log.info("%s: published, preview skipped (changes go live on merge)", article.dir.name)
            return False
        draft = True
    elif mode == "publish":
        draft = article.draft
    else:
        raise ValueError(f"unknown mode: {mode}")

    updated = None
    if not draft:
        # Keep the original post date when updating a published article.
        state.setdefault("published_at", now().isoformat(timespec="seconds"))
        updated = state["published_at"]
    entry = blog.save(
        title=article.title,
        body=article.render(fotolife.upload),
        categories=article.categories,
        draft=draft,
        edit_url=state.get("edit_url"),
        updated=updated,
    )
    state["edit_url"] = entry.edit_url
    state["published"] = not entry.draft
    if entry.draft:
        state["preview_url"] = entry.preview_url
    else:
        state.pop("preview_url", None)
        state["url"] = entry.url
    article.save_state()
    log.info(
        "%s: %s %s",
        article.dir.name,
        "draft" if entry.draft else "published",
        state.get("url") or state.get("preview_url") or entry.edit_url,
    )
    return True
