"""Transport-agnostic remote authorized-access boundary."""

from devgraph.remote.contracts import (
    RemoteCredentialValidationError,
    RemoteIngressCredential,
    RemoteOperation,
)
from devgraph.remote.gateway import RemoteGateway, generate_request_id
from devgraph.remote.responses import (
    PROBLEM_TABLE,
    RemoteFound,
    RemoteListed,
    RemoteMutationAccepted,
    RemoteProblemResponse,
    TransportSafeProjector,
)

__all__ = [
    "PROBLEM_TABLE",
    "RemoteCredentialValidationError",
    "RemoteFound",
    "RemoteGateway",
    "RemoteIngressCredential",
    "RemoteListed",
    "RemoteMutationAccepted",
    "RemoteOperation",
    "RemoteProblemResponse",
    "TransportSafeProjector",
    "generate_request_id",
]
