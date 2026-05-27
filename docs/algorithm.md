# How the multi-window burn rate algorithm works

If you're going to deploy slo-burn in production, read this.
If you're adding a new backend or debugging an alert that shouldn't have fired, start here.

---

## Why threshold alerts are broken

Most teams start with something like this:

```promql
ALERT HighErrorRate
  IF rate(http_errors[5m]) / rate(http_requests[5m]) > 0.01
  FOR 5m
```

It looks reasonable. It has two failure modes that will make your on-call miserable:

**False positives.** A deployment causes a 3-minute error spike. The alert fires, pages someone,
and by the time they acknowledge it the service has auto-recovered. You've woken someone up for
nothing. After this happens 50 times, engineers stop treating pages as urgent. Then the real
incident starts and nobody responds.

**Missed slow burns.** Your service is running at 0.5% error rate. Your SLO is 99.9%. The
threshold is 1%. The alert never fires. Meanwhile, you're consuming your 30-day error budget
at 50× the sustainable rate and will be completely out of budget in 14 hours. You find out when
a customer calls.

Threshold alerting has no concept of error budget. It has no concept of time. It just fires when
a number crosses a line.

---

## Error budget fundamentals

An SLO of 99.9% over 30 days means you're allowed a certain number of errors:

```
error_rate_threshold = 1 - (target / 100) = 1 - 0.999 = 0.001

error_budget_minutes = window_days × 24 × 60 × error_rate_threshold
                     = 30 × 24 × 60 × 0.001
                     = 43.2 minutes
```

That's 43.2 minutes of downtime per month. Not much.

The **burn rate** is how fast you're consuming that budget relative to the pace that would
exactly exhaust it at the end of the window:

```
burn_rate = current_error_rate / error_rate_threshold
```

- `burn_rate = 1.0` → consuming budget at the sustainable rate (budget exactly exhausted at day 30)
- `burn_rate = 14.4` → consuming budget 14.4× faster
- At 14.4×, the 30-day budget is exhausted in `30 / 14.4 ≈ 2.08 days ≈ 50 hours`

Burn rate gives you a single number that answers: *at this pace, how long until we have no
error budget left?*

---

## The dual-window algorithm

A single burn rate window has a precision problem. If you alert on `burn_rate(1h) > 14.4`,
a 10-minute spike at 100× burn rate fires the alert:

```
10-minute spike at 100× burn rate:
  burn_rate over 1h = (10/60) × 100 ≈ 16.7×   → exceeds threshold, FIRES
```

But the spike is over before anyone can acknowledge. You've paged for nothing.

The fix is to require **two windows** to both exceed the threshold simultaneously:

```
firing = (burn_rate(short_window) >= multiplier)
         AND
         (burn_rate(long_window) >= multiplier)
```

The short window detects the problem quickly. The long window rejects transient spikes.

```
10-minute spike at 100× burn rate:
  burn_rate(1h)  = 10/60 × 100 ≈ 16.7×  ✓ exceeds 14.4×
  burn_rate(5h)  = 10/300 × 100 ≈ 3.3×  ✗ does NOT exceed 14.4×
  → Does NOT fire ✅

Sustained 20× burn rate for 2 hours:
  burn_rate(1h)  = 20×   ✓ exceeds 14.4×
  burn_rate(5h)  = 20×   ✓ exceeds 14.4×
  → FIRES ✅
```

---

## Default thresholds (Google SRE Workbook, Table 5)

| Type | Short | Long | Multiplier | What it means |
|---|---|---|---|---|
| page | 1h | 5h | 14.4× | Exhausts 30d budget in ~50h. You need to fix this now. |
| ticket | 6h | 3d | 3× | Exhausts 30d budget in ~10d. Fix this sprint. |

The 14.4× page threshold was chosen to consume 5% of the monthly budget in 1 hour — enough
to be clearly budget-threatening, but not so sensitive that transient spikes fire it.

The 3× ticket threshold catches slow burns: a sustained error rate 3× above the SLO target
will exhaust the full 30-day budget in 10 days without anyone noticing under normal
threshold-based alerting.

---

## Choosing your own multipliers

If your window is not 30 days, or you want different sensitivity, use this formula:

```
multiplier = (budget_fraction_to_consume) × (window_days × 24h) / short_window_hours
```

To page when 5% of a 28-day budget is consumed in 2 hours:

```
multiplier = 0.05 × (28 × 24) / 2 = 0.05 × 336 = 16.8×
```

Rule of thumb: start with the Google defaults. Tune after you've run for a month and can
look at your actual firing patterns.

---

## PromQL implementation

For a 99.9% SLO on an HTTP service:

```promql
# Error rate over window W
error_rate(W) = 1 - (
  sum(rate(http_requests_total{code!~"5.."}[W]))
  /
  sum(rate(http_requests_total[W]))
)

# Burn rate over window W  
burn_rate(W) = error_rate(W) / 0.001

# Page alert (both windows must exceed threshold)
(burn_rate("1h") >= 14.4) AND (burn_rate("5h") >= 14.4)
```

slo-burn uses seconds in range vectors (e.g. `3600s` not `1h`) to avoid DST and
timezone surprises with `d` and `w` range modifiers in some Prometheus-compatible backends.

---

## Alert state machine

Each `(SLO, threshold)` pair is an independent two-state machine:

```
                  short AND long >= multiplier
    HEALTHY  ──────────────────────────────────▶  FIRING
       ▲                                              │
       └──────────────────────────────────────────────┘
                  short OR long < multiplier
```

**Transitions** — not ticks — trigger notifications. When the state changes from HEALTHY
to FIRING, slo-burn calls your configured notification targets (PagerDuty trigger, Slack
message, OpsGenie alert). When it changes from FIRING to HEALTHY, it calls resolve.

Every evaluation tick that doesn't change state is silent. No notification floods during
sustained incidents.

The deduplication key (`SHA-256("{slo_name}:{threshold_name}")[:24]`) is stable across
restarts — if slo-burn crashes mid-incident and restarts, PagerDuty will correlate the
new trigger with the existing open alert rather than creating a duplicate.

---

## Further reading

- [Google SRE Workbook, Ch. 5: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
  — the source of the algorithm
- [sloth](https://github.com/slok/sloth) — generates Prometheus recording + alert rules
  (different approach: you still need AlertManager)
- [OpenSLO](https://openslo.com/) — vendor-neutral SLO definition spec
