from __future__ import annotations

from pathlib import Path

import pytest

from devgraph.model.validation import (
    ID_GRAMMAR_VERSION,
    SIGNED_64_MAX,
    SIGNED_64_MIN,
    InvalidWorkObjectId,
    NumericBoundError,
    validate_page_limit,
    validate_priority,
    validate_version,
    validate_work_object_id,
)

ROOT = Path(__file__).parents[2]

VALID_IDS = (
    "a",
    "0",
    "a0",
    "0a",
    "a-b",
    "a--b",
    "prefix-123",
    "a" * 256,
)
INVALID_IDS: tuple[object, ...] = (
    "",
    "a" * 257,
    "-a",
    "a-",
    "A",
    "a_b",
    "a.b",
    "a/b",
    "a b",
    " a",
    "a ",
    "a\n",
    "a\x00b",
    "é",
    "Ａ",
    "x') MATCH (n) DETACH DELETE n //",
    None,
    1,
    b"a",
)
VALID_VERSIONS = (1, 2, SIGNED_64_MAX - 1, SIGNED_64_MAX)
INVALID_VERSIONS: tuple[object, ...] = (
    0,
    -1,
    SIGNED_64_MAX + 1,
    True,
    False,
    1.0,
    "1",
    None,
)
VALID_PRIORITIES = (
    SIGNED_64_MIN,
    SIGNED_64_MIN + 1,
    -1,
    0,
    1,
    SIGNED_64_MAX - 1,
    SIGNED_64_MAX,
)
INVALID_PRIORITIES: tuple[object, ...] = (
    SIGNED_64_MIN - 1,
    SIGNED_64_MAX + 1,
    True,
    False,
    1.0,
    "1",
    None,
)


def test_validation_contract_constants_are_exact() -> None:
    assert ID_GRAMMAR_VERSION == "ascii-lower-hyphen-v1"
    assert SIGNED_64_MIN == -9223372036854775808
    assert SIGNED_64_MAX == 9223372036854775807


@pytest.mark.parametrize("value", VALID_IDS)
def test_canonical_ids_are_accepted_without_normalization(value: str) -> None:
    result = validate_work_object_id(value)
    assert result == value
    assert result is value


@pytest.mark.parametrize("value", INVALID_IDS)
def test_noncanonical_ids_are_rejected_without_coercion(value: object) -> None:
    with pytest.raises(InvalidWorkObjectId, match="invalid_work_object_id"):
        validate_work_object_id(value)


@pytest.mark.parametrize("value", VALID_VERSIONS)
def test_valid_versions_are_accepted(value: int) -> None:
    assert validate_version(value) == value


@pytest.mark.parametrize("value", INVALID_VERSIONS)
def test_invalid_versions_are_rejected_without_coercion(value: object) -> None:
    with pytest.raises(NumericBoundError, match="invalid_version"):
        validate_version(value)


@pytest.mark.parametrize("value", VALID_PRIORITIES)
def test_valid_priorities_are_accepted(value: int) -> None:
    assert validate_priority(value) == value


@pytest.mark.parametrize("value", INVALID_PRIORITIES)
def test_invalid_priorities_are_rejected_without_coercion(value: object) -> None:
    with pytest.raises(NumericBoundError, match="invalid_priority"):
        validate_priority(value)


@pytest.mark.parametrize("value", [1, 2, 50, 100])
def test_valid_page_limits_are_accepted(value: int) -> None:
    assert validate_page_limit(value) == value


@pytest.mark.parametrize("value", [0, 101, True, False, 1.0, "1", None])
def test_invalid_page_limits_are_rejected_without_coercion(value: object) -> None:
    with pytest.raises(NumericBoundError, match="invalid_page_limit"):
        validate_page_limit(value)


def test_validator_authority_is_not_copied_across_production_layers() -> None:
    exact_regex = "^[a-z0-9](?:[a-z0-9-]{0,254}[a-z0-9])?$"
    signed_min = "-9223372036854775808"
    signed_max = "9223372036854775807"
    owner = ROOT / "src/devgraph/model/validation.py"
    assert exact_regex in owner.read_text()
    for path in (ROOT / "src/devgraph").rglob("*.py"):
        if path == owner:
            continue
        source = path.read_text()
        assert exact_regex not in source, path
        assert signed_min not in source, path
        assert signed_max not in source, path
