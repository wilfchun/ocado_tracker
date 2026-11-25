import sys
from pathlib import Path

from household_purchases.cli import main


def test_offline_dry_run(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("RAW_EMAIL_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "household-purchases",
            "import-eml",
            str(Path(__file__).parent / "fixtures/upcoming.eml"),
            "--dry-run",
        ],
    )
    assert main() == 0
    assert "30 lines; total 9291 pence; provisional" in capsys.readouterr().out
    assert not (tmp_path / "archive").exists()


def stdin_bytes(monkeypatch, raw):
    import io

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    # Simulate the permission check that fails after runuser changes identities.
    original = Path.read_bytes

    def restricted_read(path):
        if path == Path("/dev/stdin"):
            raise PermissionError("inherited input cannot be reopened")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", restricted_read)


def test_stdin_aliases_dry_run(monkeypatch, tmp_path, capsys):
    from household_purchases.cli import arguments, execute
    from household_purchases.config import Settings

    raw = (Path(__file__).parent / "fixtures/ocado_upcoming_sanitised.eml").read_bytes()
    for name in ("-", "/dev/stdin"):
        stdin_bytes(monkeypatch, raw)
        args = arguments().parse_args(["import-eml", name, "--dry-run"])
        assert execute(args, Settings(raw_email_dir=tmp_path / "archive")) == 0
        assert "30 lines; total 9291 pence" in capsys.readouterr().out
    assert not (tmp_path / "archive").exists()


def test_stdin_import_preserves_exact_mime_bytes(monkeypatch, tmp_path, capsys):
    from household_purchases import cli
    from household_purchases.config import Settings

    # Binary MIME input must not pass through text decoding or newline conversion.
    raw = b"Content-Type: application/octet-stream\r\n\r\n\xff\x00\x80\r\n"
    imported = []

    class Engine:
        def dispose(self):
            pass

    class Importer:
        def __init__(self, *args):
            pass

        def ingest(self, value):
            imported.append(value)
            return 1, "imported"

    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg:///unused")
    monkeypatch.setattr(cli, "engine_for", lambda _: Engine())
    monkeypatch.setattr(cli, "Ingestor", Importer)
    for name in ("-", "/dev/stdin"):
        stdin_bytes(monkeypatch, raw)
        args = cli.arguments().parse_args(["import-eml", name])
        assert cli.execute(args, Settings(raw_email_dir=tmp_path / "archive")) == 0
        assert "status=imported" in capsys.readouterr().out
    assert imported == [raw, raw]
