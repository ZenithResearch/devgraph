#!/usr/bin/env bash
set -euo pipefail
[[ ${BUILDKITE:-} == true ]] || exit 1
[[ $(id -u) != 0 ]] || { echo "Repository permission tests require an unprivileged user." >&2; exit 1; }
uv sync --locked
# verification.md runs pytest and repository checks together; do not run twice.
bash docs/dev/verification.md
node --test tests/frontend/*.test.mjs tests/frontend_kanban.mjs
uv run ruff check src tests scripts integrations
git diff --check
uv build --out-dir build/buildkite/packages
uv run python scripts/build_beta.py --output-dir "$DG_CI_TMP/beta"
cp "$DG_CI_TMP/beta/"* build/buildkite/packages/
