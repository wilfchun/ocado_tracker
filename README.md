# Household purchases

Private Ocado **provisional delivery-summary expenditure** analytics. These emails
are not final receipts or bank charges. Product names identify catalogue entries;
pack sizes, SKUs and individual multipack contents remain unknown.

## Development and offline parsing

Use `nix develop`, then `uv sync --locked` and `uv run pytest`.
Alternatively use Python 3.12+ and uv. Python dependencies are locked in `uv.lock`;
Nix dependencies use the revision pinned in `flake.nix` / `flake.lock`.

```
uv run household-purchases import-eml tests/fixtures/upcoming.eml --dry-run
```

Dry-run requires no credentials and writes neither database nor archive. Add
`--show-json` only when you want private purchase contents printed locally.

## Supported source and decisions

The original private sample is not in this repository. The fixture is synthetic:
it reproduces the documented product-table structure, five representative rows,
30 lines, 34 units and documented monetary totals; the remaining names and values
are synthetic. Summary labels and ISO explicit date wording are adapter contracts,
not verified against the unavailable original HTML. Review a real sample privately
before backfilling. Unknown monetary table rows fail for review. Only `delivery is
today`/`delivery today` with a timezone-aware source timestamp, or an explicit
`delivery date: YYYY-MM-DD` / `delivery on YYYY-MM-DD`, are supported.

The line prices already include offers. Quantity is never multiplied into them;
savings are never deducted twice. All money is integer pence, parsed with Decimal.
Final receipts, substitutions, refunds, vouchers and bag returns require real
sanitised examples and are intentionally deferred (phase 6).

## Database and archive

Use a private PostgreSQL database (not SQLite). Set `DATABASE_URL_FILE` to a
0600 file containing a SQLAlchemy `postgresql+psycopg://...` URL, or set
`DATABASE_URL` locally. Credential file takes precedence. Create the schema with
`uv run alembic upgrade head` using the schema owner's credentials. The runtime
importer uses a separate writer role; deployment grants are provided later below.

Set `RAW_EMAIL_DIR` to a persistent directory, mode 0700. Each original MIME file
is atomically archived as its SHA256 with mode 0600 before processing. Back up
both the database and this directory. Never put real emails under tests/fixtures.

```
uv run household-purchases import-eml /private/order.eml
uv run household-purchases status
uv run household-purchases retry-failed
uv run household-purchases reprocess --source-id 42
```

Changed summaries for the same order become `order_conflict` failures. Explicit
reprocess validates and replaces lines atomically, retains previous source records
and preserves catalogue classifications. Failed reprocessing leaves accepted facts
intact. Already accepted final orders cannot be replaced. A database advisory lock
serializes import and catalogue writes across processes. An extra parsed-content
hash distinguishes identical order facts from conflicts. Source uniqueness applies
to account/hash and account/remote ID; two remote IDs with identical bytes reuse
the same archived source record. First/last seen dates are observed historical
bounds, including prior accepted versions.

Run integration tests against an **empty disposable database**:

```
TEST_DATABASE_URL=postgresql+psycopg:///purchases_test uv run pytest
```

This deliberately drops the public/analytics schemas in that test database. Without
that variable, PostgreSQL tests skip; parser and API tests need no server.
