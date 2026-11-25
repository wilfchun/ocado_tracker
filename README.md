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
