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
