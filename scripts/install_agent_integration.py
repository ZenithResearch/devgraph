#!/usr/bin/env python3
"""Install one reviewed integration into a fresh agent home, without credentials."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins/devgraph/skills/devgraph"


def copy_source(source: Path, destination: Path) -> None:
    """Copy regular package files only; never follow a packaged symlink."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("integration source is unavailable or is a symlink")
    destination.mkdir()
    for item in sorted(source.iterdir()):
        if item.name in {"__pycache__", ".DS_Store"} or item.suffix == ".pyc":
            continue
        if item.is_symlink():
            raise ValueError("integration packages must not contain symlinks")
        target = destination / item.name
        if item.is_dir():
            copy_source(item, target)
        elif item.is_file():
            shutil.copyfile(item, target)
            target.chmod(0o600)
        else:
            raise ValueError("integration packages must contain regular files only")


def install(kind: str, home: Path, *, source_root: Path = ROOT) -> Path:
    home = home.expanduser().resolve()
    group = "plugins" if kind == "hermes" else "skills"
    parent = home / group
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ValueError("installation group must not be a symlink")
    target = parent / "devgraph"
    if target.exists() or target.is_symlink():
        raise ValueError("Devgraph is already installed; use a fresh profile or move it first")
    skill = source_root / "plugins/devgraph/skills/devgraph"
    if not (skill / "SKILL.md").is_file():
        raise ValueError("canonical Devgraph skill is missing")
    with tempfile.TemporaryDirectory(prefix=".devgraph-install-", dir=parent) as temporary:
        staged = Path(temporary) / "devgraph"
        if kind == "hermes":
            copy_source(source_root / "integrations/hermes/devgraph", staged)
            (staged / "skills").mkdir(exist_ok=True)
            copy_source(skill, staged / "skills/devgraph")
        elif kind == "codex-skill":
            copy_source(skill, staged)
        else:
            raise ValueError("unsupported integration")
        for name in ("LICENSE", "NOTICE"):
            if (source_root / name).is_file():
                shutil.copyfile(source_root / name, staged / name)
        # mkdir reserves the destination without overwriting any concurrent install.
        target.mkdir(mode=0o700)
        try:
            for child in staged.iterdir():
                os.rename(child, target / child.name)
        except BaseException:
            shutil.rmtree(target)
            raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("integration", choices=("hermes", "codex-skill"))
    parser.add_argument("--home", type=Path, help="Agent home (use a fresh profile for testing)")
    args = parser.parse_args()
    home = args.home or (
        Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        if args.integration == "hermes" else Path.home() / ".agents"
    )
    try:
        target = install(args.integration, home)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Integration was not installed: {exc}\n")
    print(json.dumps({"installed": args.integration, "path": str(target),
                      "configuration_changed": False, "credentials_copied": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
