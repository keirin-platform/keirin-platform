"""Race card viewer with class-change corrected scores.

Past days that have been collected are served from the tables in KEIRIN_DATA_DIR;
other days are fetched from keirin.jp on demand (cached, one request at a time).
On Render the service is deployed from the data repository itself, so every data
commit redeploys it with fresh tables (see infra/data-repo/render.yaml).

Run locally:
    KEIRIN_WEB_AUTH=off KEIRIN_DATA_DIR=../keirin-data \\
        uv run uvicorn --factory keirin.web.app:create_app_from_env --reload
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from keirin.client import KeirinApiError, KeirinClient
from keirin.score import STEP_DELTAS, window_start
from keirin.timeutil import today_jst
from keirin.web.store import ApiClient, CachedClient, NotFound, Store

log = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
WEEKDAYS = "月火水木金土日"
PUBLIC_PATHS = {"/healthz"}


class BasicAuth:
    """Minimal HTTP Basic auth (ASGI middleware); the viewer is for personal use only."""

    def __init__(self, app, username: str, password: str) -> None:
        self.app = app
        self.expected = (username.encode(), password.encode())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS or self._ok(scope):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse(
            "authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="keirin-platform"'},
        )
        await response(scope, receive, send)

    def _ok(self, scope) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"")
        if not header.lower().startswith(b"basic "):
            return False
        try:
            user, _, password = base64.b64decode(header[6:]).partition(b":")
        except (binascii.Error, ValueError):
            return False
        return secrets.compare_digest(user, self.expected[0]) & secrets.compare_digest(
            password, self.expected[1]
        )


def create_app(
    data_dir: Path,
    client: ApiClient | None,
    *,
    username: str | None = None,
    password: str | None = None,
) -> FastAPI:
    store = Store(data_dir, CachedClient(client) if client else None)
    app = FastAPI(title="keirin-platform", docs_url=None, redoc_url=None)
    app.state.store = store

    def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            template,
            {"last_collected": store.last_collected, "deltas": STEP_DELTAS, **context},
            status_code=status_code,
        )

    def day_context(day: date) -> dict:
        return {
            "day": day,
            "weekday": WEEKDAYS[day.weekday()],
            "prev_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1),
            "today": today_jst(),
            "collected": store.is_collected(day),
        }

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound):
        return render(request, "error.html", message=str(exc), status_code=404)

    @app.exception_handler(KeirinApiError)
    async def upstream_error(request: Request, exc: KeirinApiError):
        message = f"keirin.jp の取得に失敗しました: {exc}"
        return render(request, "error.html", message=message, status_code=502)

    @app.get("/healthz")
    def healthz() -> Response:
        return PlainTextResponse("ok")

    @app.get("/")
    def index() -> Response:
        return RedirectResponse(f"/d/{today_jst().isoformat()}")

    @app.get("/d/{day}", response_class=HTMLResponse)
    def day_view(request: Request, day: date):
        return render(request, "day.html", meetings=store.meetings(day), **day_context(day))

    @app.get("/d/{day}/{venue}", response_class=HTMLResponse)
    def meeting_view(request: Request, day: date, venue: str):
        return render(
            request,
            "meeting.html",
            meeting=store.meeting(day, venue),
            races=store.races(day, venue),
            **day_context(day),
        )

    @app.get("/d/{day}/{venue}/{race_no}", response_class=HTMLResponse)
    def race_view(request: Request, day: date, venue: str, race_no: int):
        meeting, race, rows = store.card(day, venue, race_no)
        return render(
            request,
            "race.html",
            meeting=meeting,
            race=race,
            rows=rows,
            window_start=window_start(day),
            race_nos=[r["race_no"] for r in store.races(day, venue)],
            **day_context(day),
        )

    if username and password:
        app.add_middleware(BasicAuth, username=username, password=password)
    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    username = os.environ.get("KEIRIN_WEB_USER")
    password = os.environ.get("KEIRIN_WEB_PASSWORD")
    if not (username and password) and os.environ.get("KEIRIN_WEB_AUTH") != "off":
        raise RuntimeError(
            "set KEIRIN_WEB_USER and KEIRIN_WEB_PASSWORD (or KEIRIN_WEB_AUTH=off for local use)"
        )
    return create_app(
        Path(os.environ.get("KEIRIN_DATA_DIR", "../keirin-data")),
        KeirinClient(min_interval=1.0),
        username=username,
        password=password,
    )
