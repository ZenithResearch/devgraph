# Parameterized backup cost table

> **parameterized planning estimate; non-binding; not a quote; not live pricing**

As-of date for input provenance: `2026-07-20`.

Low, Base, and High are explicit scenarios, not a confidence interval.

## Formulas

- source at horizon GiB = source GiB + monthly growth GiB × (scenario months − 1)
- retained full-equivalent copies = retained copies × (full fraction + incremental fraction × incremental equivalent fraction)
- retained payload GiB = retained full-equivalent copies × source at horizon GiB × compression ratio × replication factor
- Storage = retained payload GiB × storage price
- Operations = monthly operation count / 1,000 × operation price
- Transfer = egress GiB × egress price + restore GiB × restore price
- Labor = monthly labor hours × labor rate
- monthly backup count = backups per day × 30 (planning-month convention)
- Monthly total = Storage + Operations + Transfer + Fixed fee + Labor

## Scenario results

| Scenario | Currency | Source at horizon GiB | retained full-equivalent copies | Retained payload GiB | Storage | Operations | Transfer | Fixed fee | Labor | Monthly total | Unknown inputs |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Low | USD | 21.00 | 3.92 | 41.16 | 0.82 | 0.05 | 0.40 | 0.00 | 168.75 | 170.02 | none |
| Base | USD | 105.00 | 11.20 | 1,411.20 | 35.28 | 0.30 | 2.70 | 20.00 | 500.00 | 558.28 | none |
| High | USD | 660.00 | 35.10 | 48,648.60 | 1,459.46 | 2.00 | 18.00 | 100.00 | 3,000.00 | 4,579.46 | none |

## Price and labor provenance

All committed values are synthetic operator-supplied assumptions, not provider prices.

| Scenario | Input | Value | Unit | Currency | Retrieved | Scope | Source |
|---|---|---:|---|---|---|---|---|
| Low | `storage_price` | 0.02 | USD/GiB-month | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Low | `operation_price_per_1k` | 0.005 | USD/1k operations | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Low | `egress_price` | 0.08 | USD/GiB | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Low | `restore_price` | 0.08 | USD/GiB | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Low | `fixed_monthly_fee` | 0 | USD/month | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Low | `labor_rate` | 75 | USD/hour | USD | 2026-07-20 | Synthetic planning assumption; not compensation guidance | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Base | `storage_price` | 0.025 | USD/GiB-month | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Base | `operation_price_per_1k` | 0.006 | USD/1k operations | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Base | `egress_price` | 0.09 | USD/GiB | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Base | `restore_price` | 0.09 | USD/GiB | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Base | `fixed_monthly_fee` | 20 | USD/month | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| Base | `labor_rate` | 100 | USD/hour | USD | 2026-07-20 | Synthetic planning assumption; not compensation guidance | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| High | `storage_price` | 0.03 | USD/GiB-month | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| High | `operation_price_per_1k` | 0.01 | USD/1k operations | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| High | `egress_price` | 0.12 | USD/GiB | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| High | `restore_price` | 0.12 | USD/GiB | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| High | `fixed_monthly_fee` | 100 | USD/month | USD | 2026-07-20 | Synthetic planning assumption; not provider pricing | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |
| High | `labor_rate` | 150 | USD/hour | USD | 2026-07-20 | Synthetic planning assumption; not compensation guidance | [source](https://github.com/ZenithResearch/devgraph/blob/main/docs/ops/backup-cost-inputs.json) |

Taxes, discounts, minimums, provider eligibility, and deployment-specific charges may be excluded.
Unknown inputs remain UNKNOWN and are never converted to zero.
