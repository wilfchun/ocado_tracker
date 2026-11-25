from datetime import date
from pathlib import Path

import pytest

from household_purchases.parsers.base import ParseError
from household_purchases.parsers.ocado import OcadoParser, money

RAW = (Path(__file__).parent / "fixtures/upcoming.eml").read_bytes()


def test_verified_totals():
    order = OcadoParser().parse(RAW)
    assert len(order.items) == 30
    assert sum(i.quantity for i in order.items) == 34
    assert order.product_total_pence == 9231
    assert order.total_pence == 9291
    assert order.charges.offer_savings_pence == 2695
    assert order.items[1].quantity == 3
    assert order.items[1].line_total_pence == 420
    assert order.items[2].name == "M&S Fusilli"
    assert order.delivery_date == date(2026, 11, 18)


@pytest.mark.parametrize(
    "before,after",
    [
        (b"productTable", b"unknown"),
        (b"order reference: SYNTHETIC-001", b"unknown"),
        (b"<td>3</td>", b"<td>0</td>"),
        (b"92.91", b"92.92"),
        (b"<td>4.20</td>", b"<td>4.20</td><td>extra</td>"),
        (b"Carrier bag charge", b"Voucher"),
        (b"<td>4.20</td>", b"<td>4.201</td>"),
        (b"<td>3</td>", b"<td>1.5</td>"),
        (b"Offers savings", b"Unrecognised savings"),
    ],
)
def test_invalid(before, after):
    with pytest.raises(ParseError):
        OcadoParser().parse(RAW.replace(before, after))


def test_timezone():
    raw = RAW.replace(b"18 Nov 2026 10:00:00 +0000", b"30 Jun 2026 23:30:00 +0000")
    assert OcadoParser().parse(raw).delivery_date == date(2026, 7, 1)


def test_quoted_printable_and_multipart():
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(RAW)
    html = message.get_content()
    from email.message import EmailMessage

    encoded = EmailMessage()
    encoded["Date"] = message["Date"]
    encoded.set_content("Text alternative")
    encoded.add_alternative(html, subtype="html", cte="quoted-printable")
    assert OcadoParser().parse(encoded.as_bytes()).product_total_pence == 9231


@pytest.mark.parametrize("value", ["-1.00", "1.001", "1e2", "NaN", "1", "1,00.00"])
def test_bad_money(value):
    with pytest.raises(ParseError):
        money(value)


def test_unsupported_structure():
    with pytest.raises(ParseError, match="unsupported_adjustment_structure"):
        OcadoParser().parse(RAW.replace(b"</body>", b"<h2>Substitutions</h2></body>"))


def test_whitespace_and_explicit_date():
    raw = RAW.replace(b"M&amp;S Fusilli", b" M&amp;S \n  Fusilli ").replace(
        b"Your delivery is today", b"Your delivery date: 2026-11-19"
    )
    order = OcadoParser().parse(raw)
    assert order.delivery_date == date(2026, 11, 19)
    assert order.items[2].name == "M&S Fusilli"


REAL_TEMPLATE = (Path(__file__).parent / "fixtures/ocado_upcoming_sanitised.eml").read_bytes()


def test_sanitised_retailer_email():
    """Regression for the supplied real DOM, quoted-printable MIME and policy copy."""
    order = OcadoParser().parse(REAL_TEMPLATE)
    assert order.order_reference == "0000000000000"
    assert order.delivery_date == date(2026, 11, 18)
    assert order.status == "provisional"
    assert len(order.items) == 30
    assert sum(item.quantity for item in order.items) == 34
    assert order.charges.gross_subtotal_pence == 11926
    assert order.charges.offer_savings_pence == 2695
    assert order.product_total_pence == 9231
    assert order.charges.delivery_charge_pence == 0
    assert order.charges.bag_charge_pence == 60
    assert order.charges.small_order_charge_pence == 0
    assert order.charges.other_adjustments_pence is None
    assert order.total_pence == 9291
    kallo = next(
        item
        for item in order.items
        if item.name == "Kallo Organic Very Low Salt Chicken Stock Cubes"
    )
    assert kallo.quantity == 3
    assert kallo.line_total_pence == 420  # Already the extended discounted cost.
    assert next(item for item in order.items if item.name == "M&S Fusilli").line_total_pence == 70


@pytest.mark.parametrize(
    "before,after",
    [
        ("-£26.95", "-£26.94"),
        ("-£26.95", "£26.95"),
        ("Total: £92.91", "Total: £92.92"),
        ("Total: £92.91", "Total: £92.91 Voucher: -£1.00"),
        ("Carrier bag charge", "Unrecognised charge"),
        ("receipt for order 0000000000000", "receipt for your order"),
        (
            "Your order doesn’t have any substitutions, everything’s present and correct.",
            "Your order has substitutions.",
        ),
    ],
)
def test_real_template_rejects_changed_or_unsupported_facts(before, after):
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(REAL_TEMPLATE)
    html = message.get_content()
    assert before in html
    message.set_content(
        html.replace(before, after), subtype="html", charset="utf-8", cte="quoted-printable"
    )
    with pytest.raises(ParseError):
        OcadoParser().parse(message.as_bytes())
