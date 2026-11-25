# Email fixtures

- `upcoming.eml`: original synthetic contract fixture; filler products are explicitly
  synthetic. Kept to test the simple two-column summary adapter.
- `ocado_upcoming_sanitised.eml`: derived from the supplied Ocado email, preserving
  its HTML layout, all 30 product rows, actual one-cell summary, negative savings,
  quoted-printable encoding, no-substitutions statement and refund policy copy.
  Recipient/name, order reference, message headers, links, tracking resources and
  identifying attributes have been removed or replaced with dummy values. Monetary
  values and product names are retained for regression assertions.

The original is retained locally at `tests/private/ocado_upcoming_original.eml`.
That directory is ignored by Git and must not be committed. Tests use only the
sanitised fixture and need no credentials, network, database or original file.
