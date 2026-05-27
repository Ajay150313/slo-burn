# Copyright 2026 Ajay Mulukutla (Ajay150313)
# MIT License — see LICENSE in the project root


"""
Multi-window burn rate evaluator.

This is the part that actually does the work: given an SLO and a metrics
backend, compute burn rates across every configured time window and determine
which thresholds are breached.

The dual-window check is the critical property. For a threshold to fire,
BOTH the short window AND the long window burn rate must exceed the multiplier.
A spike that raises the 1h window but not the 5h window does not page anyone.
A sustained burn shows up in both windows and fires immediately.

Reference: https://sre.google/workbook/alerting-on-slos/#6-multiwindow-multi-burn-rate-alerts
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from .models import (
    AlertState,
    BurnRateReading,
    BurnRateStatus,
    BurnRateThreshold,
    ErrorBudgetStatus,
    SLO,
    Window,
)

log = logging.getLogger(__name__)


@runtime_checkable
class MetricsBackend(Protocol):
    """Interface every metrics backend must satisfy.

    Backends execute a query against their data source and return a single
    float. If the query returns no data — service not yet instrumented,
    backend temporarily unreachable — they return None rather than raising.
    The evaluator treats None as zero traffic (healthy, not erroring).
    """

    async def query_scalar(self, query: str) -> float | None:
        """Run an instant query. Return a scalar or None."""
        ...

    async def health_check(self) -> bool:
        """Return True if the backend is reachable."""
        ...

    @property
    def name(self) -> str:
        ...


class BurnRateEvaluator:
    """Evaluates burn rates and manages alert state for a set of SLOs.

    Maintains in-memory alert state across evaluation cycles so it can
    detect transitions (HEALTHY → FIRING, FIRING → RESOLVED) and avoid
    sending duplicate notifications on every tick.

    Usage::

        backend   = PrometheusBackend(url="http://prometheus:9090")
        evaluator = BurnRateEvaluator(backend=backend)

        states = await evaluator.evaluate_all(slos)
        for state in states:
            if state.is_firing:
                await router.send_to_pagerduty(state, key=PD_KEY)
    """

    def __init__(
        self,
        backend: MetricsBackend,
        evaluation_interval_seconds: int = 60,
    ) -> None:
        self._backend  = backend
        self._interval = evaluation_interval_seconds
        # Keyed by fingerprint = "{slo_name}:{threshold_name}"
        self._previous_states: dict[str, AlertState] = {}

    async def evaluate_slo(self, slo: SLO) -> list[AlertState]:
        """Evaluate all thresholds for one SLO. Returns one AlertState per threshold."""
        results: list[AlertState] = []
        for threshold in slo.thresholds:
            try:
                state = await self._check_threshold(slo, threshold)
                results.append(state)
            except Exception:
                log.exception(
                    "Failed evaluating threshold %s for SLO %s",
                    threshold.name, slo.name
                )
        return results

    async def evaluate_all(self, slos: list[SLO]) -> list[AlertState]:
        """Evaluate all SLOs concurrently. Failures in one SLO don't block others."""
        task_results = await asyncio.gather(
            *[self.evaluate_slo(slo) for slo in slos],
            return_exceptions=True,
        )
        states: list[AlertState] = []
        for slo, result in zip(slos, task_results):
            if isinstance(result, Exception):
                log.error("evaluate_slo failed for %s: %s", slo.name, result)
                continue
            states.extend(result)
        return states

    async def compute_error_budget(self, slo: SLO) -> ErrorBudgetStatus:
        """Compute current error budget consumption over the full compliance window."""
        window   = Window(duration=slo.compliance_window)
        reading  = await self._query_window(slo, window)
        now      = datetime.utcnow()

        if not reading.is_valid:
            return ErrorBudgetStatus(
                slo=slo,
                evaluated_at=now,
                budget_consumed_pct=0.0,
                budget_remaining_minutes=slo.total_error_budget_minutes,
                current_burn_rate=0.0,
                projected_exhaustion=None,
            )

        # Fraction of the error rate threshold we've consumed
        consumed_pct = (reading.error_rate / slo.error_rate_threshold) * 100.0
        remaining    = max(0.0, slo.total_error_budget_minutes * (1.0 - consumed_pct / 100.0))

        # Burn rate relative to the window (1.0 = right on track to exhaust at end of window)
        burn_rate = consumed_pct / 100.0

        projected: datetime | None = None
        if burn_rate > 1.0 and remaining > 0:
            mins_until_exhaustion = remaining / burn_rate
            projected = now + timedelta(minutes=mins_until_exhaustion)

        return ErrorBudgetStatus(
            slo=slo,
            evaluated_at=now,
            budget_consumed_pct=consumed_pct,
            budget_remaining_minutes=remaining,
            current_burn_rate=burn_rate,
            projected_exhaustion=projected,
        )

    # ── internals ────────────────────────────────────────────────────────────

    async def _check_threshold(
        self, slo: SLO, threshold: BurnRateThreshold
    ) -> AlertState:
        short_r, long_r = await asyncio.gather(
            self._query_window(slo, threshold.short_window),
            self._query_window(slo, threshold.long_window),
        )

        firing = (
            short_r.is_valid and short_r.burn_rate >= threshold.multiplier
            and
            long_r.is_valid  and long_r.burn_rate  >= threshold.multiplier
        )

        if threshold.severity.value == "page":
            status = BurnRateStatus.CRITICAL if firing else BurnRateStatus.HEALTHY
        else:
            status = BurnRateStatus.WARNING  if firing else BurnRateStatus.HEALTHY

        fp  = f"{slo.name}:{threshold.name}"
        now = datetime.utcnow()
        prev = self._previous_states.get(fp)

        was_firing = prev is not None and prev.is_firing

        if firing and not was_firing:
            fired_at    = now
            resolved_at = None
            log.warning(
                "FIRING  %s | %s | threshold=%.1f× | "
                "short(%s)=%.2f× | long(%s)=%.2f×",
                slo.name, threshold.name, threshold.multiplier,
                threshold.short_window.label, short_r.burn_rate,
                threshold.long_window.label,  long_r.burn_rate,
            )
        elif not firing and was_firing:
            fired_at    = prev.fired_at     # type: ignore[union-attr]
            resolved_at = now
            log.info("RESOLVED %s | %s", slo.name, threshold.name)
        else:
            fired_at    = prev.fired_at    if prev else None
            resolved_at = prev.resolved_at if prev else None

        state = AlertState(
            slo_name=slo.name,
            threshold=threshold,
            status=status,
            short_reading=short_r,
            long_reading=long_r,
            fired_at=fired_at,
            resolved_at=resolved_at,
            fingerprint=fp,
        )
        self._previous_states[fp] = state
        return state

    async def _query_window(self, slo: SLO, window: Window) -> BurnRateReading:
        good_q  = slo.indicator.render_good(window)
        total_q = slo.indicator.render_total(window)

        good_val, total_val = await asyncio.gather(
            self._backend.query_scalar(good_q),
            self._backend.query_scalar(total_q),
        )

        good  = good_val  or 0.0
        total = total_val or 0.0

        error_rate = max(0.0, 1.0 - (good / total)) if total > 0 else 0.0
        burn_rate  = (error_rate / slo.error_rate_threshold) if slo.error_rate_threshold > 0 else 0.0

        return BurnRateReading(
            slo_name=slo.name,
            window=window,
            burn_rate=burn_rate,
            error_rate=error_rate,
            good_count=good,
            total_count=total,
            evaluated_at=datetime.utcnow(),
            backend=self._backend.name,
        )
