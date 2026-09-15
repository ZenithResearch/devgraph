#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = ROOT / "docs" / "ops" / "backup-cost-inputs.json"
DEFAULT_OUTPUT = ROOT / "docs" / "ops" / "backup-cost-table.md"
SCENARIOS = ("low", "base", "high")
PRICE_FIELDS = (
    "storage_price",
    "operation_price_per_1k",
    "egress_price",
    "restore_price",
    "fixed_monthly_fee",
    "labor_rate",
)
EXPECTED_UNITS = {
    "storage_price": "USD/GiB-month",
    "operation_price_per_1k": "USD/1k operations",
    "egress_price": "USD/GiB",
    "restore_price": "USD/GiB",
    "fixed_monthly_fee": "USD/month",
    "labor_rate": "USD/hour",
}
PRICE_KEYS = {
    "value",
    "currency",
    "unit",
    "source_url",
    "retrieved_at",
    "scope",
    "unknown",
}
NUMERIC_FIELDS = (
    "source_data_gib",
    "monthly_growth_gib",
    "compression_ratio",
    "backups_per_day",
    "full_backup_fraction",
    "incremental_backup_fraction",
    "incremental_equivalent_fraction",
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
)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parse_date(value: object, reason: str) -> date:
    if not isinstance(value, str):
        raise ValueError(reason)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(reason) from None


def validate_inputs(data: dict[str, Any]) -> None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("cost_schema_invalid")
    as_of = _parse_date(data.get("as_of"), "as_of_invalid")
    disclaimer = data.get("disclaimer")
    if not isinstance(disclaimer, str) or not all(
        phrase in disclaimer
        for phrase in ("parameterized planning estimate", "not a quote", "not live pricing")
    ):
        raise ValueError("disclaimer_invalid")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(SCENARIOS):
        raise ValueError("scenario_set_invalid")
    for scenario_name in SCENARIOS:
        scenario = scenarios[scenario_name]
        if not isinstance(scenario, dict):
            raise ValueError("scenario_invalid")
        for field in NUMERIC_FIELDS:
            if field not in scenario or not _number(scenario[field]) or scenario[field] < 0:
                raise ValueError("scenario_numeric_invalid")
        if scenario["source_data_gib"] <= 0 or scenario["compression_ratio"] <= 0:
            raise ValueError("scenario_numeric_invalid")
        if scenario["backups_per_day"] <= 0 or scenario["scenario_months"] < 1:
            raise ValueError("scenario_numeric_invalid")
        if not math.isclose(
            scenario["full_backup_fraction"] + scenario["incremental_backup_fraction"],
            1.0,
            abs_tol=1e-9,
        ):
            raise ValueError("backup_fraction_invalid")
        if not 0 <= scenario["incremental_equivalent_fraction"] <= 1:
            raise ValueError("backup_fraction_invalid")
        retention = scenario.get("retention_copies")
        if not isinstance(retention, dict) or set(retention) != {"daily", "weekly", "monthly"}:
            raise ValueError("retention_invalid")
        if any(not _number(value) or value < 0 for value in retention.values()):
            raise ValueError("retention_invalid")
        for price_name in PRICE_FIELDS:
            price = scenario.get(price_name)
            if not isinstance(price, dict) or set(price) != PRICE_KEYS:
                raise ValueError("price_provenance_invalid")
            if not all(
                isinstance(price[key], str) and price[key]
                for key in ("currency", "unit", "source_url", "retrieved_at", "scope")
            ):
                raise ValueError("price_provenance_invalid")
            if not price["source_url"].startswith("https://"):
                raise ValueError("price_provenance_invalid")
            if price["unit"] != EXPECTED_UNITS[price_name]:
                raise ValueError("price_provenance_invalid")
            retrieved_at = _parse_date(price["retrieved_at"], "price_provenance_invalid")
            age = (as_of - retrieved_at).days
            if age < 0 or age > 365:
                raise ValueError("price_provenance_stale")
            if not isinstance(price["unknown"], bool):
                raise ValueError("price_provenance_invalid")
            if price["unknown"]:
                if price["value"] is not None:
                    raise ValueError("unknown_price_value_invalid")
            elif not _number(price["value"]) or price["value"] < 0:
                raise ValueError("price_value_invalid")
        if {scenario[name]["currency"] for name in PRICE_FIELDS} != {"USD"}:
            raise ValueError("price_currency_mismatch")


def load_inputs(path: Path = DEFAULT_INPUTS) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("cost_inputs_unreadable") from None
    validate_inputs(data)
    return data


def _cost(value: float, price: dict[str, Any], unknown: list[str], name: str) -> float | None:
    if price["unknown"]:
        unknown.append(name)
        return None
    return value * price["value"]


def calculate_scenario(name: str, scenario: dict[str, Any], as_of: str) -> dict[str, Any]:
    validate_inputs(
        {
            "schema_version": 1,
            "as_of": as_of,
            "disclaimer": "parameterized planning estimate; not a quote; not live pricing",
            "scenarios": {candidate: scenario for candidate in SCENARIOS},
        }
    )
    source_at_horizon = scenario["source_data_gib"] + scenario["monthly_growth_gib"] * (
        scenario["scenario_months"] - 1
    )
    retained_copies = sum(scenario["retention_copies"].values())
    full_equivalents = retained_copies * (
        scenario["full_backup_fraction"]
        + scenario["incremental_backup_fraction"]
        * scenario["incremental_equivalent_fraction"]
    )
    retained_payload = (
        full_equivalents
        * source_at_horizon
        * scenario["compression_ratio"]
        * scenario["replication_factor"]
    )
    monthly_backup_count = scenario["backups_per_day"] * 30
    labor_hours = (
        monthly_backup_count * scenario["backup_labor_hours"]
        + scenario["monthly_restore_count"] * scenario["restore_labor_hours"]
        + scenario["monthly_test_count"] * scenario["test_labor_hours"]
    )
    unknown: list[str] = []
    storage = _cost(retained_payload, scenario["storage_price"], unknown, "storage_price")
    operations = _cost(
        scenario["monthly_operation_count"] / 1000,
        scenario["operation_price_per_1k"],
        unknown,
        "operation_price_per_1k",
    )
    egress = _cost(
        scenario["monthly_egress_gib"], scenario["egress_price"], unknown, "egress_price"
    )
    restore = _cost(
        scenario["monthly_restore_gib"],
        scenario["restore_price"],
        unknown,
        "restore_price",
    )
    transfer = None if egress is None or restore is None else egress + restore
    fixed = _cost(1, scenario["fixed_monthly_fee"], unknown, "fixed_monthly_fee")
    labor = _cost(labor_hours, scenario["labor_rate"], unknown, "labor_rate")
    components = (storage, operations, transfer, fixed, labor)
    total = (
        None
        if any(value is None for value in components)
        else sum(value for value in components if value is not None)
    )
    return {
        "name": name,
        "currency": scenario["storage_price"]["currency"],
        "source_at_horizon_gib": source_at_horizon,
        "retained_full_equivalent_copies": full_equivalents,
        "retained_payload_gib": retained_payload,
        "storage_cost": storage,
        "operation_cost": operations,
        "transfer_cost": transfer,
        "fixed_monthly_fee": fixed,
        "labor_hours": labor_hours,
        "labor_cost": labor,
        "monthly_total": total,
        "unknown_inputs": sorted(set(unknown)),
        "inputs": scenario,
    }


def calculate_all(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_inputs(data)
    return {
        name: calculate_scenario(name, data["scenarios"][name], data["as_of"])
        for name in SCENARIOS
    }


def _display(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value:,.2f}"


def _display_price(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def render_table(results: dict[str, dict[str, Any]], disclaimer: str, as_of: str) -> str:
    lines = [
        "# Parameterized backup cost table",
        "",
        f"> **{disclaimer}**",
        "",
        f"As-of date for input provenance: `{as_of}`.",
        "",
        "Low, Base, and High are explicit scenarios, not a confidence interval.",
        "",
        "## Formulas",
        "",
        "- source at horizon GiB = source GiB + monthly growth GiB × (scenario months − 1)",
        (
            "- retained full-equivalent copies = retained copies × (full fraction + "
            "incremental fraction × incremental equivalent fraction)"
        ),
        (
            "- retained payload GiB = retained full-equivalent copies × source at "
            "horizon GiB × compression ratio × replication factor"
        ),
        "- Storage = retained payload GiB × storage price",
        "- Operations = monthly operation count / 1,000 × operation price",
        "- Transfer = egress GiB × egress price + restore GiB × restore price",
        "- Labor = monthly labor hours × labor rate",
        "- monthly backup count = backups per day × 30 (planning-month convention)",
        "- Monthly total = Storage + Operations + Transfer + Fixed fee + Labor",
        "",
        "## Scenario results",
        "",
        (
            "| Scenario | Currency | Source at horizon GiB | retained full-equivalent "
            "copies | Retained payload GiB | Storage | Operations | Transfer | Fixed "
            "fee | Labor | Monthly total | Unknown inputs |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name in SCENARIOS:
        if name not in results:
            continue
        result = results[name]
        unknown = ", ".join(result["unknown_inputs"]) or "none"
        lines.append(
            "| "
            + " | ".join(
                (
                    name.title(),
                    result["currency"],
                    _display(result["source_at_horizon_gib"]),
                    _display(result["retained_full_equivalent_copies"]),
                    _display(result["retained_payload_gib"]),
                    _display(result["storage_cost"]),
                    _display(result["operation_cost"]),
                    _display(result["transfer_cost"]),
                    _display(result["fixed_monthly_fee"]),
                    _display(result["labor_cost"]),
                    _display(result["monthly_total"]),
                    unknown,
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Price and labor provenance",
            "",
            (
                "All committed values are synthetic operator-supplied assumptions, "
                "not provider prices."
            ),
            "",
            "| Scenario | Input | Value | Unit | Currency | Retrieved | Scope | Source |",
            "|---|---|---:|---|---|---|---|---|",
        ]
    )
    for name in SCENARIOS:
        if name not in results:
            continue
        for price_name in PRICE_FIELDS:
            price = results[name]["inputs"][price_name]
            value = _display_price(price["value"])
            lines.append(
                f"| {name.title()} | `{price_name}` | {value} | {price['unit']} | "
                f"{price['currency']} | {price['retrieved_at']} | {price['scope']} | "
                f"[source]({price['source_url']}) |"
            )
    lines.extend(
        [
            "",
            (
                "Taxes, discounts, minimums, provider eligibility, and "
                "deployment-specific charges may be excluded."
            ),
            "Unknown inputs remain UNKNOWN and are never converted to zero.",
            "",
        ]
    )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render the parameterized backup cost table")
    result.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result.add_argument("--check", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        data = load_inputs(args.inputs)
        rendered = render_table(calculate_all(data), data["disclaimer"], data["as_of"])
        if args.check:
            if not args.output.is_file() or args.output.read_text() != rendered:
                print("backup cost table is stale")
                return 2
            print("backup cost table is current")
            return 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        print("backup cost table rendered")
        return 0
    except (OSError, ValueError):
        print("backup cost rendering failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
