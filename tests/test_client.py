import httpx
import pytest

from keirin.client import USER_AGENT, KeirinApiError, KeirinClient


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler, fake_time: FakeTime, **kwargs) -> KeirinClient:
    return KeirinClient(
        transport=httpx.MockTransport(handler),
        sleep=fake_time.sleep,
        clock=fake_time.clock,
        **kwargs,
    )


def test_get_sends_type_and_params_with_user_agent():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"resultCd": 0})

    client = make_client(handler, FakeTime())
    assert client.get("JSJ057", kday="20260110") == {"resultCd": 0}
    assert seen[0].url.params["type"] == "JSJ057"
    assert seen[0].url.params["kday"] == "20260110"
    assert seen[0].headers["user-agent"] == USER_AGENT


def test_requests_are_spaced_by_min_interval():
    fake_time = FakeTime()
    client = make_client(lambda r: httpx.Response(200, json={}), fake_time, min_interval=1.5)
    client.get("A")
    client.get("B")
    client.get("C")
    assert fake_time.sleeps == [1.5, 1.5]
    assert client.request_count == 3


def test_retries_server_errors_then_succeeds():
    responses = iter([httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    fake_time = FakeTime()
    client = make_client(lambda r: next(responses), fake_time, min_interval=0, backoff=5)
    assert client.get("A") == {"ok": 1}
    assert fake_time.sleeps == [5]


def test_gives_up_after_max_retries():
    client = make_client(lambda r: httpx.Response(500), FakeTime(), min_interval=0, max_retries=3)
    with pytest.raises(KeirinApiError, match="gave up after 3"):
        client.get("A")
    assert client.request_count == 3


def test_does_not_retry_forbidden():
    client = make_client(lambda r: httpx.Response(403), FakeTime(), min_interval=0)
    with pytest.raises(KeirinApiError, match="HTTP 403"):
        client.get("A")
    assert client.request_count == 1


def test_non_json_is_an_error():
    client = make_client(
        lambda r: httpx.Response(200, text="<html>"), FakeTime(), min_interval=0, max_retries=1
    )
    with pytest.raises(KeirinApiError, match="not JSON"):
        client.get("A")
