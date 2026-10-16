#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in repository|protocol|sdk|companion|codeql) lane=$1 ;; *) exit 2 ;; esac
[[ ${BUILDKITE:-} == true ]] || { echo 'Use a disposable Buildkite agent.' >&2; exit 1; }
root=$(git rev-parse --show-toplevel)
cd "$root"
export DG_CI_TMP
DG_CI_TMP=$(mktemp -d)
# npm lockfile paths must use the physical macOS path, not the /var alias.
DG_CI_TMP=$(cd "$DG_CI_TMP" && pwd -P)
trap 'rm -rf "$DG_CI_TMP"' EXIT
mkdir -p "build/buildkite/$lane"
source .buildkite/bootstrap.sh
python3 scripts/ci/buildkite.py provenance "$lane" > "build/buildkite/$lane/provenance.json"

sdk_checks() {
  cargo +1.96.0 install wasm-bindgen-cli --version 0.2.127 --locked
  cargo test --locked --workspace --all-features
  cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
  cargo check --locked -p devgraph-web --target wasm32-unknown-unknown
  (
    cd packages/web
    npm ci --ignore-scripts
    npm audit --audit-level=low --json > "$root/build/buildkite/$lane/npm-audit.json"
    npm run build
    npm test
    if [[ $(uname -s) == Linux ]]; then
      npx playwright install --with-deps chromium firefox webkit
    else
      npx playwright install chromium firefox webkit
    fi
    npm run test:browser
    npm run build
    npm pack --ignore-scripts
  )
  mkdir "$DG_CI_TMP/consumer"
  cp packages/web/devgraph-web-0.2.0-preview.2.tgz "$DG_CI_TMP/consumer/"
  npm install --prefix "$DG_CI_TMP/consumer" --ignore-scripts \
    "$DG_CI_TMP/consumer/devgraph-web-0.2.0-preview.2.tgz" typescript@5.9.3 vite@7.3.7
  npm audit --prefix "$DG_CI_TMP/consumer" --audit-level=low --json \
    > "$root/build/buildkite/$lane/npm-consumer-audit.json"
  node packages/web/test/installed-consumer.mjs "$DG_CI_TMP/consumer"
}

case "$lane" in
  repository)
    if [[ $(id -u) == 0 ]]; then
      # Hosted Linux defaults to root, which bypasses filesystem denial tests.
      useradd --create-home --shell /bin/bash devgraph-ci
      chown -R devgraph-ci:devgraph-ci "$root" "$DG_CI_TMP"
      runuser --user devgraph-ci -- bash .buildkite/repository.sh
      chown -R root:root "$root"
    else
      bash .buildkite/repository.sh
    fi
    ;;
  protocol)
    cargo test --locked -p devgraph-work-protocol
    cargo test --locked -p devgraph-work-protocol --features hostile-map-order,hostile-number-repr
    cargo check --locked -p devgraph-work-protocol --target wasm32-unknown-unknown
    rustup toolchain install 1.85.1 --profile minimal
    cargo +1.85.1 test --locked -p devgraph-work-protocol --features hostile-map-order,hostile-number-repr
    cargo fmt --all --check
    ;;
  sdk)
    cargo install cargo-audit --version 0.22.1 --locked
    python3 scripts/audit-rust-locks.py
    sdk_checks
    ;;
  companion)
    [[ $(uname -s) == Darwin ]] || { echo 'Companion lane requires macOS.' >&2; exit 1; }
    : "${DEVGRAPH_WALLET_CHECKOUT:?Use the Wallet-connected companion pipeline}"
    : "${DEVGRAPH_REVISION:?Use the reviewed immutable Devgraph commit}"
    python3 scripts/ci/buildkite.py companion-preflight "$DEVGRAPH_WALLET_CHECKOUT"
    secs_revision=$(python3 -c 'import json; print(json.load(open("docs/dev/generic-wallet-source-set.json"))["sources"]["secs_generic_todo"]["revision"])')
    git init "$DG_CI_TMP/secs"
    git -C "$DG_CI_TMP/secs" remote add origin https://github.com/ZenithResearch/secS-magik.git
    git -C "$DG_CI_TMP/secs" fetch --depth=1 origin "$secs_revision"
    git -C "$DG_CI_TMP/secs" checkout --detach FETCH_HEAD
    test "$(git -C "$DG_CI_TMP/secs" rev-parse HEAD)" = "$secs_revision"
    # Fetch historical Wallet revisions while the existing checkout connection is
    # available. Cargo then resolves private source from that local Git database.
    python3 scripts/ci/buildkite.py wallet-revisions > "$DG_CI_TMP/wallet-revisions"
    while IFS= read -r revision; do
      git -C "$DEVGRAPH_WALLET_CHECKOUT" fetch origin "$revision"
    done < "$DG_CI_TMP/wallet-revisions"
    export GIT_CONFIG_GLOBAL="$DG_CI_TMP/gitconfig" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
    unset GIT_CONFIG_COUNT GITHUB_TOKEN GH_TOKEN SDK_WALLET_SOURCE_TOKEN SDK_SOURCE_APP_PRIVATE_KEY
    git config --global credential.helper ''
    wallet_git=$(git -C "$DEVGRAPH_WALLET_CHECKOUT" rev-parse --absolute-git-dir)
    git config --global "url.file://$wallet_git.insteadOf" https://github.com/bananawalnut/castalia-wallet
    # Git replaces a prefix: match the .git form too or it appends .git twice.
    git config --global --add "url.file://$wallet_git.insteadOf" https://github.com/bananawalnut/castalia-wallet.git
    cargo +1.96.0 install wasm-pack --version 0.14.0 --locked
    uv sync --locked
    for manifest in Cargo.toml crates/devgraph-client-native/Cargo.toml crates/devgraph-browser-host/Cargo.toml \
      "$DEVGRAPH_WALLET_CHECKOUT/Cargo.toml" "$DG_CI_TMP/secs/Cargo.toml" "$DG_CI_TMP/secs/tools/read-authority/Cargo.toml"; do
      cargo fetch --locked --manifest-path "$manifest"
    done
    uv run python scripts/sdk/companion.py --wallet "$DEVGRAPH_WALLET_CHECKOUT" \
      --secs "$DG_CI_TMP/secs" --build-root "$DG_CI_TMP/companion-targets"
    for manifest in crates/devgraph-client-native/Cargo.toml crates/devgraph-browser-host/Cargo.toml; do
      cargo test --locked --offline --manifest-path "$manifest"
      cargo clippy --locked --offline --manifest-path "$manifest" --all-targets -- -D warnings
    done
    sdk_checks
    ;;
  codeql)
    fetch_checked https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.27.2/codeql-bundle-linux64.tar.gz \
      "$DG_CI_TMP/codeql.tar.gz" f002864be6dd8d5d7bdb123aaf7291ec8e291012bd9f70b6d2362f730db52aeb
    tar -xzf "$DG_CI_TMP/codeql.tar.gz" -C "$DG_CI_TMP"
    for language in python javascript; do
      "$DG_CI_TMP/codeql/codeql" database create "$DG_CI_TMP/db-$language" \
        --language="$language" --source-root="$root" --build-mode=none
      "$DG_CI_TMP/codeql/codeql" database analyze "$DG_CI_TMP/db-$language" \
        "codeql/$language-queries:codeql-suites/$language-code-scanning.qls" \
        --format=sarif-latest --sarif-category="$language" \
        --output="build/buildkite/codeql/$language.sarif"
    done
    ;;
esac
git diff --exit-code
printf '%s\n' "$lane passed; this is not installed signing qualification or deployment."
