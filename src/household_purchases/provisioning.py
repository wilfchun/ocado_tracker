"""NixOS local-stack provisioning, run as the PostgreSQL operating-system user.

All credentials are generated at runtime and retained across reboots. This helper
never drops databases, takes over a database with another owner, or prints secrets.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import tempfile
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import URL

from household_purchases.db import LOCK_ID
from household_purchases.migration import migrate

log = logging.getLogger(__name__)


class ProvisionError(ValueError):
    pass


@dataclass(frozen=True)
class ProvisionSettings:
    state_dir: Path
    database: str = "household_purchases"
    application_database: str = "household_purchases_metabase"
    socket_dir: str = "/run/postgresql"
    port: int = 5432
    metabase: bool = True

    def __post_init__(self):
        for name in (self.database, self.application_database):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) or name in {
                "postgres",
                "template0",
                "template1",
            }:
                raise ProvisionError("invalid_database_name")
        if self.database == self.application_database:
            raise ProvisionError("separate_application_database_required")

    def url(self, role: str, database: str | None = None) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=role,
            database=database or self.database,
            query={"host": self.socket_dir, "port": str(self.port)},
        ).render_as_string(hide_password=False)


def private_write(path: Path, contents: str) -> None:
    """Atomic 0600 replacement; called under the provisioning lock."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".provision-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def persistent_secret(path: Path) -> str:
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ProvisionError("secret_file_must_be_private")
        value = path.read_text().strip()
        if not value or "\n" in value or "\r" in value:
            raise ProvisionError("invalid_persisted_secret")
        return value
    value = "Aa1!" + secrets.token_urlsafe(36)
    private_write(path, value + "\n")
    return value


def scram_verifier(password: str) -> str:
    """Store only a SCRAM verifier in ALTER ROLE, rather than plaintext SQL."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
    stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")

    def encode(value: bytes) -> str:
        return b64encode(value).decode("ascii")

    return f"SCRAM-SHA-256$4096:{encode(salt)}${encode(stored)}:{encode(server)}"


def ensure_role(connection: psycopg.Connection, name: str) -> None:
    row = connection.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
        (name,),
    ).fetchone()
    if row is None:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name)))
    elif any(row[:5]) or not row[5]:
        raise ProvisionError("managed_role_has_elevated_privileges")
    if connection.execute(
        "SELECT 1 FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = %s)",
        (name,),
    ).fetchone():
        raise ProvisionError("managed_role_has_memberships")


def ensure_database(connection: psycopg.Connection, name: str, owner: str) -> None:
    row = connection.execute(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (name,)
    ).fetchone()
    if row is None:
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(name), sql.Identifier(owner)
            )
        )
    elif row[0] != owner:
        raise ProvisionError("database_owner_mismatch")
    connection.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
    )


def provision(settings: ProvisionSettings) -> None:
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if settings.state_dir.is_symlink() or settings.state_dir.stat().st_mode & 0o077:
        raise ProvisionError("provision_state_directory_must_be_private")
    with psycopg.connect(
        dbname="postgres",
        host=settings.socket_dir,
        port=settings.port,
        autocommit=True,
        client_encoding="UTF8",
    ) as admin:
        # Separate from the importer lock: migrations acquire that lock themselves
        # through a different owner connection.
        admin.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID + 1,))
        admin.execute("SET log_statement = 'none'")
        for role in ("purchases_owner", "purchases_importer", "purchases_reader"):
            ensure_role(admin, role)
        ensure_database(admin, settings.database, "purchases_owner")
        if settings.metabase:
            ensure_role(admin, "purchases_metabase")
            ensure_database(admin, settings.application_database, "purchases_metabase")
            app_password = persistent_secret(settings.state_dir / "metabase-db-password")
            reader_password = persistent_secret(settings.state_dir / "reader-password")
            persistent_secret(settings.state_dir / "metabase-admin-password")
            for role, password in (
                ("purchases_metabase", app_password),
                ("purchases_reader", reader_password),
            ):
                admin.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(scram_verifier(password))
                    )
                )
            private_write(
                settings.state_dir / "metabase.env",
                "\n".join(
                    [
                        "MB_DB_TYPE=postgres",
                        "MB_DB_HOST=127.0.0.1",
                        f"MB_DB_PORT={settings.port}",
                        f"MB_DB_DBNAME={settings.application_database}",
                        "MB_DB_USER=purchases_metabase",
                        f"MB_DB_PASS={app_password}",
                        "MB_LOAD_SAMPLE_CONTENT=false",
                        "MB_ANON_TRACKING_ENABLED=false",
                        "",
                    ]
                ),
            )
        migrate(settings.url("purchases_owner"))
        grants = Path(__file__).parent / "analytics/grants.sql"
        if not grants.exists():
            grants = Path(__file__).resolve().parents[2] / "scripts/grants.sql"
        with psycopg.connect(
            dbname=settings.database,
            user="purchases_owner",
            host=settings.socket_dir,
            port=settings.port,
        ) as owner:
            owner.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            owner.execute(grants.read_text())
        log.info("Local databases, migrations and grants provisioned")


def from_environment() -> ProvisionSettings:
    return ProvisionSettings(
        state_dir=Path(os.environ["HP_PROVISION_STATE_DIR"]),
        database=os.environ.get("HP_DATABASE_NAME", "household_purchases"),
        application_database=os.environ.get("HP_METABASE_DATABASE", "household_purchases_metabase"),
        socket_dir=os.environ.get("HP_POSTGRES_SOCKET", "/run/postgresql"),
        port=int(os.environ.get("HP_POSTGRES_PORT", "5432")),
        metabase=os.environ.get("HP_METABASE_ENABLED", "true") == "true",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        provision(from_environment())
        return 0
    except Exception as exc:
        # SQL exceptions can contain credential verifiers; never emit them.
        log.error(
            "Local-stack provisioning failed: %s; check database ownership, roles and private state permissions",
            str(exc) if isinstance(exc, ProvisionError) else type(exc).__name__,
        )
        return 1
