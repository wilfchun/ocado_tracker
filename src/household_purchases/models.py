from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    description: Mapped[str | None] = mapped_column(Text)


class SourceMessage(Base):
    __tablename__ = "source_messages"
    __table_args__ = (
        UniqueConstraint("source_account", "remote_email_id"),
        UniqueConstraint("source_account", "content_sha256"),
        CheckConstraint("status IN ('pending','imported','failed','duplicate','superseded')"),
        CheckConstraint("attempt_count >= 0"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_account: Mapped[str] = mapped_column(Text)
    remote_email_id: Mapped[str | None] = mapped_column(Text)
    internet_message_id: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_storage_path: Mapped[str] = mapped_column(Text)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("retailer", "normalised_name"),
        CheckConstraint(
            "classification_method IS NULL OR classification_method IN ('manual','rule','llm')"
        ),
        CheckConstraint("first_seen <= last_seen"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    retailer: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    normalised_name: Mapped[str] = mapped_column(Text)
    retailer_sku: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    pack_size: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    classification_method: Mapped[str | None] = mapped_column(Text)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[date]
    last_seen: Mapped[date]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("retailer", "order_reference"),
        CheckConstraint("status IN ('provisional','final')"),
        CheckConstraint("currency = 'GBP'"),
        CheckConstraint("product_total_pence >= 0 AND total_pence >= 0"),
        CheckConstraint("gross_subtotal_pence IS NULL OR gross_subtotal_pence >= 0"),
        CheckConstraint("offer_savings_pence IS NULL OR offer_savings_pence >= 0"),
        CheckConstraint("delivery_charge_pence IS NULL OR delivery_charge_pence >= 0"),
        CheckConstraint("bag_charge_pence IS NULL OR bag_charge_pence >= 0"),
        CheckConstraint("small_order_charge_pence IS NULL OR small_order_charge_pence >= 0"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    retailer: Mapped[str] = mapped_column(Text)
    order_reference: Mapped[str] = mapped_column(Text)
    delivery_date: Mapped[date] = mapped_column(index=True)
    currency: Mapped[str] = mapped_column(Text, default="GBP")
    gross_subtotal_pence: Mapped[int | None] = mapped_column(BigInteger)
    offer_savings_pence: Mapped[int | None] = mapped_column(BigInteger)
    product_total_pence: Mapped[int] = mapped_column(BigInteger)
    delivery_charge_pence: Mapped[int | None] = mapped_column(BigInteger)
    bag_charge_pence: Mapped[int | None] = mapped_column(BigInteger)
    small_order_charge_pence: Mapped[int | None] = mapped_column(BigInteger)
    other_adjustments_pence: Mapped[int | None] = mapped_column(BigInteger)
    total_pence: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("source_messages.id"))
    parsed_sha256: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        UniqueConstraint("order_id", "line_number"),
        CheckConstraint("line_number > 0 AND quantity > 0 AND line_total_pence >= 0"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    source_product_name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    line_total_pence: Mapped[int] = mapped_column(BigInteger)


class ScanState(Base):
    __tablename__ = "scan_state"
    source_account: Mapped[str] = mapped_column(Text, primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
