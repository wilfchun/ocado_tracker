import json

import httpx
import pytest

from household_purchases.metabase import QUESTIONS, BootstrapError, Metabase, bootstrap
from household_purchases.provisioning import persistent_secret


class FakeMetabase:
    def __init__(self):
        self.configured = False
        self.databases = []
        self.collections = []
        self.cards = []
        self.dashboards = []
        self.calls = []

    def handle(self, request):
        path = request.url.path.removeprefix("/api/")
        method = request.method
        body = json.loads(request.content) if request.content else None
        self.calls.append((method, path))
        response = None
        if (method, path) == ("GET", "health"):
            response = {"status": "ok"}
        elif (method, path) == ("GET", "session/properties"):
            response = {"setup-token": "setup-secret", "has-user-setup": self.configured}
        elif (method, path) == ("POST", "setup"):
            assert not self.configured
            self.configured = True
            response = {"id": "session-secret"}
        elif (method, path) == ("POST", "session"):
            assert self.configured
            response = {"id": "session-secret"}
        elif (method, path) == ("DELETE", "session"):
            assert body["session_id"] == "session-secret"
        else:
            assert request.headers["X-Metabase-Session"] == "session-secret"
            if (method, path) == ("GET", "database"):
                response = {"data": self.databases}
            elif (method, path) == ("POST", "database"):
                response = {"id": len(self.databases) + 1, **body}
                self.databases.append(response)
            elif method == "PUT" and path.startswith("database/"):
                self.databases[0].update(body)
                response = self.databases[0]
            elif (method, path) == ("GET", "collection"):
                response = self.collections
            elif (method, path) == ("POST", "collection"):
                response = {"id": len(self.collections) + 1, **body}
                self.collections.append(response)
            elif (method, path) == ("GET", "collection/1/items"):
                response = {
                    "data": self.cards
                    + [
                        {"id": d["id"], "model": "dashboard", "name": d["name"]}
                        for d in self.dashboards
                    ]
                }
            elif (method, path) == ("POST", "dashboard"):
                response = {"id": len(self.dashboards) + 1, "dashcards": [], **body}
                self.dashboards.append(response)
            elif (method, path) == ("GET", "dashboard/1"):
                response = self.dashboards[0]
            elif (method, path) == ("PUT", "dashboard/1"):
                for card in body["dashcards"]:
                    if card["id"] < 0:
                        card["id"] = -card["id"]
                self.dashboards[0].update(body)
                response = self.dashboards[0]
            elif (method, path) == ("POST", "card"):
                response = {"id": len(self.cards) + 1, "model": "card", **body}
                self.cards.append(response)
            else:
                raise AssertionError(f"Unexpected endpoint {method} {path}")
        return httpx.Response(200, json=response) if response is not None else httpx.Response(204)


def run(fake, tmp_path):
    persistent_secret(tmp_path / "reader-password")
    return bootstrap(
        state_dir=tmp_path,
        database="purchases",
        postgres_port=5432,
        metabase_port=3000,
        email="admin@example.invalid",
        transport=httpx.MockTransport(fake.handle),
    )


def test_bootstrap_creates_content_once_and_preserves_layout(tmp_path):
    fake = FakeMetabase()
    assert run(fake, tmp_path) == 1
    assert len(fake.databases) == len(fake.collections) == len(fake.dashboards) == 1
    assert len(fake.cards) == len(QUESTIONS)
    assert fake.databases[0]["details"]["user"] == "purchases_reader"
    assert fake.databases[0]["details"]["schema-filters-patterns"] == "analytics"
    fake.dashboards[0]["dashcards"][0]["row"] = 42
    assert run(fake, tmp_path) == 1
    assert len(fake.databases) == len(fake.collections) == len(fake.dashboards) == 1
    assert len(fake.cards) == len(QUESTIONS)
    assert fake.dashboards[0]["dashcards"][0]["row"] == 42
    assert fake.calls.count(("POST", "setup")) == 1
    assert fake.calls.count(("DELETE", "session")) == 2


def test_resume_after_interrupted_layout_creation(tmp_path):
    fake = FakeMetabase()
    run(fake, tmp_path)
    fake.dashboards[0]["dashcards"] = []
    run(fake, tmp_path)
    assert len(fake.cards) == len(QUESTIONS)
    assert len(fake.dashboards[0]["dashcards"]) == len(QUESTIONS)


def test_unrelated_database_is_not_overwritten(tmp_path):
    fake = FakeMetabase()
    run(fake, tmp_path)
    fake.databases[0]["details"]["dbname"] = "unrelated"
    with pytest.raises(BootstrapError, match="connection_mismatch"):
        run(fake, tmp_path)
    assert fake.databases[0]["details"]["dbname"] == "unrelated"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid",
        "http://localhost:3000",
        "http://127.0.0.1.evil.invalid",
        "http://password@127.0.0.1:3000",
    ],
)
def test_bootstrap_credentials_stay_on_loopback(url):
    with pytest.raises(BootstrapError, match="local_metabase_url_required"):
        Metabase(url)


def test_error_response_never_leaks_credentials():
    client = Metabase(
        "http://127.0.0.1:3000",
        transport=httpx.MockTransport(lambda _: httpx.Response(400, text="sensitive credential")),
    )
    try:
        with pytest.raises(BootstrapError, match="^metabase_request_failed$"):
            client.request("POST", "session", json={"password": "sensitive credential"})
    finally:
        client.close()
