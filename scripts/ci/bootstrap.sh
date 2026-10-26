#!/usr/bin/env bash
# Sourced only on disposable hosted CI runners. Downloads are checked before use.
set -euo pipefail
: "${DG_CI_TMP:?run.sh must allocate a disposable directory}"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    node_archive=node-v24.18.0-linux-x64.tar.xz
    node_sha=55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742
    uv_platform=x86_64-unknown-linux-gnu
    uv_sha=0c6bab77a67a445dc849ed5e8ee8d3cb333b6e2eba863643ce1e228075f27943
    rust_platform=x86_64-unknown-linux-gnu
    rust_sha=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c
    apt=(apt-get); if [[ $(id -u) != 0 ]]; then apt=(sudo -n apt-get); fi
    "${apt[@]}" update
    DEBIAN_FRONTEND=noninteractive "${apt[@]}" install -y --no-install-recommends \
      build-essential ca-certificates curl git xz-utils pkg-config libssl-dev python3
    ;;
  Darwin-arm64)
    node_archive=node-v24.18.0-darwin-arm64.tar.gz
    node_sha=e1a97e14c99c803e96c7339403282ea05a499c32f8d83defe9ef5ec66f979ed1
    uv_platform=aarch64-apple-darwin
    uv_sha=4b69a4e366ec38cd5f305707de95e12951181c448679a00dce2a78868dfc9f5b
    rust_platform=aarch64-apple-darwin
    rust_sha=20ef5516c31b1ac2290084199ba77dbbcaa1406c45c1d978ca68558ef5964ef5
    ;;
  *) echo 'Unsupported disposable agent platform' >&2; exit 1 ;;
esac
fetch_checked() {
  local url=$1 destination=$2 expected=$3
  curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --tlsv1.2 --retry 3 --connect-timeout 30 --max-time 900 "$url" -o "$destination"
  python3 - "$destination" "$expected" <<'PY'
import hashlib, pathlib, sys
with pathlib.Path(sys.argv[1]).open('rb') as stream:
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(chunk)
if h.hexdigest() != sys.argv[2]:
    raise SystemExit('Tool archive checksum mismatch')
PY
}
mkdir -p "$DG_CI_TMP/node" "$DG_CI_TMP/uv"
fetch_checked "https://nodejs.org/dist/v24.18.0/$node_archive" "$DG_CI_TMP/node.archive" "$node_sha"
tar -xf "$DG_CI_TMP/node.archive" -C "$DG_CI_TMP/node" --strip-components=1
fetch_checked "https://github.com/astral-sh/uv/releases/download/0.11.6/uv-$uv_platform.tar.gz" "$DG_CI_TMP/uv.tar.gz" "$uv_sha"
tar -xzf "$DG_CI_TMP/uv.tar.gz" -C "$DG_CI_TMP/uv" --strip-components=1
export CARGO_HOME="$DG_CI_TMP/cargo" RUSTUP_HOME="$DG_CI_TMP/rustup"
export UV_CACHE_DIR="$DG_CI_TMP/uv-cache" UV_PYTHON_INSTALL_DIR="$DG_CI_TMP/python"
export PATH="$DG_CI_TMP/node/bin:$DG_CI_TMP/uv:$CARGO_HOME/bin:$PATH"
export CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=2 CARGO_NET_GIT_FETCH_WITH_CLI=true
export RUSTUP_TOOLCHAIN=1.96.0
uv python install 3.10
fetch_checked "https://static.rust-lang.org/rustup/archive/1.28.2/$rust_platform/rustup-init" "$DG_CI_TMP/rustup-init" "$rust_sha"
chmod 0755 "$DG_CI_TMP/rustup-init"
"$DG_CI_TMP/rustup-init" -y --no-modify-path --profile minimal --default-toolchain none
rustup toolchain install 1.96.0 --profile minimal --component rustfmt,clippy --target wasm32-unknown-unknown
