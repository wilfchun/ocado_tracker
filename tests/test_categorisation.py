from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from household_purchases.categorisation import (
    LocalClassifier,
    classify,
    rule_category,
    set_category,
)
from household_purchases.ingestion import Ingestor
from household_purchases.models import Product

RAW = (Path(__file__).parent / "fixtures/upcoming.eml").read_bytes()


def test_rule_precedence_and_ambiguity():
    assert rule_category("Chicken soup") == "prepared-meals.soups"
    assert rule_category("Chicken stock cubes") == "store-cupboard.stocks-seasonings"
    assert rule_category("Chicken something") is None
    assert rule_category("Tinned tuna") == "meat-fish.fish"
    assert rule_category("Tinned beans") == "store-cupboard.canned-foods"


@pytest.mark.parametrize(
    "content",
    [
        '{"category_slug":"drinks.soft-drinks"}',
        '{"category_slug":"invented"}',
        "not json",
        '{"category_slug":"drinks.soft-drinks","extra":true}',
        "{}",
    ],
)
def test_validated_model_response(content):
    def handle(request):
        import json

        payload = json.loads(request.content)
        assert "SYNTHETIC" not in str(payload)
        assert payload["messages"][1]["content"] == '{"product_name": "Unfamiliar product"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    classifier = LocalClassifier(
        "http://127.0.0.1:8080/v1", "local", transport=httpx.MockTransport(handle)
    )
    try:
        assert classifier.category("Unfamiliar product", [{"slug": "drinks.soft-drinks"}]) == (
            "drinks.soft-drinks" if content == '{"category_slug":"drinks.soft-drinks"}' else None
        )
    finally:
        classifier.close()


def test_unavailable_endpoint():
    def fail(request):
        raise httpx.ConnectError("offline")

    classifier = LocalClassifier(
        "http://127.0.0.1:8080/v1", "local", transport=httpx.MockTransport(fail)
    )
    assert classifier.category("Something", [{"slug": "other"}]) is None
    classifier.close()


@pytest.mark.postgres
def test_manual_override_history_and_reuse(db, tmp_path):
    importer = Ingestor(db, tmp_path / "archive")
    source, _ = importer.ingest(RAW)
    assert classify(db)["classified"] == 5
    with Session(db) as s:
        product_id = s.scalar(select(Product.id).where(Product.name == "Coca-Cola Zero Sugar"))
    set_category(db, product_id, "other")
    importer.ingest(RAW.replace(b"SYNTHETIC-001", b"SYNTHETIC-002"))
    importer.retry(source, reprocess=True)
    assert classify(db)["classified"] == 0
    with Session(db) as s:
        assert (
            s.scalar(text("SELECT count(*) FROM analytics.purchase_lines WHERE category = 'Other'"))
            == 2
        )
        assert s.get(Product, product_id).classification_method == "manual"
    set_category(db, product_id, None)
    assert classify(db)["classified"] == 0
    with Session(db) as s:
        assert s.get(Product, product_id).category_id is None


@pytest.mark.postgres
def test_taxonomy_depth_constraint(db):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), db.begin() as c:
        c.execute(
            text(
                "UPDATE categories SET parent_id = (SELECT id FROM categories WHERE slug = 'drinks.soft-drinks') WHERE slug = 'drinks'"
            )
        )
