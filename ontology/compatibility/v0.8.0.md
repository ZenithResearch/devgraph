# Compatibility v0.8.0

Work v1 and Arena v1 canonical request bytes and signature vectors remain pinned. New Work v2 has a separate digest domain, authority operation suffix and endpoint. V1 responses retain their old envelopes and synthesize archived status for compatibility; these statuses cannot classify progress. Legacy status is retained as historical metadata, outside the canonical progress model. Updated consumers must use `/todos/v2` and `/monitor/kanban/v2`. Unknown progress is absent, never an invented state.

Migration 28 writes explicit validated stage mappings and independent archive flags in one transaction with its journal. Ambiguous records retain absent progress plus a classification report. Work versions and timestamps are preserved so metadata mapping does not invalidate prior evidence revisions. Historical source values are retained in progress_migration_json. No backfilled approvals or decisions are created. Invalid canonical storage aborts with an operator hold; never force past a failed preflight.

New grants must explicitly admit v2 operation/resource pairs. Existing v1 grants confer no v2 write authority. Native bridge, SDK and Wallet builds must agree on protocol revision and progress capability; production policy changes and installed qualification are separate.
