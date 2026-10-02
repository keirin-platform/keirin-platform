"""Synthetic API responses shaped like the real KEIRIN.JP JSON.

Names and numbers are made up on purpose: this repository is public and must
not contain collected data.
"""

from __future__ import annotations

from typing import Any

import pytest

DAY = "2026-01-10"
MEETING_ENC = "enc-meeting-A"


def race_enc(no: int) -> str:
    return f"enc-race-{no}"


def jsj057() -> dict[str, Any]:
    return {
        "kInfo": [
            {
                "jyoName": "テスト",
                "zenjituFlg": 0,
                "gradeIconChar": "F1",
                "nitijiIconChar": "初日",
                "kaisaiIconChar": "3",
                "encPrm": MEETING_ENC,
                "KeirinCd": "99",
            }
        ],
        "resultCd": 0,
    }


def jsj001() -> dict[str, Any]:
    return {"C0201data": {"raceName": "テスト杯", "joName": "テスト競輪場"}, "resultCd": 0}


def jsj017() -> dict[str, Any]:
    return {
        "kaisaihi": "20260110",
        "keirinCd": "99",
        "rInfo": [
            {
                "raceNo": 1,
                "syumoku": "Ａ級予選",
                "denTime": "15:20",
                "stTime": "15:25",
                "sInfo": [
                    {
                        "syaban": 1,
                        "senNo": "000001",
                        "senName": "山田　一郎",
                        "huken": "東　京",
                        "kyaku": "逃",
                    },
                    {
                        "syaban": 2,
                        "senNo": "000002",
                        "senName": "鈴木　二郎",
                        "huken": "大　阪",
                        "kyaku": "追",
                    },
                    {
                        "syaban": 3,
                        "senNo": "000003",
                        "senName": "佐藤　三郎",
                        "huken": "福　岡",
                        "kyaku": "両",
                    },
                ],
            }
        ],
        "resultCd": 0,
    }


def jsj018() -> dict[str, Any]:
    return {
        "kday": "20260110",
        "bkcd": "99",
        "resultList": [
            {"rclblRaceNo": "1R", "rclblSyumokuName": "Ａ級予選", "raceRVPrm": race_enc(1)}
        ],
        "resultCd": 0,
    }


def _card(car_no: int, racer_id: str, name: str, pref: str, style: str) -> dict[str, Any]:
    return {
        "syaban": str(car_no),
        "sensyuRegistNo": racer_id,
        "sensyuName": name,
        "huKen": pref,
        "prevKyuhan": "A1",
        "kyuhan": "A1",
        "kyakusitu": style,
        "sotugyouki": "100",
        "age": "30",
        "heikinTokuten": "90.25",
        "nigeCnt": "3",
        "makuriCnt": "1",
        "sasiCnt": "0",
        "markCnt": "2",
        "backCnt": "4",
        "homeTori": "1",
        "stTori": "2",
        "syouritu": "25",
        "rentairitu2": "40",
        "rentairitu3": "55",
    }


def jsj006() -> dict[str, Any]:
    # Car 3 is deliberately missing from the card to exercise the fallback path.
    return {
        "syusouInfoExistFlg": "1",
        "sensyuTypeInfo": [
            _card(1, "000001", "山田　一郎", "東　京", "逃"),
            _card(2, "000002", "鈴木　二郎", "大　阪", "追"),
        ],
        "resultCd": 0,
    }


def jsj012() -> dict[str, Any]:
    return {
        "tenki": "晴",
        "husoku": "1.5",
        "tyakujyunItemSubData": [
            {
                "tyaku": "1",
                "syaban": "2",
                "sensyuRegistNo": "000002",
                "tyakusa": "",
                "agari": "11.2",
                "kimarite": "差し",
                "BH": "",
                "kojinStateItemSubData": [],
            },
            {
                "tyaku": "2",
                "syaban": "1",
                "sensyuRegistNo": "000001",
                "tyakusa": "1/2車輪",
                "agari": "11.5",
                "kimarite": "逃げ",
                "BH": "HB",
                "kojinStateItemSubData": [{"kojinState": "", "tyakuNote": ""}],
            },
            {
                "tyaku": "失",
                "syaban": "3",
                "sensyuRegistNo": "000003",
                "tyakusa": "",
                "agari": "",
                "kimarite": "",
                "BH": "",
                "kojinStateItemSubData": [{"kojinState": "失格", "tyakuNote": "斜行"}],
            },
        ],
        "haraiGakuSubData": {
            "WH2HaraiGakuDispItemSubData": [{"haraiGaku": "【未発売】"}],
            # Deliberately not in canonical order (sorting the keys changes the order).
            "WHaraiGakuDispItemSubData": [
                {"kumiBan": "1=2", "haraiGaku": "150", "ninki": "(1)"},
                {"kumiBan": "1=3", "haraiGaku": "320", "ninki": "(3)"},
            ],
            "ST2HaraiGakuDispItemSubData": [
                {"kumiBan": "2-1", "haraiGaku": "1,230", "ninki": "(4)"}
            ],
            "APartReturnDispFlg": False,
        },
        "resultCd": 0,
    }


class FakeClient:
    """Serves the synthetic responses above, keyed by (type, encp)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.responses = {
            ("JSJ057", ""): jsj057(),
            ("JSJ001", MEETING_ENC): jsj001(),
            ("JSJ017", MEETING_ENC): jsj017(),
            ("JSJ018", MEETING_ENC): jsj018(),
            ("JSJ006", race_enc(1)): jsj006(),
            ("JSJ012", race_enc(1)): jsj012(),
        }

    def get(self, type_: str, **params: str) -> dict[str, Any]:
        self.calls.append((type_, params))
        return self.responses[(type_, params.get("encp", ""))]


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()
