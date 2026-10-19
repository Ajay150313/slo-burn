# Copyright 2026 Ajay Mulukutla (Ajay150313)
# Apache-2.0 License — see LICENSE in the project root


"""
Alert routing: PagerDuty Events v2, Slack Incoming Webhooks, OpsGenie Alerts v2.

Notifications are sent on state *transitions* only — HEALTHY→FIRING and
FIRING→RESOLVED. Every evaluation tick that doesn't change state is silent.

The deduplication key is a hash of "{slo_name}:{threshold_name}" so that if
slo-burn restarts mid-incident, PagerDuty/OpsGenie can correlate the new
trigger with the existing open alert and not create a duplicate.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..core.models import AlertSeverity, AlertState, BurnRateStatus

log = logging.getLogger(__name__)


def _dedup_key(state: AlertState) -> str:
    raw = f"{state.slo_name}:{state.threshold.name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _summary(state: AlertState) -> str:
    action = "FIRING" if state.is_firing else "RESOLVED"
    speed  = "fast" if state.threshold.multiplier >= 10 else "slow"
    return (
        f"[{action}] {state.slo_name}: {speed} burn at "
        f"{state.short_reading.burn_rate:.1f}× "
        f"(threshold {state.threshold.multiplier}×)"
    )


class AlertRouter:
    """Routes alert state transitions to notification channels.

    Only fires on transitions — not on every evaluation tick.
    Tracks last-sent state per fingerprint to detect transitions.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        self._last_status: dict[str, BurnRateStatus] = {}

    def has_transitioned(self, state: AlertState) -> bool:
        """Return True if this state is different from the last time we notified."""
        prev = self._last_status.get(state.fingerprint)
        return prev != state.status

    def record(self, state: AlertState) -> None:
        """Update internal state after a notification has been sent."""
        self._last_status[state.fingerprint] = state.status

    async def route(self, state: AlertState) -> None:
        """Record state transition (without sending — caller does the sending)."""
        if self.has_transitioned(state):
            self.record(state)

    async def route_all(self, states: list[AlertState]) -> None:
        for state in states:
            try:
                await self.route(state)
            except Exception as exc:
                log.error("route failed for %s: %s", state.fingerprint, exc)

    # ── PagerDuty Events API v2 ───────────────────────────────────────────

    async def send_to_pagerduty(
        self,
        state:           AlertState,
        integration_key: str,
        severity_map:    dict[str, str] | None = None,
    ) -> None:
        """Send a PagerDuty Events API v2 trigger or resolve."""
        sm = severity_map or {
            AlertSeverity.PAGE.value:   "critical",
            AlertSeverity.TICKET.value: "warning",
            AlertSeverity.INFO.value:   "info",
        }
        payload: dict[str, Any] = {
            "routing_key":  integration_key,
            "event_action": "trigger" if state.is_firing else "resolve",
            "dedup_key":    _dedup_key(state),
            "payload": {
                "summary":   _summary(state),
                "severity":  sm.get(state.severity.value, "warning"),
                "source":    f"slo-burn/{state.slo_name}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "custom_details": {
                    "slo":              state.slo_name,
                    "threshold":        state.threshold.name,
                    "short_window":     state.threshold.short_window.label,
                    "short_burn_rate":  round(state.short_reading.burn_rate, 3),
                    "long_window":      state.threshold.long_window.label,
                    "long_burn_rate":   round(state.long_reading.burn_rate, 3),
                    "multiplier":       state.threshold.multiplier,
                    "sli_pct":          round(state.short_reading.sli, 5),
                },
            },
        }
        try:
            r = await self._client.post(
                "https://events.pagerduty.com/v2/enqueue", json=payload
            )
            r.raise_for_status()
            log.info("PagerDuty %s sent for %s", payload["event_action"], state.fingerprint)
        except httpx.HTTPStatusError as exc:
            log.error("PagerDuty HTTP %d: %s", exc.response.status_code, exc)
        except httpx.RequestError as exc:
            log.error("PagerDuty request failed: %s", exc)

    # ── Slack Incoming Webhooks ────────────────────────────────────────────

    async def send_to_slack(
        self,
        state:       AlertState,
        webhook_url: str,
        channel:     str | None = None,
    ) -> None:
        """Send a Slack message via Incoming Webhook."""
        if state.is_firing:
            color = "#E01E5A" if state.severity == AlertSeverity.PAGE else "#ECB22E"
            icon  = "🔥"
        else:
            color = "#2EB67D"
            icon  = "✅"

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{icon} *{_summary(state)}*"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*SLO*\n{state.slo_name}"},
                    {"type": "mrkdwn", "text": f"*Severity*\n{state.severity.value.upper()}"},
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*{state.threshold.short_window.label} burn rate*\n"
                            f"{state.short_reading.burn_rate:.2f}×"
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*{state.threshold.long_window.label} burn rate*\n"
                            f"{state.long_reading.burn_rate:.2f}×"
                        ),
                    },
                    {"type": "mrkdwn", "text": f"*SLI*\n{state.short_reading.sli:.4f}%"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Threshold*\n{state.threshold.multiplier}×",
                    },
                ],
            },
        ]

        body: dict[str, Any] = {"attachments": [{"color": color, "blocks": blocks}]}
        if channel:
            body["channel"] = channel

        try:
            r = await self._client.post(webhook_url, json=body)
            r.raise_for_status()
            log.info("Slack notification sent for %s", state.fingerprint)
        except Exception as exc:
            log.error("Slack notification failed: %s", exc)

    # ── OpsGenie Alerts API v2 ─────────────────────────────────────────────

    async def send_to_opsgenie(
        self,
        state:   AlertState,
        api_key: str,
        team:    str | None = None,
    ) -> None:
        """Create or close an OpsGenie alert."""
        headers = {
            "Authorization": f"GenieKey {api_key}",
            "Content-Type": "application/json",
        }
        alias = _dedup_key(state)

        if state.is_firing:
            priority_map = {
                AlertSeverity.PAGE:   "P1",
                AlertSeverity.TICKET: "P3",
                AlertSeverity.INFO:   "P5",
            }
            body: dict[str, Any] = {
                "message":  _summary(state),
                "alias":    alias,
                "priority": priority_map.get(state.severity, "P3"),
                "details": {
                    "slo":             state.slo_name,
                    "burn_rate_short": str(round(state.short_reading.burn_rate, 3)),
                    "burn_rate_long":  str(round(state.long_reading.burn_rate,  3)),
                    "multiplier":      str(state.threshold.multiplier),
                },
            }
            if team:
                body["responders"] = [{"name": team, "type": "team"}]
            try:
                r = await self._client.post(
                    "https://api.opsgenie.com/v2/alerts", json=body, headers=headers
                )
                r.raise_for_status()
                log.info("OpsGenie alert created for %s", state.fingerprint)
            except Exception as exc:
                log.error("OpsGenie create failed: %s", exc)
        else:
            try:
                r = await self._client.post(
                    f"https://api.opsgenie.com/v2/alerts/{alias}/close",
                    json={},
                    headers=headers,
                )
                r.raise_for_status()
                log.info("OpsGenie alert closed for %s", state.fingerprint)
            except Exception as exc:
                log.error("OpsGenie close failed: %s", exc)

    async def close(self) -> None:
        await self._client.aclose()
