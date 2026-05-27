# Copyright 2026 Ajay Mulukutla (Ajay150313)
# MIT License — see LICENSE in the project root


"""
Tests for the core slo-burn engine.

Run with: pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import os
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, ".")

from src.core.models import (
    AlertSeverity,
    AlertState,
    BurnRateReading,
    BurnRateStatus,
    BurnRateThreshold,
    ErrorBudgetStatus,
    SLO,
    SLOIndicator,
    Window,
)
from src.core.evaluator import BurnRateEvaluator
from src.rules.templates import get_template, list_templates, TEMPLATES
from src.dashboard.server import render_prometheus_metrics, render_dashboard_html


# ── helpers ───────────────────────────────────────────────────────────────────

def indicator() -> SLOIndicator:
    return SLOIndicator(
        good_query="sum(rate(http_requests_total{code!~'5..'}[{window}]))",
        total_query="sum(rate(http_requests_total[{window}]))",
    )

def slo_99_9(name: str = "test-slo") -> SLO:
    return SLO.with_google_defaults(
        name=name, target=99.9, window_days=30, indicator=indicator()
    )

def mock_backend(good: float = 1000.0, total: float = 1000.0) -> AsyncMock:
    b = AsyncMock()
    b.name = "mock"
    b.health_check.return_value = True
    b.query_scalar.return_value = None  # overridden per-test
    return b

def reading(slo_name: str, burn: float, window: str = "1h") -> BurnRateReading:
    return BurnRateReading(
        slo_name=slo_name,
        window=Window.from_str(window),
        burn_rate=burn,
        error_rate=burn * 0.001,
        good_count=1000.0 - burn * 1.0,
        total_count=1000.0,
        evaluated_at=datetime.utcnow(),
    )

def make_state(
    slo_name:  str = "slo",
    is_firing: bool = False,
    severity:  AlertSeverity = AlertSeverity.PAGE,
    burn:      float = 1.0,
) -> AlertState:
    t = BurnRateThreshold(
        severity=severity,
        short_window=Window.from_str("1h"),
        long_window=Window.from_str("5h"),
        multiplier=14.4,
    )
    r = reading(slo_name, burn)
    return AlertState(
        slo_name=slo_name,
        threshold=t,
        status=BurnRateStatus.CRITICAL if is_firing else BurnRateStatus.HEALTHY,
        short_reading=r,
        long_reading=r,
        fired_at=datetime.utcnow() if is_firing else None,
    )


# ── Window ────────────────────────────────────────────────────────────────────

class TestWindow:
    def test_hours(self):
        assert Window.from_str("1h").seconds == 3600

    def test_days(self):
        assert Window.from_str("3d").seconds == 3 * 86400

    def test_minutes(self):
        assert Window.from_str("30m").seconds == 1800

    def test_combined(self):
        assert Window.from_str("1h30m").seconds == 5400

    def test_prometheus_range_always_seconds(self):
        # Always seconds to avoid DST surprises with 'd' range vectors
        assert Window.from_str("1h").prometheus_range == "3600s"
        assert Window.from_str("3d").prometheus_range == "259200s"

    def test_auto_label(self):
        w = Window.from_str("6h")
        assert w.label == "6h"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Window.from_str("bananas")

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            Window.from_str("0h")


# ── SLO model ─────────────────────────────────────────────────────────────────

class TestSLO:
    def test_error_rate_threshold(self):
        assert abs(slo_99_9().error_rate_threshold - 0.001) < 1e-12

    def test_error_budget_minutes(self):
        # 30d × 24h × 60min × 0.001 = 43.2 minutes
        assert abs(slo_99_9().total_error_budget_minutes - 43.2) < 0.001

    def test_google_defaults_thresholds(self):
        slo = slo_99_9()
        assert len(slo.thresholds) == 2
        page   = next(t for t in slo.thresholds if t.severity == AlertSeverity.PAGE)
        ticket = next(t for t in slo.thresholds if t.severity == AlertSeverity.TICKET)
        assert page.multiplier   == 14.4
        assert ticket.multiplier == 3.0

    def test_target_above_100_raises(self):
        with pytest.raises(ValueError):
            SLO(name="x", target=100.0, window_days=30, indicator=indicator())

    def test_target_zero_raises(self):
        with pytest.raises(ValueError):
            SLO(name="x", target=0.0, window_days=30, indicator=indicator())

    def test_negative_window_raises(self):
        with pytest.raises(ValueError):
            SLO(name="x", target=99.9, window_days=0, indicator=indicator())


# ── BurnRateThreshold ─────────────────────────────────────────────────────────

class TestThreshold:
    def test_short_must_be_shorter_than_long(self):
        with pytest.raises(ValueError):
            BurnRateThreshold(
                severity=AlertSeverity.PAGE,
                short_window=Window.from_str("5h"),
                long_window=Window.from_str("1h"),
                multiplier=14.4,
            )

    def test_negative_multiplier_raises(self):
        with pytest.raises(ValueError):
            BurnRateThreshold(
                severity=AlertSeverity.PAGE,
                short_window=Window.from_str("1h"),
                long_window=Window.from_str("5h"),
                multiplier=-1.0,
            )

    def test_name_fast_burn(self):
        t = BurnRateThreshold(
            severity=AlertSeverity.PAGE,
            short_window=Window.from_str("1h"),
            long_window=Window.from_str("5h"),
            multiplier=14.4,
        )
        assert "fast" in t.name

    def test_name_slow_burn(self):
        t = BurnRateThreshold(
            severity=AlertSeverity.TICKET,
            short_window=Window.from_str("6h"),
            long_window=Window.from_str("3d"),
            multiplier=3.0,
        )
        assert "slow" in t.name


# ── BurnRateEvaluator ─────────────────────────────────────────────────────────

class TestEvaluator:
    @pytest.mark.asyncio
    async def test_healthy_when_no_errors(self):
        b = mock_backend()
        b.query_scalar.return_value = 1000.0  # 100% good
        slo    = slo_99_9()
        states = await BurnRateEvaluator(backend=b).evaluate_slo(slo)
        assert all(not s.is_firing for s in states)

    @pytest.mark.asyncio
    async def test_page_alert_fires_on_sustained_high_burn(self):
        # 99.9% SLO → error_rate_threshold = 0.001
        # 14.4× burn → error_rate = 0.0144 → good = 9856 / total = 10000
        count = 0
        async def q(query: str) -> float:
            nonlocal count
            count += 1
            return 9856.0 if count % 2 == 1 else 10000.0

        b = mock_backend()
        b.query_scalar.side_effect = q
        slo    = slo_99_9()
        states = await BurnRateEvaluator(backend=b).evaluate_slo(slo)

        page = [s for s in states if s.severity == AlertSeverity.PAGE]
        assert page[0].is_firing
        assert page[0].status == BurnRateStatus.CRITICAL

    @pytest.mark.asyncio
    async def test_no_fire_when_only_short_window_exceeds(self):
        """The dual-window guarantee: spike on short window alone does not page."""
        count = 0
        async def q(query: str) -> float:
            nonlocal count
            count += 1
            # First pair (short window): high burn
            if count <= 2:
                return 9856.0 if count % 2 == 1 else 10000.0
            # Second pair (long window): healthy
            return 9999.0 if count % 2 == 1 else 10000.0

        b = mock_backend()
        b.query_scalar.side_effect = q
        slo    = slo_99_9()
        states = await BurnRateEvaluator(backend=b).evaluate_slo(slo)

        page = [s for s in states if s.severity == AlertSeverity.PAGE]
        assert not page[0].is_firing, "Should not fire — long window did not exceed threshold"

    @pytest.mark.asyncio
    async def test_zero_traffic_is_healthy(self):
        b = mock_backend()
        b.query_scalar.return_value = 0.0
        states = await BurnRateEvaluator(backend=b).evaluate_slo(slo_99_9())
        assert all(not s.is_firing for s in states)

    @pytest.mark.asyncio
    async def test_evaluate_all_is_concurrent(self):
        b = mock_backend()
        b.query_scalar.return_value = 1000.0
        slos   = [slo_99_9(f"slo-{i}") for i in range(5)]
        states = await BurnRateEvaluator(backend=b).evaluate_all(slos)
        # 5 SLOs × 2 thresholds = 10 states
        assert len(states) == 10

    @pytest.mark.asyncio
    async def test_error_budget_exhaustion(self):
        b = mock_backend()
        # 100% error rate
        async def q(query: str) -> float:
            if "good" in query.lower() or "code!~" in query:
                return 0.0
            return 1000.0
        b.query_scalar.side_effect = q
        budget = await BurnRateEvaluator(backend=b).compute_error_budget(slo_99_9())
        assert budget.is_exhausted

    @pytest.mark.asyncio
    async def test_state_transition_tracking(self):
        """Evaluator must detect HEALTHY→FIRING transitions correctly."""
        count = 0
        async def high_burn(query: str) -> float:
            nonlocal count
            count += 1
            return 9856.0 if count % 2 == 1 else 10000.0

        b = mock_backend()
        b.query_scalar.side_effect = high_burn
        ev     = BurnRateEvaluator(backend=b)
        slo    = slo_99_9()

        states = await ev.evaluate_slo(slo)
        page1  = next(s for s in states if s.severity == AlertSeverity.PAGE)
        assert page1.is_firing
        assert page1.fired_at is not None

        # Second evaluation — still firing, fired_at should be preserved
        b.query_scalar.side_effect = high_burn
        states = await ev.evaluate_slo(slo)
        page2  = next(s for s in states if s.severity == AlertSeverity.PAGE)
        assert page2.fired_at == page1.fired_at  # same incident, not a new one


# ── Config loader ─────────────────────────────────────────────────────────────

class TestConfig:
    def test_load_valid_config(self, tmp_path):
        from src.core.config import load_config
        f = tmp_path / "slos.yaml"
        f.write_text("""
slos:
  - name: api
    target: 99.9
    window_days: 30
    indicators:
      good_query: "sum(rate(req{code!~'5..'}[{window}]))"
      total_query: "sum(rate(req[{window}]))"
    alerts:
      - severity: page
        short_window: 1h
        long_window: 5h
        multiplier: 14x
""")
        cfg = load_config(f)
        slo = cfg["slos"][0]
        assert slo.name   == "api"
        assert slo.target == 99.9
        assert slo.thresholds[0].multiplier == 14.0

    def test_duplicate_names_raises(self, tmp_path):
        from src.core.config import load_config
        f = tmp_path / "slos.yaml"
        f.write_text("""
slos:
  - name: api
    target: 99.9
    window_days: 30
    indicators:
      good_query: "x"
      total_query: "y"
  - name: api
    target: 99.5
    window_days: 30
    indicators:
      good_query: "x"
      total_query: "y"
""")
        with pytest.raises(ValueError, match="Duplicate"):
            load_config(f)

    def test_missing_file_in_validate(self, tmp_path):
        from src.core.config import validate_config
        errors = validate_config(tmp_path / "nonexistent.yaml")
        assert len(errors) > 0


# ── Templates ─────────────────────────────────────────────────────────────────

class TestTemplates:
    def test_all_templates_render(self):
        for name in TEMPLATES:
            out = get_template(name)
            assert "indicators" in out
            assert "good_query" in out

    def test_override_defaults(self):
        out = get_template("http-availability", service="checkout")
        assert "checkout" in out
        assert "my-service" not in out

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown template"):
            get_template("does-not-exist")


# ── Prometheus metrics rendering ──────────────────────────────────────────────

class TestPrometheusMetrics:
    def _budget(self) -> ErrorBudgetStatus:
        return ErrorBudgetStatus(
            slo=slo_99_9(),
            evaluated_at=datetime.utcnow(),
            budget_consumed_pct=30.0,
            budget_remaining_minutes=30.0,
            current_burn_rate=0.5,
            projected_exhaustion=None,
        )

    def test_required_metric_names_present(self):
        out = render_prometheus_metrics([make_state()], [self._budget()], 0.05)
        for metric in [
            "slo_burn_rate",
            "slo_alert_firing",
            "slo_sli_current",
            "slo_error_budget_consumed_ratio",
            "slo_error_budget_remaining_minutes",
            "slo_evaluation_duration_seconds",
        ]:
            assert metric in out, f"Missing metric: {metric}"

    def test_firing_alert_value_is_1(self):
        out = render_prometheus_metrics([make_state(is_firing=True)], [self._budget()], 0.1)
        for line in out.splitlines():
            if "slo_alert_firing{" in line:
                assert line.endswith(" 1")
                break

    def test_healthy_alert_value_is_0(self):
        out = render_prometheus_metrics([make_state(is_firing=False)], [self._budget()], 0.1)
        for line in out.splitlines():
            if "slo_alert_firing{" in line:
                assert line.endswith(" 0")
                break


# ── HTML dashboard rendering ──────────────────────────────────────────────────

class TestDashboard:
    def test_renders_without_error(self):
        budget = ErrorBudgetStatus(
            slo=slo_99_9("checkout"),
            evaluated_at=datetime.utcnow(),
            budget_consumed_pct=42.0,
            budget_remaining_minutes=25.0,
            current_burn_rate=0.7,
            projected_exhaustion=None,
        )
        html = render_dashboard_html([make_state("checkout")], [budget], datetime.utcnow())
        assert "checkout" in html
        assert "42.0%" in html
        assert "slo-burn" in html

    def test_firing_shows_critical(self):
        budget = ErrorBudgetStatus(
            slo=slo_99_9("broken"),
            evaluated_at=datetime.utcnow(),
            budget_consumed_pct=99.0,
            budget_remaining_minutes=0.4,
            current_burn_rate=15.0,
            projected_exhaustion=None,
        )
        html = render_dashboard_html([make_state("broken", is_firing=True)], [budget], datetime.utcnow())
        assert "Critical" in html
