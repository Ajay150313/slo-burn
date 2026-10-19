# Copyright 2026 Ajay Mulukutla (Ajay150313)
# Apache-2.0 License — see LICENSE in the project root


"""
Prometheus text-format metrics exporter and self-contained HTML dashboard.

No Grafana required. The dashboard is intentionally plain HTML — it loads in
under 100ms and has no JavaScript dependencies that can break at 3am.
"""

from __future__ import annotations

import time
from datetime import datetime

from ..core.models import AlertState, BurnRateStatus, ErrorBudgetStatus, SLO


# ── Prometheus text format ────────────────────────────────────────────────────

def _lset(**kv: str) -> str:
    parts = ", ".join(f'{k}="{v}"' for k, v in kv.items())
    return "{" + parts + "}"


def render_prometheus_metrics(
    states:                      list[AlertState],
    budgets:                     list[ErrorBudgetStatus],
    evaluation_duration_seconds: float,
) -> str:
    lines: list[str] = []

    lines += [
        "# HELP slo_burn_rate Current burn rate relative to sustainable rate (1.0 = on track to exhaust budget at end of window)",
        "# TYPE slo_burn_rate gauge",
    ]
    for s in states:
        l = _lset(slo=s.slo_name, window=s.threshold.short_window.label, severity=s.severity.value)
        lines.append(f"slo_burn_rate{l} {s.short_reading.burn_rate:.6f}")

    lines += [
        "",
        "# HELP slo_alert_firing 1 if this burn rate alert is currently firing",
        "# TYPE slo_alert_firing gauge",
    ]
    for s in states:
        l = _lset(slo=s.slo_name, threshold=s.threshold.name, severity=s.severity.value)
        lines.append(f"slo_alert_firing{l} {1 if s.is_firing else 0}")

    lines += [
        "",
        "# HELP slo_sli_current Current SLI value as a percentage",
        "# TYPE slo_sli_current gauge",
    ]
    seen: set[str] = set()
    for s in states:
        if s.slo_name not in seen:
            seen.add(s.slo_name)
            l = _lset(slo=s.slo_name, window=s.threshold.short_window.label)
            lines.append(f"slo_sli_current{l} {s.short_reading.sli:.6f}")

    lines += [
        "",
        "# HELP slo_error_budget_consumed_ratio Fraction of error budget consumed (0–1+)",
        "# TYPE slo_error_budget_consumed_ratio gauge",
    ]
    for b in budgets:
        l = _lset(slo=b.slo.name, target=str(b.slo.target))
        lines.append(f"slo_error_budget_consumed_ratio{l} {b.budget_consumed_pct / 100.0:.6f}")

    lines += [
        "",
        "# HELP slo_error_budget_remaining_minutes Minutes of error budget remaining",
        "# TYPE slo_error_budget_remaining_minutes gauge",
    ]
    for b in budgets:
        l = _lset(slo=b.slo.name)
        lines.append(f"slo_error_budget_remaining_minutes{l} {b.budget_remaining_minutes:.2f}")

    lines += [
        "",
        f"# HELP slo_evaluation_duration_seconds Seconds taken to evaluate all SLOs",
        f"# TYPE slo_evaluation_duration_seconds gauge",
        f"slo_evaluation_duration_seconds {evaluation_duration_seconds:.4f}",
        "",
        f"# HELP slo_evaluation_timestamp_seconds Unix timestamp of last evaluation",
        f"# TYPE slo_evaluation_timestamp_seconds gauge",
        f"slo_evaluation_timestamp_seconds {time.time():.0f}",
        "",
    ]
    return "\n".join(lines)


# ── HTML dashboard ────────────────────────────────────────────────────────────

def render_dashboard_html(
    states:       list[AlertState],
    budgets:      list[ErrorBudgetStatus],
    evaluated_at: datetime,
) -> str:
    firing_count = sum(1 for s in states if s.is_firing)

    status_styles = {
        BurnRateStatus.HEALTHY:   ("#22c55e", "● Healthy"),
        BurnRateStatus.WARNING:   ("#f59e0b", "▲ Warning"),
        BurnRateStatus.CRITICAL:  ("#ef4444", "🔥 Critical"),
        BurnRateStatus.EXHAUSTED: ("#a855f7", "✕ Exhausted"),
    }

    cards = ""
    for budget in budgets:
        slo_states = [s for s in states if s.slo_name == budget.slo.name]
        color, label = status_styles.get(budget.health, ("#6b7280", budget.health.value))

        bar_pct = min(budget.budget_consumed_pct, 100)
        bar_color = "#22c55e" if bar_pct < 50 else ("#f59e0b" if bar_pct < 80 else "#ef4444")

        proj = ""
        if budget.projected_exhaustion:
            proj = (
                f'<p style="color:#f59e0b;font-size:12px;margin:8px 0 0">'
                f'⚠ Budget exhaustion projected: '
                f'{budget.projected_exhaustion.strftime("%Y-%m-%d %H:%M UTC")}'
                f'</p>'
            )

        rows = ""
        for s in slo_states:
            icon = "🔥" if s.is_firing else "✅"
            rows += (
                f"<tr>"
                f'<td style="padding:4px 8px;color:#94a3b8">{s.threshold.name}</td>'
                f'<td style="padding:4px 8px;text-align:right">{s.short_reading.burn_rate:.2f}×</td>'
                f'<td style="padding:4px 8px;text-align:right">{s.long_reading.burn_rate:.2f}×</td>'
                f'<td style="padding:4px 8px;text-align:center">{icon}</td>'
                f"</tr>"
            )

        cards += f"""
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:20px;margin-bottom:16px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <div style="font-size:15px;font-weight:700;color:#f1f5f9">{budget.slo.name}</div>
      <div style="color:#64748b;font-size:12px;margin-top:2px">{budget.slo.description}</div>
    </div>
    <div style="text-align:right">
      <span style="color:{color};font-weight:600;font-size:14px">{label}</span>
      <div style="color:#475569;font-size:11px;margin-top:2px">target {budget.slo.target}%</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px">
    <div style="background:#1e293b;border-radius:6px;padding:12px">
      <div style="color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.8px">Budget used</div>
      <div style="font-size:22px;font-weight:700;color:#f1f5f9;margin-top:4px">{budget.budget_consumed_pct:.1f}%</div>
      <div style="background:#0f172a;border-radius:3px;height:6px;margin-top:6px">
        <div style="background:{bar_color};width:{bar_pct:.1f}%;height:100%;border-radius:3px"></div>
      </div>
    </div>
    <div style="background:#1e293b;border-radius:6px;padding:12px">
      <div style="color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.8px">Remaining</div>
      <div style="font-size:22px;font-weight:700;color:#f1f5f9;margin-top:4px">{budget.budget_remaining_minutes:.0f}m</div>
      <div style="color:#475569;font-size:11px;margin-top:4px">of {budget.slo.total_error_budget_minutes:.0f}m total</div>
    </div>
    <div style="background:#1e293b;border-radius:6px;padding:12px">
      <div style="color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.8px">Burn rate</div>
      <div style="font-size:22px;font-weight:700;color:#f1f5f9;margin-top:4px">{budget.current_burn_rate:.2f}×</div>
      <div style="color:#475569;font-size:11px;margin-top:4px">1.0 = sustainable</div>
    </div>
  </div>
  {proj}
  <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:12px">
    <tr style="color:#475569;border-bottom:1px solid #1e293b">
      <th style="text-align:left;padding:4px 8px;font-weight:500">Alert</th>
      <th style="text-align:right;padding:4px 8px;font-weight:500">Short</th>
      <th style="text-align:right;padding:4px 8px;font-weight:500">Long</th>
      <th style="text-align:center;padding:4px 8px;font-weight:500">State</th>
    </tr>
    {rows}
  </table>
</div>"""

    header_color = "#ef4444" if firing_count > 0 else "#22c55e"
    header_msg   = f"🔥 {firing_count} firing" if firing_count > 0 else "✅ All healthy"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>slo-burn</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#020817;color:#f1f5f9;min-height:100vh}}</style>
</head>
<body>
<div style="max-width:880px;margin:0 auto;padding:24px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid #1e293b">
    <div>
      <div style="font-size:20px;font-weight:800;letter-spacing:-0.5px">🔥 slo-burn</div>
      <div style="color:#475569;font-size:12px;margin-top:2px">multi-window SLO burn rate</div>
    </div>
    <div style="text-align:right">
      <div style="color:{header_color};font-weight:600">{header_msg}</div>
      <div style="color:#475569;font-size:11px;margin-top:2px">{evaluated_at.strftime("%H:%M:%S UTC")} · refresh 60s</div>
    </div>
  </div>
  {cards}
  <div style="text-align:center;margin-top:20px;font-size:11px">
    <a href="/metrics" style="color:#475569;text-decoration:none">Prometheus metrics</a>
    <span style="color:#334155;margin:0 8px">·</span>
    <a href="/api/status" style="color:#475569;text-decoration:none">JSON status</a>
    <span style="color:#334155;margin:0 8px">·</span>
    <a href="/api/budget" style="color:#475569;text-decoration:none">JSON budget</a>
  </div>
</div>
</body>
</html>"""
