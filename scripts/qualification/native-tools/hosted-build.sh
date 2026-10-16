#!/bin/bash
# Manual build of qualification tools only. No credentials, grants or signing.
set -euo pipefail
umask 077
test "${BUILDKITE:-}" = true
test "${BUILDKITE_SOURCE:-}" = ui
test "${BUILDKITE_PULL_REQUEST:-false}" = false
test "$(uname -s)-$(uname -m)" = Darwin-arm64
case "$(/usr/sbin/sysctl -n hw.model)" in VirtualMac*) ;; *) exit 1 ;; esac
test "$(id -u)" != 0
: "${DEVGRAPH_TOOLING_CHECKOUT:?Use an exact reviewed tooling checkout}"
: "${DEVGRAPH_TOOLING_REVISION:?Use the immutable tooling revision}"
test "$(git -C "$DEVGRAPH_TOOLING_CHECKOUT" rev-parse HEAD)" = "$DEVGRAPH_TOOLING_REVISION"
test -z "$(git -C "$DEVGRAPH_TOOLING_CHECKOUT" status --porcelain --untracked-files=all)"
wallet_source=$(pwd -P)
test "$(git rev-parse HEAD)" = 6c9c2e5819547ed6a2170c129ca6dc1d6c501516
test -z "$(git status --porcelain --untracked-files=all)"
export DG_CI_TMP
DG_CI_TMP=$(mktemp -d)
DG_CI_TMP=$(cd "$DG_CI_TMP" && pwd -P)
trap 'rm -rf "$DG_CI_TMP"' EXIT
for repo in devgraph secS-magik; do
  git init "$DG_CI_TMP/$repo"
  git -C "$DG_CI_TMP/$repo" remote add origin "https://github.com/ZenithResearch/$repo.git"
done
git -C "$DG_CI_TMP/devgraph" fetch --depth=1 origin 97642d3feb0695d9943053010db6948e2c53bc99
git -C "$DG_CI_TMP/devgraph" checkout --detach FETCH_HEAD
git -C "$DG_CI_TMP/secS-magik" fetch --depth=1 origin 254f744d3151f234e6cc58db239c694937a0838e
git -C "$DG_CI_TMP/secS-magik" checkout --detach FETCH_HEAD
source "$DEVGRAPH_TOOLING_CHECKOUT/.buildkite/bootstrap.sh"
# Reuse only the existing Wallet repository connection, then resolve Cargo from
# the local Git object database with credential helpers disabled.
python3 "$DG_CI_TMP/devgraph/scripts/ci/buildkite.py" wallet-revisions > "$DG_CI_TMP/wallet-revisions"
while IFS= read -r revision; do
  git -C "$wallet_source" fetch origin "$revision"
done < "$DG_CI_TMP/wallet-revisions"
export GIT_CONFIG_GLOBAL="$DG_CI_TMP/gitconfig" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
unset GIT_CONFIG_COUNT GITHUB_TOKEN GH_TOKEN SDK_WALLET_SOURCE_TOKEN SDK_SOURCE_APP_PRIVATE_KEY
git config --global credential.helper ''
wallet_git=$(git -C "$wallet_source" rev-parse --absolute-git-dir)
git config --global "url.file://$wallet_git.insteadOf" https://github.com/bananawalnut/castalia-wallet
git config --global --add "url.file://$wallet_git.insteadOf" https://github.com/bananawalnut/castalia-wallet.git
python3 "$DEVGRAPH_TOOLING_CHECKOUT/scripts/qualification/native-tools/build.py" \
  --devgraph "$DG_CI_TMP/devgraph" --secs "$DG_CI_TMP/secS-magik" --output "$DG_CI_TMP/tool-build"
mkdir -p "$wallet_source/build/qualification-tools"
cp "$DG_CI_TMP/tool-build/artifacts/qualification-authority-admin" \
   "$DG_CI_TMP/tool-build/artifacts/qualification-native-client" \
   "$DG_CI_TMP/tool-build/tool-provenance.json" "$wallet_source/build/qualification-tools/"
python3 - "$wallet_source/build/qualification-tools/tool-provenance.json" "$DEVGRAPH_TOOLING_REVISION" <<'PIN_TOOLING'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]); report=json.loads(p.read_text())
report['tooling_revision']=sys.argv[2]
p.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
PIN_TOOLING
git -C "$wallet_source" diff --exit-code
git -C "$DEVGRAPH_TOOLING_CHECKOUT" diff --exit-code
printf '%s\n' 'Qualification tools built only. No Wallet identity, authority provisioning or signing scenario.'
