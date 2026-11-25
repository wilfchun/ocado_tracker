"""Idempotent bootstrap for the pinned Metabase OSS API.

Creates a local admin, a reader connection and starter content. Existing cards
and dashboard layouts are preserved; missing managed objects are recreated.
"""

import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from household_purchases.provisioning import persistent_secret

log = logging.getLogger(__name__)
COLLECTION = "Household purchases (managed)"
MARKER = "managed-by:household-purchases:v1"
DATABASE = "Household purchases"
DASHBOARD = "Ocado delivery-summary expenditure"
QUESTIONS = (
    (
        "Monthly expenditure",
        "bar",
        "SELECT date_trunc('month', delivery_date::timestamp)::date AS month, status, sum(order_total_gbp) AS spend_gbp FROM analytics.order_totals GROUP BY 1,2 ORDER BY 1",
    ),
    (
        "Category expenditure",
        "bar",
        "SELECT category, sum(line_spend_gbp) AS spend_gbp FROM analytics.purchase_lines GROUP BY 1 ORDER BY 2 DESC",
    ),
    (
        "Highest product expenditure",
        "table",
        "SELECT product_name, sum(quantity) AS purchased_units, count(DISTINCT order_id) AS orders, sum(line_spend_gbp) AS spend_gbp FROM analytics.purchase_lines GROUP BY 1 ORDER BY 4 DESC LIMIT 20",
    ),
    (
        "Offers and charges",
        "table",
        "SELECT delivery_date, status, gross_subtotal_gbp, savings_gbp, product_spend_gbp, delivery_charge_gbp, bag_charge_gbp, small_order_charge_gbp, order_total_gbp FROM analytics.order_totals ORDER BY delivery_date DESC",
    ),
    (
        "Product price history",
        "table",
        "SELECT product_name, delivery_date, status, quantity, effective_unit_price_gbp FROM analytics.product_price_history ORDER BY delivery_date DESC, product_name LIMIT 200",
    ),
    ("Import health", "table", "SELECT * FROM analytics.import_health"),
)


class BootstrapError(ValueError):
    pass


class Metabase:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None):
        parts = urlsplit(base_url)
        if (
            parts.scheme != "http"
            or parts.hostname != "127.0.0.1"
            or parts.username
            or parts.password
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise BootstrapError("local_metabase_url_required")
        self.http = httpx.Client(
            base_url=base_url,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.http.close()

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.http.request(method, "/api/" + path, **kwargs)
            if response.status_code >= 300:
                raise BootstrapError("metabase_request_failed")
            return response.json() if response.content else None
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, BootstrapError):
                raise
            raise BootstrapError("metabase_unavailable_or_invalid_response") from None

    def wait_ready(self, seconds: int = 300):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "health").get("status") == "ok":
                    return
            except BootstrapError:
                pass
            time.sleep(2)
        raise BootstrapError("metabase_startup_timeout")

    def login(self, email: str, password: str):
        properties = self.request("GET", "session/properties")
        token = properties.get("setup-token")
        # The pinned 0.56 release retains a setup token after setup.
        if token and not properties.get("has-user-setup", False):
            response = self.request(
                "POST",
                "setup",
                json={
                    "token": token,
                    "user": {
                        "first_name": "Household",
                        "last_name": "Admin",
                        "email": email,
                        "password": password,
                    },
                    "prefs": {"site_name": DASHBOARD, "site_locale": "en", "allow_tracking": False},
                },
            )
        else:
            response = self.request(
                "POST", "session", json={"username": email, "password": password}
            )
        self.http.headers["X-Metabase-Session"] = response["id"]

    def logout(self):
        session = self.http.headers.get("X-Metabase-Session")
        if session:
            try:
                self.request("DELETE", "session", json={"session_id": session})
            except BootstrapError:
                log.warning("Could not revoke bootstrap session; it will expire")
            self.http.headers.pop("X-Metabase-Session", None)

    @staticmethod
    def unique(items: list[dict], name: str) -> dict | None:
        matches = [item for item in items if item.get("name") == name and not item.get("archived")]
        if len(matches) > 1:
            raise BootstrapError("duplicate_managed_metabase_objects")
        return matches[0] if matches else None

    def content(self, *, database: str, port: int, reader_password: str) -> int:
        details = {
            "host": "127.0.0.1",
            "port": port,
            "dbname": database,
            "user": "purchases_reader",
            "password": reader_password,
            "ssl": False,
            "schema-filters-type": "inclusion",
            "schema-filters-patterns": "analytics",
        }
        databases = self.request("GET", "database")["data"]
        source = self.unique(databases, DATABASE)
        if source:
            if source["engine"] != "postgres" or any(
                str(source["details"].get(key)) != str(details[key])
                for key in ("host", "port", "dbname", "user")
            ):
                raise BootstrapError("existing_metabase_connection_mismatch")
            # Keep existing settings while refreshing the persisted reader secret.
            self.request(
                "PUT",
                f"database/{source['id']}",
                json={
                    "name": DATABASE,
                    "engine": "postgres",
                    "details": {**source["details"], **details},
                },
            )
        else:
            source = self.request(
                "POST",
                "database",
                json={
                    "name": DATABASE,
                    "engine": "postgres",
                    "details": details,
                    "is_full_sync": True,
                },
            )
        collection = self.unique(self.request("GET", "collection"), COLLECTION)
        if collection and collection.get("description") != MARKER:
            raise BootstrapError("existing_metabase_collection_mismatch")
        if collection is None:
            collection = self.request(
                "POST",
                "collection",
                json={"name": COLLECTION, "description": MARKER, "color": "#509EE3"},
            )
        collection_id = collection["id"]
        items = self.request("GET", f"collection/{collection_id}/items")["data"]
        dashboard = self.unique([i for i in items if i["model"] == "dashboard"], DASHBOARD)
        if dashboard is None:
            dashboard = self.request(
                "POST",
                "dashboard",
                json={
                    "name": DASHBOARD,
                    "description": "Provisional Ocado delivery summaries. Effective unit prices include promotions; pack sizes are unknown.",
                    "collection_id": collection_id,
                },
            )
        dashboard = self.request("GET", f"dashboard/{dashboard['id']}")
        cards = [item for item in items if item["model"] == "card"]
        additions = []
        placed = {item.get("card_id") for item in dashboard["dashcards"]}
        row = max((item["row"] + item["size_y"] for item in dashboard["dashcards"]), default=0)
        for name, display, query in QUESTIONS:
            card = self.unique(cards, name)
            if card is None:
                card = self.request(
                    "POST",
                    "card",
                    json={
                        "name": name,
                        "collection_id": collection_id,
                        "display": display,
                        "description": MARKER,
                        "visualization_settings": {},
                        "dataset_query": {
                            "database": source["id"],
                            "type": "native",
                            "native": {"query": query},
                        },
                    },
                )
            if card["id"] not in placed:
                index = len(additions)
                additions.append(
                    {
                        "id": -(index + 1),
                        "card_id": card["id"],
                        "row": row + (index // 2) * 6,
                        "col": (index % 2) * 12,
                        "size_x": 12,
                        "size_y": 6,
                        "series": [],
                        "parameter_mappings": [],
                        "visualization_settings": {},
                    }
                )
        if additions:
            self.request(
                "PUT",
                f"dashboard/{dashboard['id']}",
                json={"dashcards": dashboard["dashcards"] + additions},
            )
        return dashboard["id"]


def bootstrap(
    *,
    state_dir: Path,
    database: str,
    postgres_port: int,
    metabase_port: int,
    email: str,
    transport: httpx.BaseTransport | None = None,
) -> int:
    client = Metabase(f"http://127.0.0.1:{metabase_port}", transport=transport)
    try:
        client.wait_ready()
        client.login(email, persistent_secret(state_dir / "metabase-admin-password"))
        dashboard_id = client.content(
            database=database,
            port=postgres_port,
            reader_password=persistent_secret(state_dir / "reader-password"),
        )
        log.info("Metabase ready: /dashboard/%d", dashboard_id)
        return dashboard_id
    finally:
        client.logout()
        client.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        bootstrap(
            state_dir=Path(os.environ["HP_PROVISION_STATE_DIR"]),
            database=os.environ.get("HP_DATABASE_NAME", "household_purchases"),
            postgres_port=int(os.environ.get("HP_POSTGRES_PORT", "5432")),
            metabase_port=int(os.environ.get("HP_METABASE_PORT", "3000")),
            email=os.environ.get("HP_METABASE_ADMIN_EMAIL", "admin@example.invalid"),
        )
        return 0
    except Exception:
        log.error(
            "Metabase bootstrap failed; verify local service health and persisted admin credentials"
        )
        return 1
