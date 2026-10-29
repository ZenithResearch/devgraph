"""Bounded JSON/route admission only; all cryptography remains native secS."""

import json

MAX_BYTES = 131072
SAFE = 9007199254740991
SESSION_BASES = ("/holder-sessions/v1", "/devgraph/holder-sessions/v1")
SESSION_GET_PATHS = frozenset(base + "/capabilities" for base in SESSION_BASES)
SESSION_POST_PATHS = frozenset(
    base + suffix for base in SESSION_BASES for suffix in ("/challenges", "/exchange", "/status")
)
SESSION_PATHS = SESSION_GET_PATHS | SESSION_POST_PATHS


def _reject(_value=None):
    raise ValueError("invalid holder session JSON")


def _integer(raw):
    value = int(raw)
    if abs(value) > SAFE:
        _reject()
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result or key == "$serde_json::private::Number":
            _reject()
        result[key] = value
    return result


def _shape(value, depth=0):
    if depth > 32:
        _reject()
    if isinstance(value, dict):
        for key, item in value.items():
            key.encode("utf8")
            _shape(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _shape(item, depth + 1)
    elif isinstance(value, str):
        value.encode("utf8")


def loads(raw, *, maximum_bytes=MAX_BYTES):
    if not raw or len(raw) > maximum_bytes:
        _reject()
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_reject,
            parse_constant=_reject,
        )
        _shape(value)
        if not isinstance(value, dict):
            _reject()
        return value
    except (RecursionError, UnicodeError):
        raise ValueError("invalid holder session JSON") from None
