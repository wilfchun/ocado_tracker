"""Opt-in smoke test against a dedicated provisioned, disposable Metabase instance."""

import os
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from household_purchases.db import engine_for
from household_purchases.ingestion import Ingestor
from household_purchases.metabase import COLLECTION, QUESTIONS, Metabase, bootstrap
from household_purchases.provisioning import ProvisionSettings, provision

pytestmark = [
    pytest.mark.metabase,
    pytest.mark.skipif(
        not os.environ.get("LIVE_STACK_STATE_DIR"),
        reason="dedicated live Metabase test instance required",
    ),
]


def test_real_metabase_bootstrap_queries_and_repeat_setup(tmp_path):
    state = Path(os.environ["LIVE_STACK_STATE_DIR"])
    database = os.environ["LIVE_STACK_DATABASE"]
    pg_port = int(os.environ["LIVE_STACK_PGPORT"])
    mb_port = int(os.environ["LIVE_STACK_PORT"])
    provision(
        ProvisionSettings(
            state_dir=state,
            database=database,
            application_database=os.environ["LIVE_STACK_APP_DATABASE"],
            socket_dir="127.0.0.1",
            port=pg_port,
        )
    )
    # These are fresh connections through SCRAM-authenticated loopback HBA rules
    # in the dedicated smoke cluster, rather than existing Metabase pool sessions.
    reader_password = (state / "reader-password").read_text().strip()
    with psycopg.connect(
        dbname=database,
        user="purchases_reader",
        password=reader_password,
        host="127.0.0.1",
        port=pg_port,
    ) as reader:
        assert reader.execute("SELECT current_user").fetchone()[0] == "purchases_reader"
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            dbname=database,
            user="purchases_reader",
            password="deliberately-wrong",
            host="127.0.0.1",
            port=pg_port,
        )
    writer = engine_for(f"postgresql+psycopg://purchases_importer@127.0.0.1:{pg_port}/{database}")
    try:
        raw = (Path(__file__).parent / "fixtures/ocado_upcoming_sanitised.eml").read_bytes()
        importer = Ingestor(writer, tmp_path / "archive")
        assert importer.ingest(raw)[1] in ("imported", "duplicate")
        assert importer.ingest(raw)[1] == "duplicate"
    finally:
        writer.dispose()
    kwargs = dict(
        state_dir=state,
        database=database,
        postgres_port=pg_port,
        metabase_port=mb_port,
        email="admin@example.invalid",
    )
    dashboard = bootstrap(**kwargs)
    assert bootstrap(**kwargs) == dashboard
    client = Metabase(f"http://127.0.0.1:{mb_port}")
    try:
        client.login(
            "admin@example.invalid", (state / "metabase-admin-password").read_text().strip()
        )
        assert client.http.get("/").status_code == 200
        collection = client.unique(client.request("GET", "collection"), COLLECTION)
        items = client.request("GET", f"collection/{collection['id']}/items")["data"]
        cards = [item for item in items if item["model"] == "card"]
        assert len(cards) == len(QUESTIONS)
        details = client.request("GET", f"dashboard/{dashboard}")
        assert len(details["dashcards"]) == len(QUESTIONS)
        for card in cards:
            result = client.request("POST", f"card/{card['id']}/query", json={"parameters": []})
            assert result["status"] == "completed"
            if card["name"] == "Monthly expenditure":
                assert Decimal(str(result["data"]["rows"][0][-1])) == Decimal("92.91")
    finally:
        client.logout()
        client.close()
