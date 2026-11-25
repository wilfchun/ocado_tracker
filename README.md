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

The supplied Ocado email now has a sanitised regression fixture at
`tests/fixtures/ocado_upcoming_sanitised.eml`. It preserves the real HTML layout,
30 product rows, one-cell summary and quoted-printable encoding, while replacing
personal details and removing links/tracking resources. The original is retained
locally in ignored `tests/private/ocado_upcoming_original.eml` with private
permissions. See `tests/fixtures/README.md` for fixture provenance.

The parser supports the observed “receipt for order …” reference, “order today”
date wording, and negative offers savings displayed in the summary. It uses the
timezone-aware source timestamp for “today”. The earlier synthetic two-column
summary fixture and explicit ISO date contracts remain covered. Unknown monetary
summary fields and actual substitution/adjustment sections fail for review;
no-substitutions statements and general refund policy text do not imply changes
to an order. Only this supplied upcoming-delivery layout has real-email coverage.

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

## NixOS deployment

`nix build` builds the CLI and its bundled migrations; `nix flake check` also
checks the NixOS module. The flake supports aarch64-linux and x86_64-linux and
pins nixpkgs `1412caf7bf9e660f2f962917c14b1ea1c3bc695e`. Python's uv lock and
nixpkgs each pin their own dependency closure; tests run in both environments.

Add this flake as `inputs.household-purchases` in the server flake, then:

```nix
{ inputs, ... }: {
  imports = [ inputs.household-purchases.nixosModules.default ];
  services.household-purchases = {
    enable = true;
    databaseUrlFile = "/run/secrets/purchases-db-url";
    fastmailTokenFile = "/run/secrets/fastmail-jmap";
    syncSchedule = "hourly";
    # classificationSchedule = null; # disables the separate daily timer
    # llm = { enable = true; baseUrl = "http://127.0.0.1:8080/v1"; model = "your-model"; };
  };
}
```

Secrets must be runtime absolute **string paths**, never Nix path literals or
`builtins.readFile` secret values. sops-nix/agenix may provide these paths. systemd
LoadCredential makes private copies readable by the dedicated unprivileged user.
The module creates a private persistent data directory, hardens both oneshot
services, and opens no firewall ports. Sync and classification have separate
timers; model failure cannot fail ingestion. Same-unit timers cannot overlap;
the shared database lock protects CLI/import/reprocess/migration writes too.
Custom `dataDir`, timezone, filters and optional model API-key paths are supported.

Provision database roles as the PostgreSQL administrator:

```
psql -v ON_ERROR_STOP=1 -f scripts/bootstrap-roles.sql postgres
psql -v ON_ERROR_STOP=1 postgres -c 'CREATE DATABASE household_purchases OWNER purchases_owner'
```

Set login passwords separately with `psql`'s `\password` or use authenticated
local peer mappings in the host's existing PostgreSQL setup. Do not expose
PostgreSQL publicly. Configure the importer URL with `postgresql+psycopg` driver,
private host/socket, database and **purchases_importer** role. Use a different
0600 URL file for the **purchases_owner** role for migrations.

For each upgrade, stop both timers and wait for active jobs, back up, then run
`DATABASE_URL_FILE=/private/owner-url household-purchases migrate` explicitly as
an operator able to read that credential. Reapply grants as owner/admin:

```
psql -v ON_ERROR_STOP=1 household_purchases -f scripts/grants.sql
```

Start timers only after migrations succeed. Never give the runtime importer or
Metabase schema ownership. The writer has DML on purchase/source/catalogue tables,
SELECT on categories and sequence usage; it cannot migrate. The reader receives
only analytical view access. Default view owner privileges allow those views to
read base tables while denying direct source-table access. Tests connect as the
actual writer and reader logins to check this, including rejected writes.

## Metabase

Metabase is optional and independent of ingestion. The pinned nixpkgs module was
inspected directly: it provides `services.metabase.listen.ip`, `listen.port` and
`openFirewall`, and accepts additional environment variables via the systemd
service. See [nix/metabase-example.nix](nix/metabase-example.nix). Import it only
on the intended server. It binds to 127.0.0.1:3000 and leaves the firewall closed.
Use an SSH tunnel or the existing authenticated private reverse proxy.

Create a separate `metabase_app` PostgreSQL database with a separate login, then
provide `/run/secrets/metabase-app-env` as a root-readable 0600 environment file:

```
MB_DB_TYPE=postgres
MB_DB_HOST=127.0.0.1
MB_DB_PORT=5432
MB_DB_DBNAME=metabase_app
MB_DB_USER=metabase_app
MB_DB_PASS=replace-locally
```

The importer never accesses that application database. Metabase's own application
DB holds dashboard state. In Metabase Admin → Databases add the purchases DB using
**purchases_reader**, restrict sync to the analytics schema and leave writable
features disabled. Connect over a private network; enable certificate-verified TLS
for remote PostgreSQL. Do not use the application's write credentials for analytics.

Create dashboards from
[src/household_purchases/analytics/dashboards.sql](src/household_purchases/analytics/dashboards.sql):
spending overview, category trends, highest spend/frequency, price history, offers
and charges, and data quality. Add date, retailer, category and status filters in
the question builder as applicable. Weekly charts use the same order question with
week granularity. Always label dashboards **Ocado delivery-summary expenditure**.
Order totals belong to the order view; never sum them through a line join. Price
history is discounted price per reported unit, not a pure inflation measure.
Use `analytics.import_health` for aggregate scan times, failed/pending counts,
provisional orders and unclassified products; the reader cannot see individual
source metadata. The private `status` CLI exposes the same operational counts.

References: [PostgreSQL connection](https://www.metabase.com/docs/latest/databases/connections/postgresql),
[minimum reader privileges](https://www.metabase.com/docs/latest/databases/users-roles-privileges),
[Metabase application database](https://www.metabase.com/docs/latest/installation-and-operation/configuring-application-database).
Manual dashboard creation is the initial workflow; no live Metabase instance or
server configuration was supplied, so deployment/dashboard creation remains local
operator work.

## Monitoring, backups and recovery

Inspect `journalctl -u household-purchases-sync` and
`systemctl list-timers household-purchases-*`. Runs log counts, source IDs, parser
version and status; `status` reports completed scans, last import, failed/pending
sources and unclassified products. Failures exit nonzero. `source_messages` stores
safe error codes; inspect private archived mail locally to investigate formats.
Never paste raw MIME, tokens or SQL driver exceptions into public logs/issues.

Use the server's existing encrypted backup workflow for:

- A PostgreSQL custom-format dump (`pg_dump -Fc household_purchases`), including
  schema/migrations, catalogue, source records and analytics.
- The entire private raw directory (and Metabase application database for dashboards).
- Role provisioning/grants and separately protected credential recovery material.

Stop both timers and wait for jobs before taking a coordinated DB/archive snapshot;
resume them after the snapshot. Or use an existing consistent PostgreSQL/filesystem
backup mechanism. Protect permissions and encryption throughout. Additional archive
files after an interrupted import are harmless and may be reimported.

Restore into an empty private database owned by purchases_owner with
`pg_restore --no-owner --no-acl`, restore the raw directory to mode 0700/files 0600,
reapply grants, configure the recovered raw path, then run `status` and rerun a
small known import. It should report duplicate with unchanged line count/spend.
Start timers after verification. Keep previous backups before migrating/repairing;
prefer restoring a tested backup over blindly downgrading financial data.

`tests/test_recovery.py` exercises dump + archive restoration into a separate test
database and verifies duplicate import and explicit reprocessing after restore.
It runs when PostgreSQL CLI tools are on PATH. All database tests require a
**disposable** `TEST_DATABASE_URL`; the test login needs role/database creation for
permissions and recovery tests. They must never point at production.

## Remaining acceptance work

Phases 1–5 are implemented, with regression coverage for the supplied real
upcoming-delivery format. Before production: run a small credentialed Fastmail
dry-run, review failed historical layouts, backfill, deploy to the target host
and create dashboards. Phase 6 remains future work requiring real
receipt/substitution/refund/voucher/bag-return examples.

Initial MVP verification on aarch64-linux: 51 tests passed with disposable PostgreSQL, including
actual database roles and dump/archive restoration. `nix flake check -L` and
`nix develop --command python --version` passed. The built Nix CLI migrated an
empty DB, imported the synthetic fixture twice (one order/30 lines/9291 pence),
classified five products, and reported no failed/pending sources. x86_64-linux
is exposed by the flake but was not built on this host.

After adding the real-email fixture: 49 offline tests pass, including the real
format and seven invalid/unsupported variants. Ten PostgreSQL integration tests
are skipped without a running test database. The original private email was also
parsed locally and reconciles to the same 30 lines, 34 units and 9291 pence.
