from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "docs" / "ops" / "backup-cost-inputs.json"
TABLE = ROOT / "docs" / "ops" / "backup-cost-table.md"
SCRIPT = ROOT / "scripts" / "render_backup_costs.py"


def _module():
    assert SCRIPT.is_file(), SCRIPT
    spec = importlib.util.spec_from_file_location("render_backup_costs_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inputs() -> dict:
    assert INPUTS.is_file(), INPUTS
    return json.loads(INPUTS.read_text())


def test_cost_inputs_have_explicit_units_provenance_and_scenarios() -> None:
    data = _inputs()

    assert data["schema_version"] == 1
    assert data["as_of"] == "2026-07-20"
    assert set(data["scenarios"]) == {"low", "base", "high"}
    assert "parameterized planning estimate" in data["disclaimer"]
    assert "not a quote" in data["disclaimer"]
    assert "not live pricing" in data["disclaimer"]
    for scenario in data["scenarios"].values():
        for field in (
            "source_data_gib",
            "monthly_growth_gib",
            "compression_ratio",
            "backups_per_day",
            "full_backup_fraction",
            "incremental_backup_fraction",
            "incremental_equivalent_fraction",
            "retention_copies",
            "replication_factor",
            "monthly_operation_count",
            "monthly_egress_gib",
            "monthly_restore_gib",
            "monthly_restore_count",
            "monthly_test_count",
            "backup_labor_hours",
            "restore_labor_hours",
            "test_labor_hours",
            "scenario_months",
        ):
            assert field in scenario
        for price_name in (
            "storage_price",
            "operation_price_per_1k",
            "egress_price",
            "restore_price",
            "fixed_monthly_fee",
            "labor_rate",
        ):
            price = scenario[price_name]
            assert set(price) == {
                "value",
                "currency",
                "unit",
                "source_url",
                "retrieved_at",
                "scope",
                "unknown",
            }
            assert price["source_url"].startswith("https://")
            assert price["retrieved_at"] == data["as_of"]
            assert isinstance(price["unknown"], bool)


def test_cost_formula_matches_explicit_base_scenario_inputs() -> None:
    module = _module()
    data = _inputs()
    base = data["scenarios"]["base"]

    result = module.calculate_scenario("base", base, data["as_of"])

    source_at_horizon = base["source_data_gib"] + base["monthly_growth_gib"] * (
        base["scenario_months"] - 1
    )
    retained_copies = sum(base["retention_copies"].values())
    full_equivalents = retained_copies * (
        base["full_backup_fraction"]
        + base["incremental_backup_fraction"]
        * base["incremental_equivalent_fraction"]
    )
    retained_payload = (
        full_equivalents
        * source_at_horizon
        * base["compression_ratio"]
        * base["replication_factor"]
    )
    monthly_backup_count = base["backups_per_day"] * 30
    labor_hours = (
        monthly_backup_count * base["backup_labor_hours"]
        + base["monthly_restore_count"] * base["restore_labor_hours"]
        + base["monthly_test_count"] * base["test_labor_hours"]
    )

    assert result["source_at_horizon_gib"] == pytest.approx(source_at_horizon)
    assert result["retained_full_equivalent_copies"] == pytest.approx(full_equivalents)
    assert result["retained_payload_gib"] == pytest.approx(retained_payload)
    assert result["storage_cost"] == pytest.approx(
        retained_payload * base["storage_price"]["value"]
    )
    assert result["operation_cost"] == pytest.approx(
        base["monthly_operation_count"]
        / 1000
        * base["operation_price_per_1k"]["value"]
    )
    assert result["transfer_cost"] == pytest.approx(
        base["monthly_egress_gib"] * base["egress_price"]["value"]
        + base["monthly_restore_gib"] * base["restore_price"]["value"]
    )
    assert result["labor_cost"] == pytest.approx(
        labor_hours * base["labor_rate"]["value"]
    )
    assert result["monthly_total"] == pytest.approx(
        result["storage_cost"]
        + result["operation_cost"]
        + result["transfer_cost"]
        + base["fixed_monthly_fee"]["value"]
        + result["labor_cost"]
    )


def test_unknown_price_propagates_and_is_never_treated_as_zero() -> None:
    module = _module()
    data = _inputs()
    scenario = deepcopy(data["scenarios"]["base"])
    scenario["egress_price"]["unknown"] = True
    scenario["egress_price"]["value"] = None

    result = module.calculate_scenario("base", scenario, data["as_of"])

    assert result["transfer_cost"] is None
    assert result["monthly_total"] is None
    assert "egress_price" in result["unknown_inputs"]
    assert "UNKNOWN" in module.render_table({"base": result}, data["disclaimer"], data["as_of"])


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda data: data["scenarios"].pop("high"), "scenario_set_invalid"),
        (
            lambda data: data["scenarios"]["base"]["storage_price"].pop("source_url"),
            "price_provenance_invalid",
        ),
        (
            lambda data: data["scenarios"]["base"]["storage_price"].update(
                {"retrieved_at": "2024-01-01"}
            ),
            "price_provenance_stale",
        ),
        (
            lambda data: data["scenarios"]["base"].update(
                {"full_backup_fraction": 0.8, "incremental_backup_fraction": 0.3}
            ),
            "backup_fraction_invalid",
        ),
        (
            lambda data: data["scenarios"]["base"]["storage_price"].update(
                {"unknown": True, "value": 0}
            ),
            "unknown_price_value_invalid",
        ),
    ],
)
def test_invalid_or_stale_cost_inputs_fail_closed(mutation, reason: str) -> None:
    module = _module()
    data = _inputs()
    mutation(data)

    with pytest.raises(ValueError, match=reason):
        module.validate_inputs(data)


def test_generated_cost_table_is_byte_reproducible_and_checkable() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "backup cost table is current"
    module = _module()
    data = module.load_inputs(INPUTS)
    calculated = module.calculate_all(data)
    assert TABLE.read_text() == module.render_table(
        calculated, data["disclaimer"], data["as_of"]
    )
    text = TABLE.read_text()
    for term in (
        "Low",
        "Base",
        "High",
        "retained full-equivalent copies",
        "Storage",
        "Operations",
        "Transfer",
        "Fixed fee",
        "Labor",
        "Monthly total",
        "Unknown inputs",
        "parameterized planning estimate",
        "not a quote",
        "not live pricing",
    ):
        assert term in text
    assert "explicit scenarios, not a confidence interval" in text
