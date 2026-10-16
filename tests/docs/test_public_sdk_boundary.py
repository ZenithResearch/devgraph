"""Public protocol/web builds must not require the retired archive or Wallet access."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_workspace_resolves_no_private_git_source():
    lock = (ROOT / "Cargo.lock").read_text()
    assert "git+" not in lock
    for crate in ("devgraph-client-core", "devgraph-web", "devgraph-work-protocol"):
        manifest = (ROOT / "crates" / crate / "Cargo.toml").read_text()
        assert "git =" not in manifest
        assert "castalia-wallet" not in manifest
    workspace = (ROOT / "Cargo.toml").read_text()
    assert ('exclude = ["crates/devgraph-client-native", '
            '"crates/devgraph-browser-host"]') in workspace


def test_archive_is_not_an_active_package_dependency():
    manifests = [ROOT / "Cargo.lock", *ROOT.glob("crates/*/Cargo.toml"),
                 *ROOT.glob("crates/*/Cargo.lock"), ROOT / "packages/web/package-lock.json"]
    for path in manifests:
        assert "devgraph-private-history" not in path.read_text(), path
