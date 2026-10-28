"""Authenticated loopback source-session reader, independent of graph migrations.

The existing owner-local read capability admits reads only. This service exposes
metadata observations; it cannot import graph nodes or mutate Work.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from devgraph.auth.local_read import LocalReadCredentialVerifier
from devgraph.observability.session_associations import read_inspections
from devgraph.observability.session_capture import SessionJournal
from devgraph.observability.session_ref import SessionIdentity, work_ref
from devgraph.observability.session_sources import NativeSources, hook_evidence, owned, row

REF = re.compile(r"sr-[0-9a-f]{64}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SessionReader:
    def __init__(self, config, *, work_reader=None, graph_reader=None):
        self.config = config
        self.sources = NativeSources(config["sources"])
        self.lock = threading.Lock()
        self.scanned = 0.0
        self.value = {"items": [], "coverage": []}
        self.work_reader = work_reader or self.read_work
        self.graph_reader = graph_reader or self.read_graph

    def read_work(self, key):
        work_ref(key)
        value = self.graph_reader("/work/" + urllib.parse.quote(key, safe="/"))
        if value is None:
            return None
        try:
            if type(value["version"]) is not int or value["version"] < 1:
                return None
            return {"key": key, "title": value["title"], "version": value["version"]}
        except (KeyError, TypeError):
            return None

    def read_graph(self, path):
        if not path.startswith(("/work/", "/session-refs/v1")):
            raise ValueError("unsupported_graph_read")
        token_path = Path(self.config["data_root"]) / "devgraph/credentials/devgraph.read"
        owned(token_path, private=True)
        token = token_path.read_text().strip()
        request = urllib.request.Request(
            "http://127.0.0.1:8080" + path,
            headers={"Authorization": "Bearer " + token},
            method="GET",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=3) as response:
                raw = response.read(262_145)
            if len(raw) > 262_144:
                return None
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PermissionError("work_access_denied") from None
            return None
        except (OSError, ValueError, KeyError):
            return None

    def refresh(self, *, force=False):
        with self.lock:
            if not force and time.monotonic() - self.scanned < 15:
                return
            items, coverage = self.sources.scan()
            index = {r["id"]: r for r in items}
            canonical = {}
            graph_status = {"status": "not_connected", "bounded": True}
            if self.config.get("canonical_reads"):
                after = None
                for _ in range(12):
                    path = "/session-refs/v1?limit=100"
                    if after:
                        path += "&after=" + after
                    page = self.graph_reader(path)
                    if not page or page.get("schema") != "devgraph.session-refs.v1":
                        graph_status = {"status": "unavailable", "bounded": True}
                        break
                    canonical.update((r["id"], r) for r in page["items"])
                    after = page.get("next_after")
                    graph_status = {
                        "status": "available",
                        "bounded": bool(after),
                        "loaded": len(canonical),
                    }
                    if not after:
                        break
                for item in items:
                    item["work_state"] = canonical.get(item["id"], {}).get("work_state")
                    item["canonical"] = (
                        True
                        if item["id"] in canonical
                        else None
                        if graph_status["bounded"]
                        else False
                    )
            if self.config.get("observer_journal"):
                journal = SessionJournal(Path(self.config["observer_journal"]))
                try:
                    for item in items:
                        for link in item["links"]:
                            if link.get("basis") != "hermes_model_config.codex_thread_id":
                                continue
                            origin, target = (
                                SessionIdentity(**link[k]) for k in ("origin", "target")
                            )
                            event = (
                                "binding-"
                                + hashlib.sha256((origin.id + ":" + target.id).encode()).hexdigest()
                            )
                            link["witness"] = journal.observe_link(
                                origin,
                                target,
                                relation="uses_runtime",
                                source_instance_id=origin.source_instance_id,
                                event_id=event,
                            )
                finally:
                    journal.close()
            inspection_path = self.config.get("inspection_file")
            if inspection_path:
                try:
                    for binding in read_inspections(Path(inspection_path)):
                        item = index.get(binding["ref_id"])
                        if item:
                            item["work_refs"].append(
                                {k: v for k, v in binding.items() if k != "ref_id"}
                            )
                            if binding["observed_at"] > (item["last_activity_at"] or ""):
                                item["native_last_activity_at"] = item["last_activity_at"]
                                item["last_activity_at"] = binding["observed_at"]
                                item["activity_basis"] = "observed_work_inspection"
                except (OSError, ValueError, sqlite3.Error):
                    coverage.append({"label": "CLI Work inspections", "status": "unavailable"})
            for source in self.config["sources"]:
                if source["application"] != "hermes":
                    continue
                try:
                    events = hook_evidence(source)
                    last_hooks = set()
                    for event in events:
                        identity = SessionIdentity("hermes", source["id"], event["session_id"])
                        item = index.get(identity.id)
                        if not item:
                            continue
                        if identity.id not in last_hooks:
                            item["last_observer_event_at"] = event["observed_at"]
                            item["last_observer_hook"] = event["hook"]
                            last_hooks.add(identity.id)
                        if event.get("work_ref"):
                            binding = {
                                "key": event["work_ref"],
                                "basis": event["basis"],
                                "observed_at": event["observed_at"],
                            }
                            if binding not in item["work_refs"]:
                                item["work_refs"].append(binding)
                        if event.get("parent_session_id"):
                            parent = SessionIdentity(
                                "hermes", source["id"], event["parent_session_id"]
                            )
                            link = {
                                "relation": "delegated_from",
                                "id": parent.id,
                                "basis": "hermes_lifecycle_hook",
                            }
                            if link not in item["links"]:
                                item["links"].append(link)
                        if event.get("native_application") and event.get("native_id"):
                            candidates = [
                                s
                                for s in self.config["sources"]
                                if s["application"] == event["native_application"]
                            ]
                            if len(candidates) == 1:
                                native = SessionIdentity(
                                    event["native_application"],
                                    candidates[0]["id"],
                                    event["native_id"],
                                )
                                target = index.get(native.id)
                                if target:
                                    link = {
                                        "relation": "delegated_to",
                                        "id": native.id,
                                        "basis": "typed_cli_identity_event",
                                    }
                                    if link not in item["links"]:
                                        item["links"].append(link)
                                    target["links"].append(
                                        {
                                            "relation": "delegated_from",
                                            "id": item["id"],
                                            "basis": "typed_cli_identity_event",
                                        }
                                    )
                    for entry in coverage:
                        if entry["source_instance_id"] == source["id"]:
                            entry["observer"] = (
                                "installed"
                                if (
                                    Path(source["root"]) / "plugins/devgraph-observer/plugin.yaml"
                                ).exists()
                                else "not_installed"
                            )
                            entry["hook_events_loaded"] = len(events)
                except (OSError, ValueError, sqlite3.Error, KeyError):
                    coverage.append(
                        {
                            "source_instance_id": source["id"],
                            "label": source["label"] + " hooks",
                            "status": "unavailable",
                        }
                    )
            # Wrapper journals have exact dispatch/native identities and explicit
            # declared Work scope. They never imply successful Work mutation.
            for directory in self.config.get("journals", []):
                try:
                    journal = SessionJournal(Path(directory), read_only=True)
                    try:
                        native_rows = journal.db.execute(
                            "SELECT payload FROM events WHERE event_key LIKE '%:native' "
                            "ORDER BY sequence DESC LIMIT 200"
                        ).fetchall()
                        for (raw,) in native_rows:
                            native = json.loads(raw)
                            attempt = journal.attempt(native["attempt_id"])
                            identity = SessionIdentity(**native["identity"])
                            item = index.get(identity.id)
                            if not item:
                                source = next(
                                    (
                                        s
                                        for s in self.config["sources"]
                                        if s["id"] == identity.source_instance_id
                                        and s["application"] == identity.application
                                    ),
                                    None,
                                )
                                if source is None:
                                    continue
                                item = row(source, identity.external_session_id)
                                item["record_available"] = False
                                item["last_activity_at"] = attempt["observed_at"]
                                item["activity_basis"] = "captured_native_identity"
                                items.append(item)
                                index[item["id"]] = item
                            dispatch = attempt["dispatch"]
                            for key in dispatch["work_refs"]:
                                binding = {
                                    "key": key,
                                    "basis": "declared_dispatch_scope",
                                    "observed_at": attempt["observed_at"],
                                }
                                if binding not in item["work_refs"]:
                                    item["work_refs"].append(binding)
                            if dispatch["parent"]:
                                parent = SessionIdentity(**dispatch["parent"])
                                item["links"].append(
                                    {
                                        "id": parent.id,
                                        "relation": "delegated_from",
                                        "basis": "captured_dispatch",
                                        "attempt_id": native["attempt_id"],
                                    }
                                )
                    finally:
                        journal.close()
                except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                    coverage.append({"label": "Delegation journal", "status": "unavailable"})
            self.value = {
                "schema": "devgraph.session-source-page.v1",
                "read_at": datetime.now(timezone.utc).isoformat(),
                "items": sorted(
                    items, key=lambda r: (r["last_activity_at"] or "", r["id"]), reverse=True
                ),
                "coverage": coverage,
                "canonical_graph_imported": any(r.get("canonical") is True for r in items),
                "canonical_graph": graph_status,
            }
            self.scanned = time.monotonic()

    def snapshot(self, *, after=None, limit=100):
        self.refresh()
        with self.lock:
            value = copy.deepcopy(self.value)
        rows = value["items"]
        start = 0
        if after:
            position = next((i for i, r in enumerate(rows) if r["id"] == after), None)
            if position is None:
                raise ValueError("cursor_changed")
            start = position + 1
        value["total_loaded"] = len(rows)
        value["items"] = rows[start : start + limit]
        value["next_after"] = value["items"][-1]["id"] if start + limit < len(rows) else None
        return value

    def detail(self, ref_id):
        self.refresh()
        with self.lock:
            item = next((copy.deepcopy(r) for r in self.value["items"] if r["id"] == ref_id), None)
        if item is None:
            return None
        item["source_resolution"] = self.sources.resolve(item)
        item["canonical_status"] = "not_connected"
        item["work_state"] = None
        if self.config.get("canonical_reads"):
            with self.lock:
                status = self.value["canonical_graph"]
            item["canonical_status"] = status["status"]
            if status["status"] == "available":
                canonical = self.graph_reader("/session-refs/v1/" + ref_id + "?limit=100")
                item["canonical_status"] = (
                    "imported"
                    if canonical
                    else "not_imported"
                    if item.get("canonical") is False
                    else "unavailable"
                )
                if canonical:
                    item["canonical"] = True
                    item["canonical_import"] = canonical
                    item["work_state"] = canonical.get("work_state")
                    for binding in canonical["work_refs"]:
                        existing = next(
                            (b for b in item["work_refs"] if b["key"] == binding["key"]), None
                        )
                        if existing:
                            existing.update(canonical=True, canonical_basis=binding["basis"])
                        else:
                            item["work_refs"].append({**binding, "canonical": True})
                    for binding in canonical["links"]:
                        forward = binding["origin_id"] == ref_id
                        target = binding["target_id"] if forward else binding["origin_id"]
                        relation = binding["relation"]
                        if not forward:
                            relation = {
                                "uses_runtime": "runtime_for",
                                "delegated_to": "delegated_from",
                                "forked_from": "fork_origin_for",
                            }[relation]
                        existing = next(
                            (
                                link
                                for link in item["links"]
                                if link["id"] == target and link["relation"] == relation
                            ),
                            None,
                        )
                        if existing:
                            existing["canonical"] = True
                        else:
                            item["links"].append(
                                {
                                    "id": target,
                                    "relation": relation,
                                    "basis": binding["basis"],
                                    "canonical": True,
                                }
                            )
        item["related_work"] = []
        for binding in item["work_refs"][:32]:
            work = self.work_reader(binding["key"])
            item["related_work"].append(
                {**binding, "resolution": "available" if work else "missing", **(work or {})}
            )
        with self.lock:
            for link in item["links"]:
                other = next((r for r in self.value["items"] if r["id"] == link["id"]), None)
                link["resolution"] = "loaded" if other else "outside_loaded_window"
        return item


class Handler(BaseHTTPRequestHandler):
    server_version = "DevGraphSessionReader/1"

    def log_message(self, *args):
        pass

    def send(self, code, value):
        raw = json.dumps(value, allow_nan=False).encode()
        self.send_response(code)
        for key, value in {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "Content-Length": str(len(raw)),
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        }.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def admitted(self):
        if (
            self.headers.get("Host") != self.server.authority
            or self.headers.get("Origin")
            or self.headers.get("Sec-Fetch-Site", "none") != "none"
        ):
            self.server.audit("denied", "invalid_origin")
            self.send(403, {"error": "invalid_origin"})
            return False
        auth = self.headers.get("Authorization", "")
        try:
            self.server.verifier.verify(
                auth[7:] if auth.startswith("Bearer ") else None, audience=self.server.audience
            )
        except Exception:
            self.server.audit("denied", "credential_required")
            self.send(401, {"error": "credential_required"})
            return False
        self.server.audit("read", "session_metadata")
        return True

    def do_GET(self):
        if not self.admitted():
            return
        url = urllib.parse.urlsplit(self.path)
        if len(self.path) > 512 or self.headers.get("Transfer-Encoding"):
            return self.send(400, {"error": "invalid_request"})
        params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        try:
            if url.path == "/session-refs/v1":
                if set(params) - {"limit", "after"} or any(len(v) != 1 for v in params.values()):
                    raise ValueError("invalid_query")
                limit = int(params.get("limit", ["100"])[0])
                after = params.get("after", [None])[0]
                if not 1 <= limit <= 100 or (after is not None and not REF.fullmatch(after)):
                    raise ValueError("invalid_query")
                return self.send(200, self.server.reader.snapshot(after=after, limit=limit))
            if url.path.startswith("/session-refs/v1/") and not params:
                ref_id = url.path.removeprefix("/session-refs/v1/")
                if not REF.fullmatch(ref_id):
                    raise ValueError("invalid_ref")
                result = self.server.reader.detail(ref_id)
                return self.send(200 if result else 404, result or {"error": "ref_not_loaded"})
            return self.send(404, {"error": "not_found"})
        except ValueError:
            return self.send(400, {"error": "invalid_query_or_changed_cursor"})
        except PermissionError:
            return self.send(401, {"error": "work_access_denied"})
        except Exception:
            return self.send(503, {"error": "source_unavailable"})

    def do_POST(self):
        if self.admitted():
            self.send(405, {"error": "read_only"})

    do_PUT = do_PATCH = do_DELETE = do_POST


def serve(config_path):
    owned(config_path, private=True)
    config = json.loads(config_path.read_text())
    root = Path(config["data_root"])
    registry = root / "secrets/devgraph.read.v1/credential-registry.json"
    owned(registry, private=True)
    audience = json.loads(registry.read_text())["audience"]
    verifier = LocalReadCredentialVerifier(data_root=root, audience=audience)
    server = ThreadingHTTPServer(("127.0.0.1", 48642), Handler)
    server.authority = "127.0.0.1:48642"
    server.verifier, server.audience = verifier, audience
    server.reader = SessionReader(config)
    audit_path = config_path.parent / "read-audit.sqlite3"
    if audit_path.exists():
        owned(audit_path, private=True)
    else:
        fd = os.open(audit_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    audit_lock = threading.Lock()
    with sqlite3.connect(audit_path) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reads (id INTEGER PRIMARY KEY, at TEXT, "
            "outcome TEXT, reason TEXT)"
        )

    def audit(outcome, reason):
        with audit_lock, sqlite3.connect(audit_path) as db:
            db.execute(
                "INSERT INTO reads(at,outcome,reason) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(), outcome, reason),
            )
            db.execute("DELETE FROM reads WHERE id < (SELECT MAX(id)-10000 FROM reads)")

    server.audit = audit
    server.reader.refresh(force=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    serve(parser.parse_args().config)
