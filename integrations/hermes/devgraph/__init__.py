"""Standalone Hermes adapter; Devgraph CLI owns credentials and authorization."""

from pathlib import Path

from .tools import SCHEMAS, available, handle


def register(ctx):
    """Register through Hermes' public plugin API without modifying its core."""
    for schema in SCHEMAS:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="devgraph",
            schema=schema,
            handler=lambda args, _name=name, **kw: handle(_name, args),
            check_fn=available,
            description=schema["description"],
        )
    # The installer supplies the same canonical skill used by Codex.
    skill = Path(__file__).parent / "skills/devgraph/SKILL.md"
    if skill.is_file():
        ctx.register_skill("devgraph", skill, "Read and update the local Devgraph work graph.")
