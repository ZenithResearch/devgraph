# Export and redaction

The policy package classifies records and supports four modes:

- `internal` returns serialized records, including private records;
- `redacted` omits private records and strips or redacts configured sensitive
  fields and private reference IDs;
- `public_safe_summary` returns counts without record content;
- `denied` raises a policy denial and has no HTTP route.

Internal export requires `devgraph.export.internal`. Redacted and public-safe
summary require `devgraph.export.redacted`. An insufficient grant fails closed;
the service does not silently downgrade modes.

HTTP exports fetch strict `{kind, ids}` selections through the authorized
facade. They preserve order and duplicates, accept an empty selection, and fail
the whole request if any ID is missing. Export permission is sufficient for
this fetch; a separate read grant is not required.

The shared redaction helpers also sanitize API errors, logs, event summaries,
monitor titles, and storage-health detail. Policy is deterministic code, not a
claim that every possible secret can be recognized. Callers must still avoid
placing credentials or private payloads in titles and other public-facing
metadata.

```bash
uv run pytest tests/policy tests/auth/test_export_enforcement.py \
  tests/api/test_exports.py -q
```
