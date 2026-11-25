"""Strict adapter for the documented upcoming-delivery table.

Only explicit, tested label aliases are supported. Real historical templates must
be reviewed before extending this adapter; unknown summary rows fail closed.
"""

import re
from datetime import datetime
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pydantic import ValidationError

from household_purchases.parsers.base import ParseError
from household_purchases.schemas import OrderCharges, ParsedOrder, ParsedPurchaseItem

LABELS = {
    "subtotal": "gross_subtotal_pence",
    "gross product subtotal": "gross_subtotal_pence",
    "offers savings": "offer_savings_pence",
    "delivery charge": "delivery_charge_pence",
    "carrier bag charge": "bag_charge_pence",
    "small order charge": "small_order_charge_pence",
    "order total": "total_pence",
}


def money(text: str) -> int:
    text = text.strip().removeprefix("£").strip()
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}", text):
        raise ParseError("invalid_money")
    return int(Decimal(text.replace(",", "")) * 100)


class OcadoParser:
    version = "ocado-upcoming-v1"

    def __init__(self, timezone: str = "Europe/London"):
        self.timezone = ZoneInfo(timezone)

    def parse(self, raw: bytes, *, received_at: datetime | None = None) -> ParsedOrder:
        try:
            return self._parse(raw, received_at)
        except ParseError:
            raise
        except (ValueError, TypeError, KeyError, OverflowError, ValidationError):
            raise ParseError("invalid_or_unreconciled_order") from None

    def _parse(self, raw: bytes, received_at: datetime | None) -> ParsedOrder:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        body = message.get_body(preferencelist=("html",))
        if body is None:
            raise ParseError("missing_html")
        soup = BeautifulSoup(body.get_content(), "html.parser")
        tables = soup.select("#productTable")
        if len(tables) != 1:
            raise ParseError("missing_or_duplicate_product_table")
        rows = tables[0].find_all("tr")
        if not rows or [c.get_text(" ", strip=True) for c in rows[0].find_all("th")] != [
            "Product",
            "Quantity",
            "Price to pay (£)",
        ]:
            raise ParseError("unexpected_product_headers")
        items = []
        for row in rows[1:]:
            cells = row.find_all("td", recursive=False)
            if len(cells) != 3 or row.find("th") or any(c.find("table") for c in cells):
                raise ParseError("malformed_product_row")
            name, quantity, price = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            if not re.fullmatch(r"[1-9]\d*", quantity) or not name:
                raise ParseError("invalid_product")
            items.append(
                ParsedPurchaseItem(name=name, quantity=int(quantity), line_total_pence=money(price))
            )
        prose = soup.get_text(" ", strip=True)
        refs = re.findall(
            r"\border (?:reference|number)\s*[:#]?\s*([A-Za-z0-9-]+)", prose, re.IGNORECASE
        )
        if len(set(refs)) != 1:
            raise ParseError("missing_or_ambiguous_order_reference")
        # Do not infer arbitrary date prose. Only these two documented forms.
        explicit = re.findall(
            r"\bdelivery (?:date|on)\s*:?\s*(\d{4}-\d{2}-\d{2})\b", prose, re.IGNORECASE
        )
        if explicit:
            from datetime import date

            if len(set(explicit)) != 1:
                raise ParseError("ambiguous_delivery_date")
            delivery_date = date.fromisoformat(explicit[0])
        elif re.search(r"\bdelivery\s+(?:is\s+)?today\b", prose, re.IGNORECASE):
            timestamp = received_at or parsedate_to_datetime(str(message.get("Date", "")))
            if timestamp.tzinfo is None:
                raise ParseError("naive_message_timestamp")
            delivery_date = timestamp.astimezone(self.timezone).date()
        else:
            raise ParseError("unknown_delivery_date")
        summary = {}
        for row in soup.find_all("tr"):
            if row in rows:
                continue
            cells = row.find_all(["td", "th"], recursive=False)
            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            if not texts:
                continue
            label = texts[0].casefold().rstrip(":")
            key = LABELS.get(label)
            if key:
                if len(texts) != 2 or key in summary:
                    raise ParseError("malformed_or_duplicate_summary")
                summary[key] = money(texts[1])
            elif not row.find("table") and any(re.search(r"£|\d+\.\d{2}", t) for t in texts):
                raise ParseError("unknown_monetary_summary")
        if set(summary) != set(LABELS.values()):
            raise ParseError("missing_summary_fields")
        total = summary.pop("total_pence")
        return ParsedOrder(
            order_reference=refs[0],
            delivery_date=delivery_date,
            items=tuple(items),
            charges=OrderCharges(**summary),
            total_pence=total,
        )
