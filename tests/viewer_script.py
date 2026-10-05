"""Streamlit script used by test_viewer.py (AppTest runs scripts, not functions)."""

import os
from datetime import date
from pathlib import Path

import numpy as np
from conftest import MEETING_ENC, FakeClient, race_enc

from keirin.client import KeirinApiError
from keirin.predict import CARD_FEATURES, N_PARAMS, Model
from keirin.store import CachedClient, Store
from keirin.viewer import render

client = FakeClient()
client.responses[("JSJ001", MEETING_ENC)]["C0201data"]["C0201race"] = [{"encParaR": race_enc(1)}]
client.responses[("JSJ006", race_enc(1))]["sensyuTypeInfo"][0]["kyuhan"] = "S2"
if os.environ.get("KEIRIN_TEST_NINFO"):
    client.responses[("JSJ017", MEETING_ENC)]["rInfo"][0].update(
        {
            "nInfo": [
                {"syaban": 1, "narabiX": 3, "narabiY": 1},
                {"syaban": 2, "narabiX": 1, "narabiY": 1},
                {"syaban": 3, "narabiX": 3, "narabiY": 2},
            ],
            "line": "細切れ",
        }
    )
if os.environ.get("KEIRIN_TEST_FINISHED"):
    client.responses[("JSJ001", MEETING_ENC)]["C0201data"]["C0201race"][0]["rcvKekka"] = "1"
if os.environ.get("KEIRIN_TEST_FAIL"):

    def failing_get(type_, **params):
        raise KeirinApiError(f"{type_}: HTTP 403")

    client.get = failing_get
# A prediction model that weighs the score only (the fake race is Ａ級).
params = np.zeros(N_PARAMS)
params[CARD_FEATURES.index("score")] = 1.0
model = (
    None if os.environ.get("KEIRIN_TEST_NO_MODEL") else Model({"A": params}, {"A": 1}, None, None)
)
render(
    Store(Path(os.environ["KEIRIN_TEST_DATA_DIR"]), CachedClient(client)),
    date(2026, 1, 10),
    lambda: model,
)
