from __future__ import annotations

import ast
from pathlib import Path

CLIENT_PATH = Path("src/devgraph/client/http.py")
FORBIDDEN_IMPORT_PREFIXES = (
    "devgraph.api",
    "devgraph.auth",
    "devgraph.events.outbox",
    "devgraph.model.repository",
    "devgraph.policy",
    "devgraph.storage",
    "neo4j",
    "subprocess",
    "socket",
    "uvicorn",
)
FORBIDDEN_CALLS = {
    "getenv",
    "from_env",
    "sleep",
    "create_app",
    "register_routes",
    "bind",
    "listen",
    "Popen",
    "run",
}


def parsed_client() -> ast.Module:
    return ast.parse(CLIENT_PATH.read_text())


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def test_production_client_imports_no_internal_or_runtime_seams() -> None:
    imports: list[str] = []
    for node in ast.walk(parsed_client()):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in imports
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def test_production_client_has_exact_public_method_surface() -> None:
    module = parsed_client()
    client_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "DevgraphHttpClient"
    )
    public_methods = {
        node.name
        for node in client_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public_methods == {
        "execute_arena",
        "get_arena",
        "list_arenas",
        "get_arena_members",
        "get_work_arena",
        "execute_named_work",
        "accept_proposal",
        "archive_work",
        "convert_proposal",
        "create_work",
        "create_issue",
        "get_task_blockers",
        "get_work",
        "get_work_children",
        "get_work_relationships",
        "get_supporting_material",
        "get_work_document",
        "get_issue",
        "list_work",
        "list_issues",
        "patch_work",
        "query_cypher",
        "transition_issue_to_review",
        "transition_work_status",
    }


def test_client_has_no_forbidden_calls_app_state_or_hidden_retry_loop() -> None:
    module = parsed_client()
    calls = [dotted_name(node.func) for node in ast.walk(module) if isinstance(node, ast.Call)]
    assert not any(name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS for name in calls)
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr == "state"
        and dotted_name(node.value).endswith("app")
        for node in ast.walk(module)
    )
    loops = [
        node for node in ast.walk(module) if isinstance(node, (ast.For, ast.AsyncFor, ast.While))
    ]
    # The sole loop consumes a bounded HTTP response. It cannot issue another
    # HTTP request; no operation, including writes, has an automatic retry loop.
    assert len(loops) == 1
    loop = loops[0]
    assert isinstance(loop, ast.For)
    assert isinstance(loop.iter, ast.Call)
    assert dotted_name(loop.iter.func) == "incoming.iter_bytes"
    loop_calls = [dotted_name(node.func) for node in ast.walk(loop) if isinstance(node, ast.Call)]
    assert not any(name.endswith(("request", "stream")) for name in loop_calls)
    transport_calls = [name for name in calls if name == "self._transport.request"]
    assert len(transport_calls) == 2  # Existing typed Work transport + bounded Cypher transport.


def test_client_source_contains_no_ambient_configuration_or_unbounded_expansion() -> None:
    source = CLIENT_PATH.read_text()
    forbidden_literals = (
        "DEVGRAPH_",
        "os.environ",
        "app.state",
        "/exports",
        "/initiative-observations",
        "/monitor",
        "Hermes",
        "Matrix",
        "Dregg",
    )
    assert all(literal not in source for literal in forbidden_literals)
