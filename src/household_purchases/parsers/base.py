from datetime import datetime
from typing import Protocol
from household_purchases.schemas import ParsedOrder


class ParseError(ValueError):
    """A safe code, never interpolated email contents."""


class Parser(Protocol):
    version: str

    def parse(self, raw: bytes, *, received_at: datetime | None = None) -> ParsedOrder: ...
