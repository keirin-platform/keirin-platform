"""Streamlit script used by test_viewer.py (AppTest runs scripts, not functions)."""

import os
from datetime import date
from pathlib import Path

from conftest import MEETING_ENC, FakeClient, race_enc

from keirin.client import KeirinApiError
from keirin.store import CachedClient, Store
from keirin.viewer import render

client = FakeClient()
client.responses[("JSJ001", MEETING_ENC)]["C0201data"]["C0201race"] = [{"encParaR": race_enc(1)}]
client.responses[("JSJ006", race_enc(1))]["sensyuTypeInfo"][0]["kyuhan"] = "S2"
if os.environ.get("KEIRIN_TEST_FAIL"):

    def failing_get(type_, **params):
        raise KeirinApiError(f"{type_}: HTTP 403")

    client.get = failing_get
render(Store(Path(os.environ["KEIRIN_TEST_DATA_DIR"]), CachedClient(client)), date(2026, 1, 10))
