from pathlib import Path

import httpx
import pytest

from household_purchases.config import Settings
from household_purchases.email.fastmail import (
    CORE,
    MAIL,
    FastmailClient,
    JmapError,
    matches_headers,
    safe_url,
)

RAW = (Path(__file__).parent / "fixtures/upcoming.eml").read_bytes()
SESSION = {
    "capabilities": {CORE: {"maxObjectsInGet": 1}, MAIL: {}},
    "primaryAccounts": {MAIL: "a"},
    "accounts": {"a": {"accountCapabilities": {MAIL: {}}}},
    "apiUrl": "https://api.fastmail.com/jmap/api/",
    "downloadUrl": "https://www.fastmailusercontent.com/jmap/download/{accountId}/{blobId}/{name}?type={type}",
}


def test_pagination_get_limits_and_download():
    calls = []

    def handle(request):
        import json

        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json=SESSION)
        if request.url.host == "www.fastmailusercontent.com":
            return httpx.Response(200, content=RAW)
        method, args, tag = json.loads(request.content)["methodCalls"][0]
        assert method in ("Email/query", "Email/get")
        if method == "Email/query":
            result = {
                "ids": ["e1", "e2"] if args["position"] == 0 else [],
                "position": args["position"],
                "queryState": "q",
            }
            assert "inMailbox" not in args["filter"]
        else:
            assert len(args["ids"]) == 1
            result = {
                "list": [
                    {"id": args["ids"][0], "blobId": "b", "receivedAt": "2026-11-18T10:00:00Z"}
                ],
                "notFound": [],
            }
        return httpx.Response(200, json={"methodResponses": [[method, result, tag]]})

    client = FastmailClient("test-token", transport=httpx.MockTransport(handle))
    try:
        assert len(list(client.discover(Settings()))) == 2
        assert client.download("blob/with slash") == RAW
        assert "%2F" in str(calls[-1].url)
    finally:
        client.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://api.fastmail.com/a",
        "https://evil.invalid/a",
        "https://api.fastmail.com.evil.invalid/a",
        "https://secret@api.fastmail.com/a",
        "https://api.fastmail.com:444/a",
    ],
)
def test_url_restriction(url):
    with pytest.raises(JmapError):
        safe_url(url)


def test_retry_and_authentication():
    attempts, sleeps = [], []

    def handle(request):
        attempts.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "1"})
            if len(attempts) == 1
            else httpx.Response(200, json=SESSION)
        )

    client = FastmailClient(
        "test-token", transport=httpx.MockTransport(handle), sleep=sleeps.append
    )
    assert sleeps == [1]
    client.close()
    with pytest.raises(JmapError, match="authentication_failed"):
        FastmailClient("test-token", transport=httpx.MockTransport(lambda _: httpx.Response(401)))


def test_exact_local_filters():
    assert matches_headers(RAW, Settings())
    assert not matches_headers(
        RAW.replace(b"Your upcoming Ocado delivery", b"Your upcoming Ocado delivery extra"),
        Settings(),
    )
    assert not matches_headers(RAW, Settings(recipient="someone@example.invalid"))


def test_redirect_never_followed():
    with pytest.raises(JmapError, match="http_failed"):
        FastmailClient(
            "test-token",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(302, headers={"Location": "https://evil.invalid"})
            ),
        )


def test_method_allowlist_and_bounded_retries():
    client = FastmailClient(
        "test-token", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=SESSION))
    )
    with pytest.raises(JmapError, match="read_only"):
        client.call("Email/set", {})
    client.close()
    sleeps = []
    with pytest.raises(JmapError, match="retries_exhausted"):
        FastmailClient(
            "test-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(503)),
            sleep=sleeps.append,
        )
    assert len(sleeps) == 3


def test_duplicate_results_and_query_state_change():
    import json

    def transport(changing):
        def handle(request):
            if request.url.path.endswith("/session"):
                return httpx.Response(200, json=SESSION)
            method, args, tag = json.loads(request.content)["methodCalls"][0]
            if method == "Email/query":
                position = args["position"]
                result = {
                    "position": position,
                    "queryState": "new" if changing and position else "q",
                    "ids": ["e1", "e1"] if position == 0 else [],
                }
            else:
                result = {
                    "list": [{"id": "e1", "blobId": "b", "receivedAt": "2026-11-18T10:00:00Z"}]
                }
            return httpx.Response(200, json={"methodResponses": [[method, result, tag]]})

        return httpx.MockTransport(handle)

    client = FastmailClient("test", transport=transport(False))
    assert len(list(client.discover(Settings()))) == 1
    client.close()
    client = FastmailClient("test", transport=transport(True))
    with pytest.raises(JmapError, match="query_changed"):
        list(client.discover(Settings()))
    client.close()
