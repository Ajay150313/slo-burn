---
name: slo-burn
description: Configure and use slo-burn for multi-window SLO burn-rate alerting. Use when writing an slos.yaml, choosing burn-rate windows and multipliers, routing page and ticket alerts, or computing time to error-budget exhaustion.
---

# slo-burn

Standalone runtime evaluator for multi-window burn-rate alerts (Google SRE Workbook method).
It queries Prometheus or Datadog, tracks alert state, notifies PagerDuty, Slack or OpsGenie,
and serves a dashboard.

## Define an SLO

```yaml
backends:
  prometheus:
    url: http://prometheus:9090

slos:
  - name: checkout-api-availability
    target: 99.9
    window_days: 30
    indicators:
      good_query: |
        sum(rate(http_requests_total{service="checkout",code!~"5.."}[{window}]))
      total_query: |
        sum(rate(http_requests_total{service="checkout"}[{window}]))
    alerts:
      - {severity: page,   short_window: 1h, long_window: 5h, multiplier: 14x}
      - {severity: ticket, short_window: 6h, long_window: 3d, multiplier: 3x}
    notify:
      - {type: slack, webhook_url: "${SLACK_WEBHOOK}", channel: "#oncall"}
```

- `{window}` is replaced at evaluation time.
- An alert fires only when both the short and long window exceed the multiplier. The long window
  confirms the burn is sustained; the short window lets the alert clear quickly.
- Read secrets with `${VAR}`; never write credentials into the file.

Run: `slo-burn serve --config slos.yaml` (dashboard on 9000, metrics on 9090).

## Budget math

```python
from src.core.models import allowed_downtime_minutes, hours_to_budget_exhaustion

allowed_downtime_minutes(99.99, window_days=30)                    # about 4.32 minutes
hours_to_budget_exhaustion(budget_remaining_fraction=0.5, burn_rate=14.4, window_days=30)
```

Page when the forecast drops below your response time, not on the burn-rate number alone.

## Guidance

- Page at 14x over 1h/5h; ticket at 3x over 6h/3d. Tune from there.
- Pick the SLI from user-visible success, not host health.
- Run tests with `PYTHONPATH=src pytest -q` or after `pip install -e ".[dev]"`.
