#!/usr/bin/env bash
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true || ${BUILDKITE:-} == true ]] || exit 1
[[ $(id -u) != 0 ]] || { echo "Repository permission tests require an unprivileged user." >&2; exit 1; }
uv sync --locked
# verification.md runs pytest and repository checks together; do not run twice.
bash docs/dev/verification.md
node --test tests/frontend/*.test.mjs tests/frontend_kanban.mjs
uv run ruff check src tests scripts integrations
git diff --check
uv build --out-dir "$DG_CI_OUTPUT_ROOT/packages"
uv venv "$DG_CI_TMP/ontology-installed"
uv export --locked --no-emit-project --no-dev --output-file "$DG_CI_TMP/ontology-constraints.txt"
uv pip install --python "$DG_CI_TMP/ontology-installed/bin/python" \
  -r "$DG_CI_TMP/ontology-constraints.txt" "$DG_CI_OUTPUT_ROOT"/packages/devgraph-*.whl
uv run python scripts/qualify_ontology_install.py \
  --python "$DG_CI_TMP/ontology-installed/bin/python"
mkdir "$DG_CI_TMP/ontology-sdist"
tar -xzf "$DG_CI_OUTPUT_ROOT"/packages/devgraph-*.tar.gz -C "$DG_CI_TMP/ontology-sdist"
uv build "$DG_CI_TMP"/ontology-sdist/devgraph-* --offline --wheel \
  --out-dir "$DG_CI_TMP/ontology-rebuilt"
uv pip install --offline --reinstall --no-deps \
  --python "$DG_CI_TMP/ontology-installed/bin/python" "$DG_CI_TMP"/ontology-rebuilt/*.whl
uv run python scripts/qualify_ontology_install.py \
  --python "$DG_CI_TMP/ontology-installed/bin/python"
uv run python scripts/build_beta.py --output-dir "$DG_CI_TMP/beta"
cp "$DG_CI_TMP/beta/"* "$DG_CI_OUTPUT_ROOT/packages"/
