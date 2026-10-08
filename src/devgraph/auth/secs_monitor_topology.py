"""Additive signed topology profile. A v1 session cannot authorize this route."""

from __future__ import annotations

from devgraph.auth.secs_monitor_view_read import (
    SecSMonitorViewReadAdapter,
    SecSMonitorViewReadVerifier,
)
from devgraph.topology import InvalidTopologyFilter, build_topology, parse_target

OPERATION = "devgraph.monitor.view.read.v2"
SESSION_SCHEMA = "secs-devgraph-monitor-session.v2"
PROOF_SCHEMA = "devgraph-monitor-request-proof.v2"


class SecSMonitorTopologyVerifier(SecSMonitorViewReadVerifier):
    operation = OPERATION
    session_schema = SESSION_SCHEMA
    proof_schema = PROOF_SCHEMA
    version = 2
    session_signature_domain = b"secs-devgraph-monitor-session.v2/signature\x00"
    session_digest_domain = b"secs-devgraph-monitor-session.v2/session\x00"
    proof_signature_domain = b"devgraph.monitor.view.read.v2/request-proof\x00"
    proof_digest_domain = b"devgraph.monitor.view.read.v2/request-proof-digest\x00"

    @classmethod
    def valid_target(cls, target: str) -> bool:
        if not isinstance(target, str):
            return False
        try:
            parse_target(target, canonical=True)
        except InvalidTopologyFilter:
            return False
        return True


class SecSMonitorTopologyAdapter(SecSMonitorViewReadAdapter):
    def project(self, path_query: str):
        return build_topology(self._storage, parse_target(path_query, canonical=True))
