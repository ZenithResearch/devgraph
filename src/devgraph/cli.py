"""Local operator CLI for the private Devgraph host.

The CLI deliberately does not invent a localhost authentication bypass.  It
can prove the loopback service posture, inspect the canonical ontology bundle,
manage the two user launch agents, and run bounded Work reads with one
owner-private read credential. Generic HTTP mutations remain closed; one fixed
local command invokes only the exact secS Issue-create receiver over owner-only
artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from devgraph.arena_requests import ARENA_OPERATIONS, InvalidArenaRequest
from devgraph.auth.secs_issue_create import SecSIssueCreateDenied
from devgraph.client import (
    DevgraphClientError,
    DevgraphHttpClient,
    DevgraphRequestContext,
)
from devgraph.events.outbox import IdempotencyScopeConflict
from devgraph.local_host import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_HOST_ROOT,
    DEFAULT_JAVA_HOME,
    DEFAULT_LAUNCH_AGENT_ROOT,
    DEFAULT_LOG_ROOT,
    LocalHostConfig,
    LocalHostError,
    apply_local_migrations,
    config_snapshot,
    configure_local_host,
    load_local_config,
    local_read_credential_status,
    provision_local_read_credential,
    read_local_read_credential,
)
from devgraph.model.repository import (
    WorkObjectAlreadyExistsError,
    WorkObjectRepositoryError,
)
from devgraph.model.validation import validate_work_object_id
from devgraph.ops.auth_setup import auth_status, provision_read
from devgraph.ops.local_process import neo4j_process_snapshot, wait_for_neo4j_exit
from devgraph.ops.named_work_agent import LocalNamedWorkAgentError, execute_local_named_work
from devgraph.ops.secs_issue_create_agent import (
    LocalAgentIssueCreateError,
    execute_local_agent_secs_issue_create_v1,
)
from devgraph.ops.secs_issue_create_receiver import (
    LocalSecSIssueCreateError,
    execute_local_secs_issue_create_v1,
)
from devgraph.ops.secs_issue_create_wallet import (
    LocalSecSWalletIssueCreateError,
    execute_local_wallet_secs_issue_create_v1,
)
from devgraph.ops.signer_profile import (
    SignerProfileError,
    configure_signer,
    create_signer,
    forget_signer,
    inspect_signer_key,
)
from devgraph.storage.base import StorageUnavailable
from devgraph.work_requests import WORK_OPERATIONS, InvalidWorkRequest

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
HOST_ROOT = DEFAULT_HOST_ROOT
LOG_ROOT = DEFAULT_LOG_ROOT
LAUNCH_AGENT_ROOT = DEFAULT_LAUNCH_AGENT_ROOT
ONTOLOGY_RELEASE_ROOT = Path(str(files("devgraph").joinpath("resources/ontology-releases")))
WORK_KINDS = ("Proposal", "Initiative", "Project", "Issue", "Task")

SERVICE_LABELS = {
    "neo4j": "ca.zenith.devgraph.neo4j",
    "api": "ca.zenith.devgraph.api",
}
SERVICE_LOGS = {
    "neo4j": {
        "stdout": LOG_ROOT / "launchd-neo4j.out.log",
        "stderr": LOG_ROOT / "launchd-neo4j.err.log",
    },
    "api": {
        "stdout": LOG_ROOT / "launchd-api.out.log",
        "stderr": LOG_ROOT / "launchd-api.err.log",
    },
}


class CliError(RuntimeError):
    """Safe operator-facing failure."""


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _safe_get(client: httpx.Client, url: str) -> tuple[int | None, dict[str, Any] | None]:
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return None, None
    try:
        body = response.json()
    except (json.JSONDecodeError, TypeError, ValueError):
        body = None
    return response.status_code, body if isinstance(body, dict) else None


def status_snapshot(
    *,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Return a bounded status snapshot without exposing response details."""

    owns_client = client is None
    # Readiness performs bounded migration and canonical-row inspection. A
    # freshly restarted external-volume database can legitimately need several
    # seconds for the first read without being unavailable.
    active_client = client or httpx.Client(timeout=10.0)
    try:
        live_status, live_body = _safe_get(active_client, f"{base_url.rstrip('/')}/live")
        ready_status, ready_body = _safe_get(active_client, f"{base_url.rstrip('/')}/ready")
        protected_status, _ = _safe_get(active_client, f"{base_url.rstrip('/')}/work/Issue")
    finally:
        if owns_client:
            active_client.close()

    live = live_status == 200 and live_body == {"live": True}
    ready = ready_status == 200 and bool(ready_body and ready_body.get("ready") is True)
    protected = protected_status == 401
    readiness = None
    if ready_body is not None:
        readiness = {
            key: ready_body.get(key)
            for key in (
                "ready",
                "reason",
                "current_applied_version",
                "minimum_schema_version",
                "maximum_schema_version",
            )
            if key in ready_body
        }
    return {
        "api": {
            "base_url": base_url.rstrip("/"),
            "live": live,
            "live_status": live_status,
            "protected_route_closed": protected,
            "protected_status": protected_status,
            "ready": ready,
            "ready_status": ready_status,
            "readiness": readiness,
        },
        "healthy": live and ready and protected,
    }


def _release_key(path: Path) -> tuple[int, ...]:
    match = re.fullmatch(r"v(\d+(?:\.\d+)*)", path.name)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def ontology_snapshot(release: str | None = None) -> dict[str, Any]:
    """Read the newest or requested committed ontology release manifest."""

    if release is None:
        candidates = [
            path for path in ONTOLOGY_RELEASE_ROOT.iterdir() if path.is_dir() and _release_key(path)
        ]
        if not candidates:
            raise CliError("no committed ontology release found")
        release_path = max(candidates, key=_release_key)
    else:
        normalized = release if release.startswith("v") else f"v{release}"
        if re.fullmatch(r"v\d+(?:\.\d+)*", normalized) is None:
            raise CliError("invalid ontology release")
        release_path = ONTOLOGY_RELEASE_ROOT / normalized
    manifest_path = release_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise CliError("ontology release manifest unavailable") from error
    if not isinstance(manifest, dict):
        raise CliError("ontology release manifest malformed")
    authority = manifest.get("authority")
    compatibility = manifest.get("compatibility")
    return {
        "authority": authority if isinstance(authority, dict) else None,
        "bundle_digest": manifest.get("bundle_digest"),
        "canonical_base_url": manifest.get("canonical_base_url"),
        "compatibility": compatibility if isinstance(compatibility, dict) else None,
        "discovery_url": manifest.get("discovery_url"),
        "manifest_path": str(manifest_path),
        "release": manifest.get("release"),
        "version": manifest.get("version"),
    }


def _launch_domain() -> str:
    return f"gui/{os.getuid()}"


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return subprocess.CompletedProcess(command, 127, "", "")


def launchd_snapshot(runner=_run) -> dict[str, Any]:
    """Return redacted launchd state for both private local services."""

    services: dict[str, Any] = {}
    for name, label in SERVICE_LABELS.items():
        result = runner(["launchctl", "print", f"{_launch_domain()}/{label}"])
        output = result.stdout if result.returncode == 0 else ""
        state = re.search(r"^\s*state = ([^\n]+)$", output, re.MULTILINE)
        pid = re.search(r"^\s*pid = (\d+)$", output, re.MULTILINE)
        services[name] = {
            "label": label,
            "loaded": result.returncode == 0,
            "pid": int(pid.group(1)) if pid else None,
            "state": state.group(1).strip() if state else "not_loaded",
        }
    return {"services": services}


def _service_order(action: str) -> list[str]:
    return ["api", "neo4j"] if action == "down" else ["neo4j", "api"]


def manage_services(
    action: str,
    runner=_run,
    *,
    launch_agent_root: Path = LAUNCH_AGENT_ROOT,
    services: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Start, stop, or restart the user launch agents."""

    if sys.platform != "darwin":
        raise CliError("local service management is available only on macOS")
    results: list[dict[str, Any]] = []
    service_order = list(services) if services is not None else _service_order(action)
    if not service_order or any(name not in SERVICE_LABELS for name in service_order):
        raise CliError("invalid service selection")
    for name in service_order:
        label = SERVICE_LABELS[name]
        target = f"{_launch_domain()}/{label}"
        if action == "up":
            probe = runner(["launchctl", "print", target])
            command = (
                ["launchctl", "kickstart", target]
                if probe.returncode == 0
                else [
                    "launchctl",
                    "bootstrap",
                    _launch_domain(),
                    str(launch_agent_root / f"{label}.plist"),
                ]
            )
        elif action == "down":
            probe = runner(["launchctl", "print", target])
            if probe.returncode != 0:
                results.append(
                    {"action": action, "ok": True, "service": name, "state": "not_loaded"}
                )
                continue
            command = ["launchctl", "bootout", target]
        elif action == "restart":
            # The agents are KeepAlive jobs. SIGTERM gives Neo4j and Uvicorn a
            # bounded graceful shutdown; launchd then starts a fresh process.
            command = ["launchctl", "kill", "SIGTERM", target]
        else:
            raise CliError("unsupported service action")
        result = runner(command)
        results.append({"action": action, "ok": result.returncode == 0, "service": name})
        if result.returncode != 0:
            break
    return {"results": results, "successful": all(item["ok"] for item in results)}


def _service_logs(log_root: Path) -> dict[str, dict[str, Path]]:
    return {
        name: {stream: log_root / path.name for stream, path in streams.items()}
        for name, streams in SERVICE_LOGS.items()
    }


def read_logs(
    service: str,
    stream: str,
    lines: int,
    *,
    log_root: Path = LOG_ROOT,
) -> dict[str, Any]:
    if lines < 1 or lines > 500:
        raise CliError("log line count must be between 1 and 500")
    path = _service_logs(log_root)[service][stream]
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError as error:
        raise CliError("service log unavailable") from error
    return {"lines": content[-lines:], "path": str(path), "service": service, "stream": stream}


def _optional_local_config(config_path: Path) -> LocalHostConfig | None:
    return load_local_config(config_path, required=False)


def query_work_snapshot(
    *,
    kind: str,
    work_id: str | None,
    include_archived: bool,
    descending: bool = False,
    after_id: str | None = None,
    limit: int = 50,
    relationship: str | None = None,
    after_resource: str | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    transport=None,
) -> dict[str, Any]:
    """Execute one bounded read through the public Work API client."""

    if relationship not in (
        None,
        "children",
        "parent",
        "dependencies",
        "dependents",
        "blockers",
        "blocked",
    ):
        raise CliError("unsupported Work relationship read")
    if relationship is not None and work_id is None:
        raise CliError("relationship reads require a Work id")
    if relationship in {"blockers", "blocked"} and kind != "Task":
        raise CliError("blockers are defined only for Task")
    if work_id is not None and (
        include_archived or descending or after_id or (relationship is None and limit != 50)
    ):
        raise CliError("list options require a list operation")
    try:
        if work_id is not None:
            validate_work_object_id(work_id)
        if after_id is not None:
            validate_work_object_id(after_id)
    except ValueError:
        raise CliError("Work ids must be canonical lowercase identifiers") from None
    config = load_local_config(config_path)
    assert config is not None
    credential = read_local_read_credential(config)
    owns_transport = transport is None
    active_transport = transport or httpx.Client(trust_env=False)
    try:
        client = DevgraphHttpClient(
            transport=active_transport,
            base_url=DEFAULT_BASE_URL,
            timeout=10.0,
        )
        context = DevgraphRequestContext(credential=credential)
        if relationship is not None:
            if (
                relationship not in {"children", "blockers"}
                or after_resource is not None
                or limit != 50
            ):
                try:
                    collection = client.get_work_relationships(
                        context,
                        kind=kind,
                        work_id=work_id,
                        relationship=relationship,
                        after_resource=after_resource,
                        limit=limit,
                    )
                except ValueError:
                    raise CliError("invalid Work relationship page") from None
                return {"items": [item.model_dump(mode="json") for item in collection.items]}
            collection = (
                client.get_work_children(context, kind=kind, work_id=work_id)
                if relationship == "children"
                else client.get_task_blockers(context, task_id=work_id)
            )
            return {"items": [item.model_dump(mode="json") for item in collection.items]}
        if work_id is not None:
            result = client.get_work(
                context,
                kind=kind,
                work_id=work_id,
            )
            return {"item": result.model_dump(mode="json")}
        result = client.list_work(
            context,
            kind=kind,
            include_archived=include_archived,
            descending=descending,
            after_id=after_id,
            limit=limit,
        )
        return {"items": [item.model_dump(mode="json") for item in result.items]}
    finally:
        if owns_transport:
            active_transport.close()


def local_status_snapshot(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    config = _optional_local_config(config_path)
    api = status_snapshot(base_url=base_url)
    configuration = config_snapshot(config, config_path=config_path) if config is not None else None
    launchd = launchd_snapshot()
    neo4j_process = neo4j_process_snapshot(config) if config is not None else None
    if neo4j_process is not None:
        neo4j_process["managed"] = (
            launchd.get("services", {}).get("neo4j", {}).get("loaded") is True
        )
    runtime = configuration.get("runtime") if configuration is not None else None
    services = launchd.get("services")
    runtime_ready = (
        isinstance(runtime, dict)
        and bool(runtime)
        and all(available is True for available in runtime.values())
    )
    services_loaded = (
        isinstance(services, dict)
        and set(services) == set(SERVICE_LABELS)
        and all(
            isinstance(service, dict) and service.get("loaded") is True
            for service in services.values()
        )
    )
    return {
        "api": api,
        "configuration": configuration,
        "configured": config is not None,
        "healthy": (
            config is not None
            and api.get("healthy") is True
            and runtime_ready
            and services_loaded
            and neo4j_process is not None
            and neo4j_process["state"] == "running"
        ),
        "launchd": launchd,
        "neo4j_process": neo4j_process,
    }


def start_local_services(config: LocalHostConfig) -> dict[str, Any]:
    """Start storage, apply migrations, then admit the API launch agent."""

    process = neo4j_process_snapshot(config)
    if process["state"] == "unknown":
        return {"successful": False, "reason": process["reason"], "neo4j_process": process}
    if process["state"] == "running":
        managed = launchd_snapshot().get("services", {}).get("neo4j", {}).get("loaded")
        if managed is not True:
            return {
                "successful": False,
                "reason": "unmanaged_neo4j_process",
                "neo4j_process": process,
            }
    prior_api = manage_services(
        "down",
        launch_agent_root=config.launch_agent_root,
        services=("api",),
    )
    if not prior_api["successful"]:
        return {
            "api": None,
            "migration": None,
            "neo4j": None,
            "prior_api": prior_api,
            "successful": False,
        }
    neo4j = manage_services(
        "up",
        launch_agent_root=config.launch_agent_root,
        services=("neo4j",),
    )
    if not neo4j["successful"]:
        return {
            "api": None,
            "migration": None,
            "neo4j": neo4j,
            "prior_api": prior_api,
            "successful": False,
        }
    migration = apply_local_migrations(config)
    if migration.get("ready") is not True:
        return {
            "api": None,
            "migration": migration,
            "neo4j": neo4j,
            "prior_api": prior_api,
            "successful": False,
        }
    api = manage_services(
        "up",
        launch_agent_root=config.launch_agent_root,
        services=("api",),
    )
    return {
        "api": api,
        "migration": migration,
        "neo4j": neo4j,
        "prior_api": prior_api,
        "successful": api["successful"] and bool(migration.get("ready")),
    }


def stop_local_services(config: LocalHostConfig) -> dict[str, Any]:
    """Unload both jobs and prove the tracked database process exited."""
    import time

    started = time.monotonic()
    initial = neo4j_process_snapshot(config)
    services = manage_services(
        "down",
        launch_agent_root=config.launch_agent_root,
    )
    result = {
        "services": services,
        "initial_neo4j_process": initial,
        "neo4j_process": None,
        "jobs_unloaded": False,
        "successful": False,
    }
    if services["successful"] and initial["state"] != "unknown":
        process = wait_for_neo4j_exit(config, tracked_pid=initial["pid"])
        result["neo4j_process"] = process
        if process["successful"]:
            jobs = launchd_snapshot()["services"]
            result["jobs_unloaded"] = all(
                jobs.get(name, {}).get("loaded") is False for name in ("api", "neo4j")
            )
            result["successful"] = result["jobs_unloaded"]
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result


def restart_local_services(config: LocalHostConfig) -> dict[str, Any]:
    """Prove shutdown before repeating the migration-gated start sequence."""

    stopped = stop_local_services(config)
    if not stopped["successful"]:
        return {"start": None, "stop": stopped, "successful": False}
    started = start_local_services(config)
    return {"start": started, "stop": stopped, "successful": started["successful"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devgraph",
        description="Operate the private local Devgraph host",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    auth = subcommands.add_parser("auth", help="configure an existing signer and local credentials")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    key = auth_commands.add_parser(
        "key", help="create a new Wallet identity or inspect its public key"
    )
    key_commands = key.add_subparsers(dest="auth_key_command", required=True)
    create_key = key_commands.add_parser("create", help="create and configure a new Dregg identity")
    create_key.add_argument(
        "--key-file",
        type=Path,
        help="new file in an existing private directory; defaults to Wallet custody",
    )
    inspect_key = key_commands.add_parser(
        "inspect", help="ask Wallet for an existing key's public pin"
    )
    inspect_key.add_argument("--key-file", type=Path, required=True)
    setup = auth_commands.add_parser(
        "setup", help="verify and save an existing Dregg key reference"
    )
    setup.add_argument("--key-file", type=Path, required=True)
    setup.add_argument(
        "--public-key", required=True, help="expected 64-character lowercase public hex"
    )
    setup.add_argument("--replace", action="store_true", help="replace the saved reference")
    auth_check = auth_commands.add_parser(
        "status", help="inspect signer, read credential and grant bundles"
    )
    auth_check.add_argument(
        "--check", action="store_true", help="ask Wallet to verify the selected key and pin"
    )
    auth_check.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    auth_commands.add_parser(
        "forget", help="remove the saved signer reference; retain the key and grants"
    )
    auth_read = auth_commands.add_parser(
        "read", help="provision or rotate the local read-only credential"
    )
    read_commands = auth_read.add_subparsers(dest="auth_read_command", required=True)
    for action in ("provision", "rotate"):
        read = read_commands.add_parser(action)
        read.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
        read.add_argument("--actor-id", default="local-devgraph-operator")
        read.add_argument("--ttl-hours", type=int, default=24 * 30)

    auth_work = auth_commands.add_parser(
        "work", help="plan and manage the local signer's named Work grants"
    )
    grant_commands = auth_work.add_subparsers(dest="auth_work_command", required=True)
    plan = grant_commands.add_parser("plan", help="prepare a public, bounded Work grant plan")
    plan.add_argument("--ttl-hours", type=int, default=24 * 30)
    plan.add_argument("--output-file", type=Path)
    plan.add_argument("--renew", action="store_true", help="plan a change to existing authority")
    plan.add_argument(
        "--include-arenas",
        action="store_true",
        default=None,
        help="explicitly add Arena and membership permissions",
    )
    apply = grant_commands.add_parser("apply", help="activate a reviewed private plan file")
    apply.add_argument("--plan-file", type=Path, required=True)
    grant_commands.add_parser("status", help="verify current producer and receiver authority")
    grant_commands.add_parser("revoke", help="revoke current named Work authority")
    for action in ("renew", "rotate-verifier"):
        command = grant_commands.add_parser(action)
        command.add_argument("--ttl-hours", type=int, default=24 * 30)
        if action == "renew":
            command.add_argument("--include-arenas", action="store_true", default=None)

    status = subcommands.add_parser("status", help="prove API and authorization posture")
    status.add_argument("--base-url", default=DEFAULT_BASE_URL)

    ontology = subcommands.add_parser("ontology", help="inspect a canonical ontology release")
    ontology.add_argument("--release")

    service = subcommands.add_parser("service", help="inspect or manage local services")
    service.add_argument("action", choices=("status", "up", "down", "restart"))

    logs = subcommands.add_parser("logs", help="read a bounded local service log tail")
    logs.add_argument("service", choices=tuple(SERVICE_LOGS))
    logs.add_argument("--stream", choices=("stdout", "stderr"), default="stderr")
    logs.add_argument("--lines", type=int, default=50)

    query = subcommands.add_parser(
        "query",
        help="run bounded read-only Devgraph queries with the local credential",
    )
    query_commands = query.add_subparsers(dest="query_command", required=True)
    query_cypher = query_commands.add_parser(
        "cypher", help="execute the bounded read-only Work Cypher language"
    )
    query_cypher.add_argument("--request-file", type=Path, required=True)
    query_cypher.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    query_work = query_commands.add_parser("work", help="get or list canonical work")
    query_work.add_argument("kind", choices=WORK_KINDS)
    query_work.add_argument("work_id", nargs="?")
    query_work.add_argument("--include-archived", action="store_true")
    query_work.add_argument("--descending", action="store_true")
    query_work.add_argument("--after-id")
    query_work.add_argument(
        "--limit", type=int, choices=range(1, 101), metavar="1..100", default=50
    )
    query_work.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    arena_read = query_commands.add_parser("arena", help="get or list Arenas")
    arena_read.add_argument("arena_id", nargs="?")
    arena_read.add_argument("--include-archived", action="store_true")
    arena_read.add_argument("--after-id")
    arena_members = query_commands.add_parser("arena-members", help="read direct Arena members")
    arena_members.add_argument("arena_id")
    arena_members.add_argument("--after-resource")
    for read_parser in (arena_read, arena_members):
        read_parser.add_argument(
            "--limit", type=int, choices=range(1, 101), default=50, metavar="1..100"
        )
        read_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    arena_of = query_commands.add_parser("arena-of", help="resolve a Work root's Arena")
    arena_of.add_argument("kind", choices=WORK_KINDS)
    arena_of.add_argument("work_id")
    arena_of.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    query_children = query_commands.add_parser("children", help="read Work children")
    query_children.add_argument("kind", choices=WORK_KINDS)
    query_children.add_argument("work_id")
    query_children.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    query_blockers = query_commands.add_parser("blockers", help="read Task blockers")
    query_blockers.add_argument("work_id")
    query_blockers.set_defaults(kind="Task")
    query_blockers.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    for relationship in ("parent", "dependencies", "dependents", "blocked"):
        command = query_commands.add_parser(relationship, help=f"read Work {relationship}")
        if relationship == "blocked":
            command.set_defaults(kind="Task")
        else:
            command.add_argument("kind", choices=WORK_KINDS)
        command.add_argument("work_id")
        command.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
        command.add_argument("--after-resource")
        command.add_argument(
            "--limit", type=int, choices=range(1, 101), metavar="1..100", default=50
        )
    for command in (query_children, query_blockers):
        command.add_argument("--after-resource")
        command.add_argument(
            "--limit", type=int, choices=range(1, 101), metavar="1..100", default=50
        )

    work = subcommands.add_parser("work", help="execute signed named Work mutations")
    work_commands = work.add_subparsers(dest="work_operation", required=True)
    for operation in WORK_OPERATIONS:
        command = work_commands.add_parser(operation, help=f"sign and execute {operation}")
        command.add_argument("--request-file", type=Path, required=True)
        command.add_argument("--idempotency-key-file", type=Path, required=True)

    arena = subcommands.add_parser("arena", help="execute signed Arena mutations")
    arena_commands = arena.add_subparsers(dest="arena_operation", required=True)
    for operation in ARENA_OPERATIONS:
        command = arena_commands.add_parser(operation, help=f"sign and execute Arena {operation}")
        command.add_argument("--request-file", type=Path, required=True)
        command.add_argument("--idempotency-key-file", type=Path, required=True)

    secs_issue_create = subcommands.add_parser(
        "secs-issue-create-v1",
        help="execute one exact local secS-authorized Issue create",
    )
    secs_issue_create.add_argument("--request-file", type=Path, required=True)
    secs_issue_create.add_argument(
        "--signed-projection-file",
        type=Path,
        required=True,
    )
    secs_issue_create.add_argument(
        "--idempotency-key-file",
        type=Path,
        required=True,
    )

    wallet_issue_create = subcommands.add_parser(
        "wallet-issue-create-v1",
        help="approve and execute one exact local secS-authorized Issue create",
    )
    wallet_issue_create.add_argument("--request-file", type=Path, required=True)
    wallet_issue_create.add_argument(
        "--idempotency-key-file",
        type=Path,
        required=True,
    )

    agent_issue_create = subcommands.add_parser(
        "agent-issue-create-v1",
        help="sign and execute one secS-authorized Issue with an existing Dregg identity",
    )
    agent_issue_create.add_argument("--request-file", type=Path, required=True)
    agent_issue_create.add_argument("--idempotency-key-file", type=Path, required=True)

    local = subcommands.add_parser(
        "local",
        help="configure and operate a private local installation",
    )
    local_commands = local.add_subparsers(dest="local_command", required=True)
    recovery = local_commands.add_parser(
        "recovery", help="create and verify private recovery sets on both local disks"
    )
    recovery.add_argument("recovery_action", choices=("create", "status", "verify"))
    maintenance = local_commands.add_parser(
        "maintenance", help="back up, retain, and monitor the configured private host"
    )
    maintenance.add_argument(
        "maintenance_action", choices=("backup", "monitor", "status", "install")
    )

    configure = local_commands.add_parser(
        "configure",
        help="provision an existing directory and install user launch agents",
    )
    configure.add_argument("--data-root", type=Path, required=True)
    configure.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    configure.add_argument("--host-root", type=Path, default=DEFAULT_HOST_ROOT)
    configure.add_argument("--log-root", type=Path, default=DEFAULT_LOG_ROOT)
    configure.add_argument(
        "--launch-agent-root",
        type=Path,
        default=DEFAULT_LAUNCH_AGENT_ROOT,
    )
    configure.add_argument("--neo4j-home", type=Path)
    configure.add_argument("--java-home", type=Path, default=DEFAULT_JAVA_HOME)
    configure.add_argument("--python-executable", type=Path)
    configure.add_argument("--require-mounted-volume", action="store_true")
    configure.add_argument("--replace", action="store_true")

    local_config = local_commands.add_parser(
        "config",
        help="show the credential-free persisted local configuration",
    )
    local_config.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    local_status = local_commands.add_parser(
        "status",
        help="show configuration, launchd, and API posture",
    )
    local_status.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    local_status.add_argument("--base-url", default=DEFAULT_BASE_URL)

    read_credential = local_commands.add_parser(
        "read-credential",
        help="inspect, reveal, or rotate the local devgraph.read capability",
    )
    read_credential_commands = read_credential.add_subparsers(
        dest="read_credential_command",
        required=True,
    )
    for action in ("status", "show"):
        read_action = read_credential_commands.add_parser(action)
        read_action.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    rotate_read = read_credential_commands.add_parser("rotate")
    rotate_read.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    rotate_read.add_argument("--actor-id", default="local-devgraph-operator")
    rotate_read.add_argument("--ttl-hours", type=int, default=24 * 30)

    for action in ("start", "stop", "restart"):
        local_action = local_commands.add_parser(action, help=f"{action} local services")
        local_action.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "auth":
            if args.auth_command == "work":
                from devgraph.ops import work_grants

                try:
                    if args.auth_work_command == "plan":
                        result = work_grants.plan_grant(
                            ttl_hours=args.ttl_hours,
                            renew=args.renew,
                            include_arenas=args.include_arenas,
                        )
                        if args.output_file is not None:
                            work_grants.write_plan(result, args.output_file)
                    elif args.auth_work_command == "apply":
                        result = work_grants.apply_grant(plan_file=args.plan_file)
                    elif args.auth_work_command == "renew":
                        result = work_grants.renew_grant(
                            ttl_hours=args.ttl_hours, include_arenas=args.include_arenas
                        )
                    elif args.auth_work_command == "rotate-verifier":
                        result = work_grants.rotate_verifier(ttl_hours=args.ttl_hours)
                    elif args.auth_work_command == "revoke":
                        result = work_grants.revoke_grant()
                    else:
                        result = work_grants.grant_status()
                except work_grants.WorkGrantError as error:
                    print(_json({"error": str(error)}), file=sys.stderr)
                    return 2
                print(_json(result))
                if args.auth_work_command == "plan":
                    return 0
                if args.auth_work_command == "revoke":
                    return (
                        0
                        if (
                            result.get("receiver_revoked") is True
                            and result.get("producer_revoked") is True
                        )
                        else 2
                    )
                return 0 if result.get("ready") is True else 2
            if args.auth_command == "key":
                if args.auth_key_command == "create":
                    result = create_signer(key_file=args.key_file)
                    print(_json(result))
                    return 0 if result["configured"] else 2
                print(_json(inspect_signer_key(key_file=args.key_file)))
                return 0
            if args.auth_command == "setup":
                result = configure_signer(
                    key_file=args.key_file, public_key=args.public_key, replace=args.replace
                )
            elif args.auth_command == "forget":
                result = forget_signer()
            elif args.auth_command == "read":
                result = provision_read(
                    config_path=args.config,
                    actor_id=args.actor_id,
                    ttl_hours=args.ttl_hours,
                    replace=args.auth_read_command == "rotate",
                )
            else:
                result = auth_status(config_path=args.config, check=args.check)
                print(_json(result))
                if args.check:
                    return 0 if result["signer"]["identity_verified"] is True else 1
                return 0
            print(_json(result))
            return 0
        if args.command == "status":
            result = status_snapshot(base_url=args.base_url)
            print(_json(result))
            return 0 if result["healthy"] else 1
        if args.command == "ontology":
            print(_json(ontology_snapshot(args.release)))
            return 0
        if args.command == "service":
            result = launchd_snapshot() if args.action == "status" else manage_services(args.action)
            print(_json(result))
            if args.action == "status":
                return 0 if all(item["loaded"] for item in result["services"].values()) else 1
            return 0 if result["successful"] else 1
        if args.command == "logs":
            config = _optional_local_config(DEFAULT_CONFIG_PATH)
            log_root = config.log_root if config is not None else LOG_ROOT
            print(_json(read_logs(args.service, args.stream, args.lines, log_root=log_root)))
            return 0
        if args.command == "query":
            if args.query_command in ("arena", "arena-members", "arena-of"):
                from devgraph.ops.arena_client import query_arena_snapshot

                print(
                    _json(
                        query_arena_snapshot(
                            operation=args.query_command,
                            arena_id=getattr(args, "arena_id", None),
                            kind=getattr(args, "kind", None),
                            work_id=getattr(args, "work_id", None),
                            include_archived=getattr(args, "include_archived", False),
                            after_id=getattr(args, "after_id", None),
                            after_resource=getattr(args, "after_resource", None),
                            limit=getattr(args, "limit", 50),
                            config_path=args.config,
                        )
                    )
                )
                return 0
            if args.query_command == "cypher":
                from devgraph.cypher_read import CypherReadError
                from devgraph.ops.cypher_client import query_cypher_snapshot

                try:
                    result = query_cypher_snapshot(
                        request_file=args.request_file, config_path=args.config
                    )
                except CypherReadError as error:
                    print(_json({"error": error.code}), file=sys.stderr)
                    return 2
                print(_json(result))
                return 0
            print(
                _json(
                    query_work_snapshot(
                        kind=args.kind,
                        work_id=args.work_id,
                        include_archived=getattr(args, "include_archived", False),
                        descending=getattr(args, "descending", False),
                        after_id=getattr(args, "after_id", None),
                        after_resource=getattr(args, "after_resource", None),
                        limit=getattr(args, "limit", 50),
                        relationship=(None if args.query_command == "work" else args.query_command),
                        config_path=args.config,
                    )
                )
            )
            return 0
        if args.command == "arena":
            print(
                _json(
                    execute_local_named_work(
                        request_file=args.request_file,
                        idempotency_key_file=args.idempotency_key_file,
                        operation=args.arena_operation,
                        request_domain="arena",
                    )
                )
            )
            return 0
        if args.command == "work":
            print(
                _json(
                    execute_local_named_work(
                        request_file=args.request_file,
                        idempotency_key_file=args.idempotency_key_file,
                        operation=args.work_operation,
                    )
                )
            )
            return 0
        if args.command == "secs-issue-create-v1":
            print(
                _json(
                    execute_local_secs_issue_create_v1(
                        request_file=args.request_file,
                        signed_projection_file=args.signed_projection_file,
                        idempotency_key_file=args.idempotency_key_file,
                    )
                )
            )
            return 0
        if args.command == "wallet-issue-create-v1":
            print(
                _json(
                    execute_local_wallet_secs_issue_create_v1(
                        request_file=args.request_file,
                        idempotency_key_file=args.idempotency_key_file,
                    )
                )
            )
            return 0
        if args.command == "agent-issue-create-v1":
            print(
                _json(
                    execute_local_agent_secs_issue_create_v1(
                        request_file=args.request_file,
                        idempotency_key_file=args.idempotency_key_file,
                    )
                )
            )
            return 0
        if args.command == "local":
            if args.local_command == "recovery":
                from devgraph.ops.local_recovery import run_recovery

                try:
                    result = run_recovery(args.recovery_action)
                except Exception:
                    print(_json({"error": "local_recovery_failed"}), file=sys.stderr)
                    return 2
                print(_json(result))
                return 0 if result.get("successful", result.get("healthy", True)) else 1
            if args.local_command == "maintenance":
                from devgraph.ops.local_maintenance import run_maintenance

                try:
                    result = run_maintenance(args.maintenance_action)
                except Exception:
                    print(_json({"error": "local_maintenance_failed"}), file=sys.stderr)
                    return 2
                print(_json(result))
                return 0 if result.get("successful", result.get("healthy", True)) else 1
            if args.local_command == "configure":
                config = LocalHostConfig.build(
                    data_root=args.data_root,
                    host_root=args.host_root,
                    log_root=args.log_root,
                    launch_agent_root=args.launch_agent_root,
                    neo4j_home=args.neo4j_home,
                    java_home=args.java_home,
                    python_executable=args.python_executable,
                    require_mounted_volume=args.require_mounted_volume,
                )
                print(
                    _json(
                        configure_local_host(
                            config,
                            config_path=args.config,
                            replace=args.replace,
                        )
                    )
                )
                return 0
            if args.local_command == "config":
                config = load_local_config(args.config)
                assert config is not None
                print(_json(config_snapshot(config, config_path=args.config)))
                return 0
            if args.local_command == "status":
                result = local_status_snapshot(
                    config_path=args.config,
                    base_url=args.base_url,
                )
                print(_json(result))
                return 0 if result["healthy"] else 1
            if args.local_command == "read-credential":
                config = load_local_config(args.config)
                assert config is not None
                if args.read_credential_command == "show":
                    print(read_local_read_credential(config))
                    return 0
                if args.read_credential_command == "rotate":
                    state = provision_local_read_credential(
                        config,
                        actor_id=args.actor_id,
                        ttl_seconds=args.ttl_hours * 60 * 60,
                        replace=True,
                    )
                    result = local_read_credential_status(config)
                    result["state"] = state
                    print(_json(result))
                    return 0
                print(_json(local_read_credential_status(config)))
                return 0
            config = _optional_local_config(args.config)
            launch_agent_root = (
                config.launch_agent_root if config is not None else LAUNCH_AGENT_ROOT
            )
            if args.local_command == "start" and config is not None:
                result = start_local_services(config)
                print(_json(result))
                return 0 if result["successful"] else 1
            if args.local_command == "restart" and config is not None:
                result = restart_local_services(config)
                print(_json(result))
                return 0 if result["successful"] else 1
            if args.local_command == "stop" and config is not None:
                result = stop_local_services(config)
                print(_json(result))
                return 0 if result["successful"] else 1
            action = {
                "start": "up",
                "stop": "down",
                "restart": "restart",
            }[args.local_command]
            result = manage_services(action, launch_agent_root=launch_agent_root)
            print(_json(result))
            return 0 if result["successful"] else 1
    except SecSIssueCreateDenied as error:
        print(_json({"error": error.reason}), file=sys.stderr)
        return 2
    except IdempotencyScopeConflict:
        print(_json({"error": "idempotency_scope_conflict"}), file=sys.stderr)
        return 2
    except WorkObjectAlreadyExistsError:
        print(_json({"error": "work_object_already_exists"}), file=sys.stderr)
        return 2
    except WorkObjectRepositoryError:
        print(_json({"error": "work_object_repository_error"}), file=sys.stderr)
        return 2
    except StorageUnavailable:
        print(_json({"error": "canonical_storage_unavailable"}), file=sys.stderr)
        return 2
    except (
        CliError,
        DevgraphClientError,
        LocalHostError,
        LocalSecSIssueCreateError,
        LocalSecSWalletIssueCreateError,
        LocalAgentIssueCreateError,
        LocalNamedWorkAgentError,
        SignerProfileError,
        InvalidWorkRequest,
        InvalidArenaRequest,
    ) as error:
        print(_json({"error": str(error)}), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
