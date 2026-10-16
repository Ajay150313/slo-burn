# slo-burn 🔥

**Multi-window SLO burn rate alerting for teams tired of being woken up for nothing.**

[![CI](https://github.com/your-org/slo-burn/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/slo-burn/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/slo-burn.svg)](https://pypi.org/project/slo-burn/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![codecov](https://codecov.io/gh/your-org/slo-burn/branch/main/graph/badge.svg)](https://codecov.io/gh/your-org/slo-burn)

---

## The problem

I've been in SRE for eleven years. I've been paged at 3am for a 90-second CPU spike that auto-resolved before I finished unlocking my phone. I've watched teammates burn out. I've seen engineers disable PagerDuty notifications because they'd been conditioned to treat every alert as noise — and then miss the one that took down production for four hours.

The industry data backs this up:

- **73%** of organisations had outages caused by ignored or suppressed alerts — [Splunk State of Observability 2025](https://www.splunk.com/en_us/form/state-of-observability.html) (n=1,855)
- **44%** experienced an outage directly linked to a suppressed alert in the past year — [NeuBird AI State of Production Reliability 2026](https://www.morningstar.com/news/business-wire/20260406439955/) (n=1,039)
- **78%** of engineers discovered at least one failure only after customers were already affected — same report
- Operational toil **rose to 30%** in 2024, the first increase in five years — [Catchpoint SRE Report 2025](https://www.catchpoint.com/asset/2025-sre-report)
- Teams receive over 2,000 alerts weekly, with only 3% needing immediate action — incident.io

The root cause is almost always the same: **threshold-based alerting**. It has no memory. It has no context. It treats a 2-minute spike identically to a 48-hour sustained burn that will eat your entire error budget.

```
# This alert is lying to you
ALERT HighErrorRate
  IF rate(http_errors[5m]) / rate(http_requests[5m]) > 0.01
  FOR 5m
```

That rule fires on every blip. Your engineers start dismissing it. Then a real incident starts slowly at 0.3% error rate — below the threshold — and quietly exhausts your monthly error budget over 14 hours while nobody notices.

**slo-burn fixes this** by implementing the [Google SRE Workbook multi-window burn rate approach](https://sre.google/workbook/alerting-on-slos/#6-multiwindow-multi-burn-rate-alerts): an alert only fires when a high burn rate is confirmed across *two* time windows simultaneously — catching fast burns quickly without false positives, and catching slow burns before they destroy your budget.

---

## How it's different from sloth / slo-generator

[sloth](https://github.com/slok/sloth) and [google/slo-generator](https://github.com/google/slo-generator) are excellent tools. They *generate* Prometheus recording rules and AlertManager alert rules that you then deploy into your existing Prometheus stack.

**slo-burn is a self-contained runtime evaluator.** It:

- Queries your metrics backend directly (Prometheus, Datadog, or any compatible endpoint)
- Evaluates burn rates and manages alert state itself
- Routes notifications to PagerDuty, Slack, or OpsGenie without AlertManager
- Serves a built-in dashboard — no Grafana required
- Works the same way whether your backend is Prometheus, Thanos, Mimir, or Datadog

If you already have a full Prometheus + AlertManager stack and just want to generate rules: use sloth. If you want a standalone evaluator that works against any backend: use slo-burn.

---

## Quickstart

```bash
pip install slo-burn
```

Create `slos.yaml`:

```yaml
backends:
  prometheus:
    url: http://prometheus:9090

slos:
  - name: checkout-api-availability
    description: "99.9% of checkout requests must succeed"
    target: 99.9        # percent
    window_days: 30     # rolling compliance window

    indicators:
      # {window} is replaced at evaluation time with the actual window duration
      good_query: |
        sum(rate(http_requests_total{service="checkout",code!~"5.."}[{window}]))
      total_query: |
        sum(rate(http_requests_total{service="checkout"}[{window}]))

    alerts:
      # Wake someone up: burning budget 14× faster than sustainable
      - severity: page
        short_window: 1h
        long_window:  5h
        multiplier:   14x

      # Create a ticket: slow burn that will exhaust budget within 10 days
      - severity: ticket
        short_window: 6h
        long_window:  3d
        multiplier:   3x

    notify:
      - type: pagerduty
        integration_key: ${PD_CHECKOUT_KEY}
      - type: slack
        webhook_url: ${SLACK_WEBHOOK}
        channel: "#oncall"
```

Run:

```bash
export PD_CHECKOUT_KEY=your-pd-key
export SLACK_WEBHOOK=https://hooks.slack.com/...

slo-burn serve --config slos.yaml
```

Dashboard → `http://localhost:9000`
Prometheus metrics → `http://localhost:9090/metrics`

Page on time remaining, not just the burn-rate number:

```python
from src.core.models import hours_to_budget_exhaustion, allowed_downtime_minutes

# 99.99% over 30 days ≈ 4.32 minutes of allowed downtime
allowed_downtime_minutes(99.99, window_days=30)

# 14.4× burn on a full 30-day budget ≈ 50 hours left
hours_to_budget_exhaustion(1.0, 14.4, window_days=30)
```

---

## Why multi-window works

A single burn rate window has a precision problem. A 10-minute spike at 100× burn rate raises your 1-hour window above the alert threshold — but it's gone before anyone could even acknowledge the page. Multi-window requires *both* windows to exceed the threshold simultaneously:

```
10-minute spike at 100× burn rate:
  1h  window burn rate: ~16.7×  ✓ exceeds 14×
  5h  window burn rate:  ~3.3×  ✗ does not exceed 14×
  → Alert does NOT fire ✅

Sustained 20× burn rate for 2 hours:
  1h  window burn rate: 20×     ✓ exceeds 14×
  5h  window burn rate: 20×     ✓ exceeds 14×
  → Alert FIRES ✅
```

The short window catches it quickly. The long window confirms it's real. Transient spikes don't page you. Sustained burns always do.

See [docs/algorithm.md](docs/algorithm.md) for the full math including PromQL, multiplier selection, and the state machine.

---

## Configuration reference

### SLO definition

```yaml
slos:
  - name: my-service-latency          # unique, used in metrics labels
    description: "P99 < 300ms"        # shown in dashboard
    target: 99.5                       # SLO target in percent (0 < target < 100)
    window_days: 28                    # rolling compliance window in days
    backend: prometheus                # prometheus | datadog

    indicators:
      good_query:  "..."              # events where system behaved correctly
      total_query: "..."              # all events (good + bad)
      # Use {window} as a placeholder — slo-burn replaces it with e.g. "3600s"

    alerts:
      - severity: page                 # page | ticket | info
        short_window: 1h               # fast detection window
        long_window:  5h               # spike-rejection confirmation window
        multiplier:   14x              # how many times the sustainable burn rate

    notify:
      - type: pagerduty
        integration_key: ${PD_KEY}
      - type: slack
        webhook_url: ${SLACK_WEBHOOK}
        channel: "#incidents"
      - type: opsgenie
        api_key: ${OG_KEY}
        team: platform-sre

    labels:                            # propagated to Prometheus metrics
      team: payments
      tier: "1"
```

### Backends

```yaml
backends:
  prometheus:
    url: http://prometheus:9090
    timeout: 30s
    # Optional basic auth:
    # username: ${PROM_USER}
    # password: ${PROM_PASS}

  datadog:
    api_key: ${DD_API_KEY}
    app_key: ${DD_APP_KEY}
    site: us1                          # us1 | us3 | us5 | eu1 | ap1
```

Environment variables in `${VAR}` syntax are interpolated at startup.

---

## CLI

```
slo-burn serve     --config slos.yaml [--port 9000] [--interval 60]
slo-burn validate  --config slos.yaml
slo-burn status    --config slos.yaml [--json]
slo-burn budget    --config slos.yaml [--json]
slo-burn template  list
slo-burn template  use http-availability --service my-api --target 99.9
```

---

## Built-in SLO templates

```bash
$ slo-burn template list

  http-availability       HTTP/HTTPS availability (4xx/5xx error rate)
  http-latency            HTTP response time percentile
  grpc-availability       gRPC status-code-based availability
  database-query-time     Database query duration percentile
  queue-processing        Message queue consumer success rate
  cronjob-success         Scheduled job success rate

$ slo-burn template use http-availability --service checkout > slos.yaml
```

---

## Prometheus metrics exposed

`slo-burn` exports its own metrics at `:9090/metrics` so you can alert on the alerter:

```
slo_burn_rate{slo,window,severity}             # current burn rate (1.0 = sustainable)
slo_alert_firing{slo,threshold,severity}       # 1 if firing, 0 if healthy
slo_sli_current{slo,window}                    # SLI value in percent
slo_error_budget_consumed_ratio{slo,target}    # fraction of budget consumed (0–1+)
slo_error_budget_remaining_minutes{slo}        # minutes of budget left
slo_evaluation_duration_seconds                # how long the last evaluation took
slo_evaluation_timestamp_seconds               # unix timestamp of last evaluation
```

---

## Docker

```bash
docker run -p 9000:9000 -p 9090:9090 \
  -v $(pwd)/slos.yaml:/etc/slo-burn/slos.yaml \
  -e PD_KEY=your-key \
  -e SLACK_WEBHOOK=https://hooks.slack.com/... \
  ghcr.io/your-org/slo-burn:latest
```

---

## Kubernetes / Helm

```bash
helm repo add slo-burn https://your-org.github.io/slo-burn
helm install slo-burn slo-burn/slo-burn \
  --set config.prometheusUrl=http://prometheus:9090 \
  --set secrets.pdKey=your-pd-key
```

---

## Comparison

| | slo-burn | sloth | google/slo-generator |
|---|:---:|:---:|:---:|
| Self-contained (no AlertManager) | ✅ | ❌ | ❌ |
| Prometheus backend | ✅ | ✅ | ✅ |
| Datadog backend | ✅ | ❌ | ✅ |
| Built-in dashboard | ✅ | ❌ | ❌ |
| Runtime evaluator | ✅ | ❌ | ✅ |
| Generates recording rules | ❌ | ✅ | ❌ |
| PagerDuty / Slack / OpsGenie | ✅ | via AM | via AM |
| Single binary / pip install | ✅ | ✅ | ✅ |

---

## Contributing

Pull requests are welcome. Open issues are tagged [`good first issue`](https://github.com/your-org/slo-burn/issues?q=label%3Agood-first-issue) if you want somewhere to start.

**What we need most right now:**
- New Relic backend (see `src/exporters/prometheus.py` for the interface)
- OpenTelemetry / OTLP backend
- Helm chart polish
- More SLO templates

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup.

---

## License

MIT. See [LICENSE](LICENSE).

---

## Acknowledgements

Built on top of the work in the [Google SRE Workbook](https://sre.google/workbook/alerting-on-slos/), specifically the multi-window multi-burn-rate alerting approach in Chapter 5. The math belongs to them; the plumbing is mine.
