#!/usr/bin/env python3
"""Pinned-source launcher for private disposable qualification, never production."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from devgraph.session_agent_service import main  # noqa: E402

raise SystemExit(main())
