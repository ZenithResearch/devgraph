"""Hermes adapter tests run real subprocesses against temporary fake CLIs only."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "integrations/hermes/devgraph"


@pytest.fixture
def plugin():
    name = "test_hermes_devgraph_plugin"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Actual subprocess and file boundaries; only Hermes config loading is replaced."""
    executable = tmp_path / "devgraph"
    audit = tmp_path / "calls.jsonl"
    settings = {"cli_path": str(executable), "timeout_seconds": 3}
    module = types.ModuleType("hermes_cli.config")
    module.load_config = lambda: {"plugins": {"entries": {"devgraph": settings}}}
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)

    def install(body):
        executable.write_text(
            f"#!{sys.executable} -I\n"
            "import json, os, stat, sys, time\nfrom pathlib import Path\n"
            "arguments = sys.argv[1:]\n"
            "call = {'arguments': arguments, 'environment': dict(os.environ)}\n"
            "if '--request-file' in arguments:\n"
            "    request = Path(arguments[arguments.index('--request-file') + 1])\n"
            "    key = Path(arguments[arguments.index('--idempotency-key-file') + 1])\n"
            "    call.update(request=json.loads(request.read_bytes()), key=key.read_text(),\n"
            "        request_bytes=request.read_text(),\n"
            "        file_mode=stat.S_IMODE(request.stat().st_mode),\n"
            "        directory_mode=stat.S_IMODE(request.parent.stat().st_mode))\n"
            f"with Path({str(audit)!r}).open('a') as stream:\n"
            "    stream.write(json.dumps(call) + '\\n')\n" + body + "\n",
        )
        executable.chmod(0o700)
        return executable

    return install, audit, settings


def tools(plugin):
    return sys.modules[plugin.__name__ + ".tools"]


def call(plugin, name, args):
    return json.loads(plugin.handle(name, args))


def request(domain="work", operation="create"):
    return {"schema": f"devgraph.{domain}-request.v1", "operation": operation,
            "kind": "Task" if domain == "work" else "Arena", "id": "example",
            "expected_version": None if operation == "create" else 1,
            "payload": {"id": "example", "title": "Read plans — test"}}


def test_registration_manifest_and_shared_skill(plugin, monkeypatch, tmp_path):
    captured = []
    skill = tmp_path / "skills/devgraph/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# shared skill")
    monkeypatch.setattr(plugin, "__file__", str(tmp_path / "__init__.py"))
    context = types.SimpleNamespace(
        register_tool=lambda **kwargs: captured.append(kwargs),
        register_skill=lambda *args: captured.append(args),
    )
    plugin.register(context)
    names = {entry["name"] for entry in captured if type(entry) is dict}
    assert names == {schema["name"] for schema in tools(plugin).SCHEMAS}
    manifest = (PLUGIN / "plugin.yaml").read_text()
    assert all("  - " + name in manifest for name in names)
    assert captured[-1][0:2] == ("devgraph", skill)
    # Actual registered handler accepts Hermes' task metadata and routes its own name.
    monkeypatch.setattr(plugin, "handle", lambda name, args: json.dumps({"name": name}))
    for entry in captured[:-1]:
        assert json.loads(entry["handler"]({}, task_id="fixture"))["name"] == entry["name"]


def test_packaged_skill_guides_narrow_work_authority():
    skill = PLUGIN.parents[2] / "plugins/devgraph/skills/devgraph/SKILL.md"
    reference = PLUGIN.parents[2] / "plugins/devgraph/skills/devgraph/references/work-api.md"
    combined = skill.read_text() + "\n" + reference.read_text()
    for phrase in (
        "operation, Work kind, Work ID, expected version",
        "wildcard Work kinds",
        "all descendants",
        "lifecycle authority",
        "Split unrelated edits into separate narrow write requests",
        "changing `Issue/example-issue` from version 7 to status `review`",
        "Adding `Task/setup-api` version 3 as a dependency",
        "evidence paths needed for that operation",
    ):
        assert phrase in combined


def test_read_actual_process_is_bounded_and_drops_inherited_secrets(plugin, fake_cli, monkeypatch):
    install, audit, _ = fake_cli
    install('print(json.dumps({"items": [{"id": "example", "title": "A plan"}]}))')
    monkeypatch.setenv("DEVGRAPH_DEV_TOKEN", "must-not-inherit")
    monkeypatch.setenv("DEVGRAPH_SIGNING_KEY_FILE", "/must/not/read")
    monkeypatch.setenv("HTTP_PROXY", "http://do-not-use.invalid")
    monkeypatch.setenv("PYTHONPATH", "/must/not/import")
    result = call(plugin, "devgraph_read", {"operation": "work", "kind": "Task"})
    assert result == {"ok": True, "data": {"items": [{"id": "example", "title": "A plan"}]}}
    observed = json.loads(audit.read_text())
    assert observed["arguments"] == ["query", "work", "Task", "--limit", "20"]
    blocked = {"DEVGRAPH_DEV_TOKEN", "DEVGRAPH_SIGNING_KEY_FILE", "HTTP_PROXY", "PYTHONPATH"}
    assert not blocked & set(observed["environment"])


@pytest.mark.parametrize("arguments,expected", [
    ({"operation": "arena", "id": "example"}, ["query", "arena", "example"]),
    ({"operation": "arena-members", "id": "example", "after_resource": "Task/child", "limit": 2},
     ["query", "arena-members", "example", "--limit", "2", "--after-resource", "Task/child"]),
    ({"operation": "arena-of", "kind": "Task", "id": "example"},
     ["query", "arena-of", "Task", "example"]),
    ({"operation": "blockers", "kind": "Task", "id": "example"},
     ["query", "blockers", "example", "--limit", "20"]),
    ({"operation": "work", "kind": "Issue", "include_archived": True, "after_id": "previous"},
     ["query", "work", "Issue", "--include-archived", "--limit", "20", "--after-id", "previous"]),
])
def test_typed_read_routes(plugin, fake_cli, arguments, expected):
    install, audit, _ = fake_cli
    install('print("{\\"items\\": []}")')
    assert call(plugin, "devgraph_read", arguments)["ok"]
    assert json.loads(audit.read_text())["arguments"] == expected


@pytest.mark.parametrize("arguments", [
    {"operation": "local restart"},
    {"operation": "work", "kind": "Task", "id": "--help"},
    {"operation": "work", "kind": "Task", "limit": 101},
    {"operation": "work", "kind": "Task", "limit": True},
    {"operation": "work", "kind": "Task", "url": "https://example.invalid"},
    {"operation": "work", "kind": "Task", "credential": "never-accepted"},
    {"operation": "work", "kind": "Task", "args": ["auth", "read", "show"]},
    {"operation": "blockers", "kind": "Issue", "id": "example"},
    {"operation": "arena-of", "kind": "Task", "id": "example", "limit": 1},
    {"operation": "children", "kind": "Task", "id": "example", "after_resource": "--help"},
    {"operation": "children", "kind": "Task", "id": "example", "after_resource": "Task:child"},
    {"operation": "children", "kind": "Task", "id": "example", "after_resource": "Unknown/child"},
    {"operation": "arena-members", "id": "example", "after_resource": "Project/child"},
])
def test_no_generic_commands_or_authority_inputs(plugin, fake_cli, arguments):
    install, audit, _ = fake_cli
    install('raise AssertionError("must not execute")')
    assert call(plugin, "devgraph_read", arguments)["error"] == "invalid_arguments"
    assert not audit.exists()


def test_unhealthy_status_is_useful_and_omits_paths_and_credentials(plugin, fake_cli):
    install, _, _ = fake_cli
    install('print(json.dumps({"healthy": False, "configured": True, '
            '"configuration": {"secret": "never-return"}, '
            '"api": {"api": {"readiness": {"current_applied_version": 26}}}})); sys.exit(1)')
    assert call(plugin, "devgraph_status", {}) == {
        "ok": True, "data": {"healthy": False, "configured": True, "migration_version": 26},
    }


@pytest.mark.parametrize("domain", ["work", "arena"])
def test_signed_operation_exact_forwarding_retries_private_files(plugin, fake_cli, domain):
    install, audit, _ = fake_cli
    install('print(json.dumps({"receipt": {"receipt_id": "receipt-1", "duplicate": False}}))')
    args = {"request": request(domain), "idempotency_key": "stable-operation-key-001"}
    first = call(plugin, f"devgraph_{domain}_operation", args)
    second = call(plugin, f"devgraph_{domain}_operation", args)
    assert first == second == {"ok": True, "data": {
        "receipt": {"receipt_id": "receipt-1", "duplicate": False},
    }}
    calls = [json.loads(line) for line in audit.read_text().splitlines()]
    assert calls[0]["request_bytes"] == calls[1]["request_bytes"]
    for observed in calls:
        assert observed["arguments"][0:2] == [domain, "create"]
        assert observed["request"] == args["request"]
        assert observed["key"] == args["idempotency_key"]
        assert observed["file_mode"] == 0o600 and observed["directory_mode"] == 0o700
        assert not Path(observed["arguments"][3]).exists()


def test_native_denial_does_not_fallback_or_expose_errors(plugin, fake_cli):
    install, audit, _ = fake_cli
    install('print(json.dumps({"error": "secS denied; secret=not-for-model"}), file=sys.stderr); '
            'sys.exit(2)')
    result = call(plugin, "devgraph_work_operation", {
        "request": request(), "idempotency_key": "stable-operation-key-001",
    })
    assert result["ok"] is False and result["outcome_unknown"] is True
    assert "identical request and idempotency key" in result["guidance"]
    assert "not-for-model" not in json.dumps(result)
    assert len(audit.read_text().splitlines()) == 1


def test_successful_results_redact_credentials_and_nested_security_fields(plugin, fake_cli):
    install, _, _ = fake_cli
    token = "dgread1_" + "x" * 43
    install(f'print(json.dumps({{"items": [{{"id": "example", "credential": "hidden", '
            f'"nested": {{"private_key": "hidden"}}, "description": "Bearer {token}"}}], '
            '"signed_projection": "hidden", "idempotency_key": "hidden", '
            '"api_key": "hidden", "headers": {"x-api-key": "hidden"}}))')
    result = call(plugin, "devgraph_read", {"operation": "arena"})
    assert result["ok"] and result["data"]["items"][0]["description"] == "[redacted]"
    assert "hidden" not in json.dumps(result) and token not in json.dumps(result)


def test_timeout_is_bounded_and_write_outcome_uncertain(plugin, fake_cli):
    install, _, settings = fake_cli
    settings["timeout_seconds"] = 1
    install("time.sleep(20)")
    start = time.monotonic()
    result = call(plugin, "devgraph_work_operation", {
        "request": request(), "idempotency_key": "stable-operation-key-001",
    })
    assert time.monotonic() - start < 4
    assert result["error"] == "cli_timeout" and result["outcome_unknown"] is True


def test_output_limit_stops_noisy_child(plugin, fake_cli):
    install, _, _ = fake_cli
    install('sys.stdout.write("x" * 400000); sys.stdout.flush(); time.sleep(20)')
    start = time.monotonic()
    result = call(plugin, "devgraph_read", {"operation": "arena"})
    assert time.monotonic() - start < 4
    assert result == {"ok": False, "error": "cli_output_limit"}


@pytest.mark.parametrize("changes", [
    {"credential": "never"}, {"url": "https://example.invalid"},
    {"cli_path": "relative/path"}, {"timeout_seconds": 121}, {"timeout_seconds": True},
])
def test_invalid_user_config_fails_closed(plugin, fake_cli, changes):
    install, audit, settings = fake_cli
    install('print("{}")')
    settings.update(changes)
    assert not plugin.available()
    assert call(plugin, "devgraph_status", {})["ok"] is False
    assert not audit.exists()


def test_unsafe_executable_is_unavailable(plugin, fake_cli):
    install, _, _ = fake_cli
    executable = install('print("{}")')
    executable.chmod(0o777)
    assert not plugin.available()


@pytest.mark.parametrize("mutate", [
    lambda args: args["request"].update(operation="local start"),
    lambda args: args["request"].update(schema="devgraph.work-request.v0"),
    lambda args: args["request"].update(expected_version=True),
    lambda args: args.update(idempotency_key="too-short"),
    lambda args: args.update(credential="never-accepted"),
    lambda args: args["request"].update(signed_projection="never-accepted"),
])
def test_invalid_write_arguments_never_start_cli(plugin, fake_cli, mutate):
    install, audit, _ = fake_cli
    install('print("{}")')
    args = {"request": request(), "idempotency_key": "stable-operation-key-001"}
    mutate(args)
    result = call(plugin, "devgraph_work_operation", args)
    assert result == {"ok": False, "error": "invalid_arguments"}
    assert not audit.exists()


def test_published_operations_match_versioned_devgraph_contract(plugin):
    from devgraph.arena_requests import ARENA_OPERATIONS
    from devgraph.work_requests import WORK_OPERATIONS

    assert tools(plugin).WORK_OPERATIONS == WORK_OPERATIONS
    assert tools(plugin).ARENA_OPERATIONS == ARENA_OPERATIONS


@pytest.mark.parametrize("operation", ["children", "arena-members"])
def test_forwarded_cursor_is_accepted_by_real_devgraph_client(plugin, operation):
    from devgraph.client.http import DevgraphHttpClient, DevgraphRequestContext

    received = []

    def request_transport(method, url, **_kwargs):
        received.append((method, url))
        return types.SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                                     json=lambda: {"items": []})

    args = {"operation": operation, "id": "parent", "after_resource": "Task/child"}
    if operation == "children":
        args["kind"] = "Issue"
    command = tools(plugin)._read_arguments(args)
    cursor = command[command.index("--after-resource") + 1]
    client = DevgraphHttpClient(transport=types.SimpleNamespace(request=request_transport),
                               base_url="http://example.invalid", timeout=1)
    context = DevgraphRequestContext(credential="synthetic-fixture")
    if operation == "children":
        client.get_work_relationships(context, kind="Issue", work_id="parent",
                                      relationship=operation, after_resource=cursor)
    else:
        client.get_arena_members(context, arena_id="parent", after_resource=cursor)
    assert len(received) == 1 and "after_resource=Task%2Fchild" in received[0][1]


def test_real_hermes_directory_discovery_registration_and_dispatch(tmp_path, fake_cli):
    """Opt-in checkout dependency; never discover the operator's installed plugins."""
    root = os.environ.get("DEVGRAPH_TEST_HERMES_ROOT")
    if not root:
        pytest.skip("set DEVGRAPH_TEST_HERMES_ROOT to test an actual Hermes checkout")
    root = Path(root).resolve()
    install, audit, _ = fake_cli
    executable = install('print(json.dumps({"items": [{"id": "loader-example"}]}))')
    profile = tmp_path / "clean-profile"
    plugin_path = profile / "plugins/devgraph"
    shutil.copytree(PLUGIN, plugin_path, ignore=shutil.ignore_patterns("__pycache__"))
    skill = plugin_path / "skills/devgraph/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: devgraph\ndescription: Read the local work graph.\n---\n# Devgraph",
    )
    (profile / "config.yaml").write_text(
        "plugins:\n  enabled: [devgraph]\n  entries:\n    devgraph:\n"
        f"      cli_path: {json.dumps(str(executable))}\n      timeout_seconds: 3\n",
    )
    script = '''
import json
from hermes_constants import get_hermes_home
from hermes_cli.plugins import PluginManager
from tools.registry import registry
manager = PluginManager()
manifests = manager._scan_directory(get_hermes_home() / "plugins", "user")
assert [m.name for m in manifests] == ["devgraph"]
manager._load_plugin(manifests[0])
loaded = manager._plugins["devgraph"]
assert loaded.enabled and loaded.error is None, loaded.error
assert len(loaded.tools_registered) == 4
assert "devgraph:devgraph" in manager._plugin_skills
assert registry.get_entry("devgraph_read").check_fn()
result = json.loads(registry.dispatch("devgraph_read", {"operation": "arena"}))
assert result == {"ok": True, "data": {"items": [{"id": "loader-example"}]}}
print(json.dumps({"discovered": True, "tools": len(loaded.tools_registered),
                  "skill": True, "read": True}))
'''
    completed = subprocess.run(
        [str(root / ".venv/bin/python"), "-c", script], cwd=tmp_path,
        env={"HOME": str(tmp_path), "HERMES_HOME": str(profile),
             "PATH": "/usr/bin:/bin", "PYTHONPATH": str(root),
             "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["read"] is True
    assert len(audit.read_text().splitlines()) == 1
