from datetime import date
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator


def normalise_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ParsedPurchaseItem(Contract):
    name: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    line_total_pence: int = Field(ge=0)


class OrderCharges(Contract):
    gross_subtotal_pence: int = Field(ge=0)
    offer_savings_pence: int = Field(ge=0)
    delivery_charge_pence: int = Field(ge=0)
    bag_charge_pence: int = Field(ge=0)
    small_order_charge_pence: int = Field(ge=0)
    other_adjustments_pence: int | None = None


class ParsedOrder(Contract):
    retailer: Literal["ocado"] = "ocado"
    order_reference: str = Field(min_length=1)
    delivery_date: date
    currency: Literal["GBP"] = "GBP"
    status: Literal["provisional"] = "provisional"
    items: tuple[ParsedPurchaseItem, ...] = Field(min_length=1)
    charges: OrderCharges
    total_pence: int = Field(ge=0)

    @property
    def product_total_pence(self) -> int:
        return sum(item.line_total_pence for item in self.items)

    @model_validator(mode="after")
    def reconcile(self) -> "ParsedOrder":
        c = self.charges
        if c.gross_subtotal_pence - c.offer_savings_pence != self.product_total_pence:
            raise ValueError("subtotal_mismatch")
        if self.product_total_pence + c.delivery_charge_pence + c.bag_charge_pence + c.small_order_charge_pence + (c.other_adjustments_pence or 0) != self.total_pence:
            raise ValueError("total_mismatch")
        return self
