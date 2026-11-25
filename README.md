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

## Fastmail history and scheduled scans

Create a **read-only JMAP token** in Fastmail Settings → Privacy & Security → API
tokens. Keep it in a private file referenced by `FASTMAIL_TOKEN_FILE`. This is a
broad mailbox credential. No mutation API methods exist in this client. Session
and download destinations must be HTTPS on the three explicit Fastmail hosts;
redirects are rejected before credentials can be forwarded elsewhere.

```
uv run household-purchases import-history --since 2025-01-01 --dry-run
uv run household-purchases import-history --since 2025-01-01
uv run household-purchases sync
```

`--since` is inclusive, `--until` exclusive, at midnight in `TIMEZONE` (default
Europe/London). Both commands deliberately do a full filtered scan, including
archived mail, with pagination and local exact header checks. No incremental
cursor is needed initially; `SYNC_OVERLAP_DAYS` is reserved and unused. A changing
query state interrupts the scan for safe rerun. Completed scans are recorded only
after downloads and durable source recording. Parse failures remain retryable;
download failures remain discoverable on the next scan. Runs containing failures
exit 1 while independently valid orders still import. HTTP retries are bounded
at four attempts with backoff (numeric Retry-After capped at 30 seconds).

Optional settings: `SOURCE_ACCOUNT` (non-secret identifier, default `fastmail`),
`FASTMAIL_ACCOUNT_ID` (JMAP override), `OCADO_SENDER`, `OCADO_SUBJECT`,
`OCADO_RECIPIENT`, `OCADO_MAILBOX_ID`. Unset recipient/mailbox filters include all
matching mail. `LOG_LEVEL` defaults to INFO. Secrets are read only by commands
that need them; offline dry-run needs no environment configuration.

Integration references checked during implementation:
[Fastmail API](https://www.fastmail.com/dev/),
[read-only token scopes](https://www.fastmail.help/hc/en-us/articles/5254602856719-API-tokens),
[JMAP session and downloads (RFC 8620)](https://www.rfc-editor.org/rfc/rfc8620.html),
[Email/query and Email/get (RFC 8621)](https://www.rfc-editor.org/rfc/rfc8621.html).
Live import/backfill has not run: supply private credentials and review a small
real sample before using historical import.

## Categories

```
uv run household-purchases classify --limit 100
uv run household-purchases categories list
uv run household-purchases products uncategorised
uv run household-purchases products set-category 123 drinks.soft-drinks
uv run household-purchases products set-category 123 -
```

Rules classify only unclassified products; existing decisions are preserved.
Manual assignments update every historical analytical row through catalogue joins.
`-` clears a category and keeps that manual decision during future classification.
Uncategorised differs from the deliberate `Other` category. Two-level taxonomy
slugs are seeded by migration; edit category labels/descriptions with the schema
owner, retaining stable slugs. A trigger rejects cycles/deeper hierarchies.

The optional model is disabled by default. Set `LLM_ENABLED=true`, `LLM_MODEL`,
and optionally `LLM_BASE_URL` (default `http://127.0.0.1:8080/v1`) and
`LLM_API_KEY_FILE`. Configure a trusted local endpoint; only product names and the
allowed taxonomy are submitted. JSON schema requests and independent local
validation reject invented slugs and malformed responses. Each request times out
after 15 seconds; a command handles at most 1000 products (default 100). Failures
leave products unclassified. Financial ingestion never calls the model; run
classification separately. No brand, SKU or pack-size guesses are stored.
