#!/usr/bin/env bash
set -euo pipefail
export DG_EVIDENCE_BUILD DG_EVIDENCE_JOB
DG_EVIDENCE_BUILD=$(buildkite-agent meta-data get evidence-build)
DG_EVIDENCE_JOB=$(buildkite-agent meta-data get evidence-job)
python3 - <<'PY'
import os, re
for name in ('DG_EVIDENCE_BUILD', 'DG_EVIDENCE_JOB'):
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', os.environ[name]):
        raise SystemExit('Use exact evidence build/job UUIDs')
PY
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p build/buildkite/installed
# Remove stale success before download/verification; failure cannot retain a pass.
rm -f build/buildkite/installed/admission.json
buildkite-agent artifact download qualification.tar.gz "$scratch" \
  --build "$DG_EVIDENCE_BUILD" --step "$DG_EVIDENCE_JOB"
python3 scripts/ci/buildkite.py admit "$scratch" > "$scratch/admission.json"
mv "$scratch/admission.json" build/buildkite/installed/admission.json
