"""A newly tracked standalone lockfile must enter the audit gate automatically."""

import os
import shutil
import subprocess
from pathlib import Path


def test_every_tracked_lock_is_audited_even_after_a_failure(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(root / "scripts/audit-rust-locks.py", scripts)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in ("Cargo.lock", "tools/new-adapter/Cargo.lock"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture")
        subprocess.run(["git", "add", name], cwd=tmp_path, check=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    cargo = binary / "cargo"
    cargo.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AUDIT_CALLS"\nexit 1\n')
    cargo.chmod(0o755)
    log = tmp_path / "audit-calls"
    result = subprocess.run(
        ["python3", str(scripts / "audit-rust-locks.py"), "--no-fetch"],
        env={
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "AUDIT_CALLS": str(log),
        },
    )
    assert result.returncode == 1
    assert log.read_text().splitlines() == [
        "audit --file Cargo.lock --no-fetch",
        "audit --file tools/new-adapter/Cargo.lock --no-fetch",
    ]
