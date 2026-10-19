# Changelog

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). Versioning follows [Semantic Versioning](https://semver.org/).

---

## Unreleased

### Fixed
- License metadata: `pyproject.toml` declared MIT while the repository is Apache-2.0.
- Removed placeholder `your-org` links, a nonexistent Helm chart and container image
  reference; Docker instructions now build the image locally.
- Added CI (lint, tests on Python 3.9-3.12).

### Added
- `hours_to_budget_exhaustion(budget_remaining, burn_rate, window_days)` — hours until the error budget is gone
- `allowed_downtime_minutes(target, window_days)` — 99.99% over 30 days is 4.32 minutes
- `ErrorBudgetStatus.hours_remaining` for on-call paging on forecast, not threshold

## [0.3.0] — 2025-01-15

### Added
- Built-in web dashboard at `localhost:9000` — budget gauges, burn rate table, alert history, auto-refresh every 60s
- Prometheus metrics exporter at `localhost:9090/metrics` — seven metrics including `slo_alert_firing` and `slo_error_budget_consumed_ratio`
- Datadog backend — supports all five Datadog regional sites (US1, US3, US5, EU1, AP1)
- `slo-burn budget` CLI command — error budget remaining per SLO, supports `--json`
- `slo-burn template` command — list and generate starter YAML from built-in templates
- Six built-in SLO templates: `http-availability`, `http-latency`, `grpc-availability`, `database-query-time`, `queue-processing`, `cronjob-success`
- Error budget exhaustion projection — estimated time-to-empty based on current burn rate
- `{window}` placeholder substitution in all PromQL queries

### Changed
- All SLOs now evaluated concurrently — reduces evaluation latency on large configs
- Alert state transitions tracked across evaluation cycles — notifications fire on changes only, not on every tick

### Fixed
- Division by zero when metrics backend returns empty series for total query
- Alert could fire when only the short window exceeded the threshold — long window confirmation was not being enforced correctly in edge cases involving NaN responses

---

## [0.2.0] — 2024-11-01

### Added
- OpsGenie notification target
- `slo-burn validate` command — exits non-zero on config errors, suitable for CI
- `slo-burn status --json` flag for machine-readable output
- Environment variable interpolation (`${VAR}`) throughout YAML config
- VictoriaMetrics and Grafana Mimir tested and confirmed compatible with Prometheus backend

### Changed
- PagerDuty `custom_details` now includes `burn_rate_short`, `burn_rate_long`, `threshold_multiplier`, and `sli` — enough context to start an investigation without opening the dashboard

---

## [0.1.0] — 2024-09-10

### Added
- Core multi-window burn rate evaluation engine
- Prometheus backend
- PagerDuty and Slack notification targets
- YAML-first SLO configuration with schema validation
- `slo-burn serve` CLI command
- Apache 2.0 license
