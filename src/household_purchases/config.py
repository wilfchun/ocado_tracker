import os
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


def secret_file(key: str) -> str | None:
    value = os.environ.get(key)
    return Path(value).read_text().strip() if value else None


class Settings(BaseModel):
    raw_email_dir: Path = Field(
        default_factory=lambda: Path(os.environ.get("RAW_EMAIL_DIR", "raw-emails"))
    )
    timezone: str = Field(default_factory=lambda: os.environ.get("TIMEZONE", "Europe/London"))
    source_account: str = Field(
        default_factory=lambda: os.environ.get("SOURCE_ACCOUNT", "fastmail")
    )
    sender: str = Field(
        default_factory=lambda: os.environ.get("OCADO_SENDER", "noreply@email.ocado.com")
    )
    subject: str = Field(
        default_factory=lambda: os.environ.get("OCADO_SUBJECT", "Your upcoming Ocado delivery")
    )
    recipient: str | None = Field(default_factory=lambda: os.environ.get("OCADO_RECIPIENT"))
    mailbox_id: str | None = Field(default_factory=lambda: os.environ.get("OCADO_MAILBOX_ID"))
    fastmail_account_id: str | None = Field(
        default_factory=lambda: os.environ.get("FASTMAIL_ACCOUNT_ID")
    )

    def database_url(self) -> str:
        value = secret_file("DATABASE_URL_FILE") or os.environ.get("DATABASE_URL")
        if not value:
            raise ValueError("database_url_required")
        return value

    def model_post_init(self, __context: object) -> None:
        ZoneInfo(self.timezone)
