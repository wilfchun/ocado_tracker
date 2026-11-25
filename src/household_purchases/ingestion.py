import hashlib
import logging
import os
import tempfile
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from household_purchases.db import lock
from household_purchases.models import Order, Product, PurchaseItem, SourceMessage
from household_purchases.parsers.base import ParseError
from household_purchases.parsers.ocado import OcadoParser
from household_purchases.schemas import normalise_name

log = logging.getLogger(__name__)


def archive(raw: bytes, directory: Path) -> str:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError("archive_directory_must_be_private")
    name = hashlib.sha256(raw).hexdigest() + ".eml"
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".incoming-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return name


class Ingestor:
    def __init__(self, engine: Engine, raw_dir: Path, parser: OcadoParser | None = None):
        self.engine, self.raw_dir = engine, raw_dir
        self.parser = parser or OcadoParser()

    def ingest(
        self,
        raw: bytes,
        *,
        account: str = "local",
        remote_id: str | None = None,
        received_at: datetime | None = None,
        reprocess: bool = False,
        rejection: str | None = None,
    ) -> tuple[int, str]:
        path = archive(raw, self.raw_dir)
        digest = path.removesuffix(".eml")
        headers = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        with Session(self.engine) as session, session.begin():
            lock(session)
            criteria = [SourceMessage.content_sha256 == digest]
            if remote_id:
                criteria.append(SourceMessage.remote_email_id == remote_id)
            source = session.scalar(
                select(SourceMessage).where(SourceMessage.source_account == account, or_(*criteria))
            )
            if source is None:
                source = SourceMessage(
                    source_account=account,
                    remote_email_id=remote_id,
                    content_sha256=digest,
                    received_at=received_at,
                    internet_message_id=str(headers["Message-ID"])
                    if headers["Message-ID"]
                    else None,
                    subject=str(headers["Subject"]) if headers["Subject"] else None,
                    raw_storage_path=path,
                )
                session.add(source)
                session.flush()
            elif source.content_sha256 != digest:
                raise ValueError("remote_content_changed")
            source_id = source.id
            if source.status in ("imported", "duplicate", "superseded") and not reprocess:
                return source_id, "duplicate"
        try:
            if rejection:
                raise ParseError(rejection)
            parsed = self.parser.parse(raw, received_at=received_at)
            fingerprint = hashlib.sha256(parsed.model_dump_json().encode()).hexdigest()
            with Session(self.engine) as session, session.begin():
                lock(session)
                source = session.get(SourceMessage, source_id)
                authoritative = session.scalar(
                    select(Order).where(Order.source_message_id == source_id)
                )
                if authoritative and (authoritative.retailer, authoritative.order_reference) != (
                    parsed.retailer,
                    parsed.order_reference,
                ):
                    raise ParseError("source_identity_changed")
                order = session.scalar(
                    select(Order).where(
                        Order.retailer == parsed.retailer,
                        Order.order_reference == parsed.order_reference,
                    )
                )
                if order and order.parsed_sha256 == fingerprint and not reprocess:
                    self._mark(
                        source, "duplicate" if order.source_message_id != source_id else "imported"
                    )
                    return source_id, "duplicate"
                if order and (order.status == "final" or not reprocess):
                    raise ParseError("order_conflict")
                if order is None:
                    order = Order()
                    session.add(order)
                else:
                    if order.source_message_id != source_id:
                        previous = session.get(SourceMessage, order.source_message_id)
                        previous.status = "superseded"
                    session.execute(delete(PurchaseItem).where(PurchaseItem.order_id == order.id))
                for key, value in parsed.model_dump(exclude={"items", "charges"}).items():
                    setattr(order, key, value)
                for key, value in parsed.charges.model_dump().items():
                    setattr(order, key, value)
                order.product_total_pence = parsed.product_total_pence
                order.source_message_id, order.parsed_sha256 = source_id, fingerprint
                session.flush()
                for number, item in enumerate(parsed.items, 1):
                    normal = normalise_name(item.name)
                    product = session.scalar(
                        select(Product).where(
                            Product.retailer == parsed.retailer, Product.normalised_name == normal
                        )
                    )
                    if product is None:
                        product = Product(
                            retailer=parsed.retailer,
                            name=item.name,
                            normalised_name=normal,
                            first_seen=parsed.delivery_date,
                            last_seen=parsed.delivery_date,
                        )
                        session.add(product)
                        session.flush()
                    product.first_seen = min(product.first_seen, parsed.delivery_date)
                    product.last_seen = max(product.last_seen, parsed.delivery_date)
                    session.add(
                        PurchaseItem(
                            order_id=order.id,
                            product_id=product.id,
                            line_number=number,
                            source_product_name=item.name,
                            quantity=item.quantity,
                            line_total_pence=item.line_total_pence,
                        )
                    )
                self._mark(source, "imported")
            return source_id, "imported"
        except Exception as exc:
            code = str(exc) if isinstance(exc, ParseError) else "order_write_failed"
            with Session(self.engine) as session, session.begin():
                lock(session)
                source = session.get(SourceMessage, source_id)
                # A failed explicit reprocess must not mislabel an accepted authoritative source.
                if source.status != "imported":
                    source.status = "failed"
                source.attempt_count += 1
                source.processed_at = datetime.now(UTC)
                source.parser_version = self.parser.version
                source.error_code = source.error_detail = code
            log.warning(
                "source_id=%d status=failed parser=%s error=%s",
                source_id,
                self.parser.version,
                code,
            )
            return source_id, "failed"

    def _mark(self, source: SourceMessage, status: str) -> None:
        source.status, source.error_code, source.error_detail = status, None, None
        source.attempt_count += 1
        source.processed_at = datetime.now(UTC)
        source.parser_version = self.parser.version

    def retry(self, source_id: int, *, reprocess: bool = False) -> tuple[int, str]:
        with Session(self.engine) as session:
            source = session.get(SourceMessage, source_id)
            if source is None:
                raise ValueError("unknown_source")
            name = source.content_sha256 + ".eml"
            raw = (self.raw_dir / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source.content_sha256:
                raise ValueError("archive_hash_mismatch")
            return self.ingest(
                raw,
                account=source.source_account,
                remote_id=source.remote_email_id,
                received_at=source.received_at,
                reprocess=reprocess,
                rejection="source_filter_mismatch"
                if source.error_code == "source_filter_mismatch"
                else None,
            )
