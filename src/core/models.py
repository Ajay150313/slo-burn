# Copyright 2026 Ajay Mulukutla (Ajay150313)
# MIT License — see LICENSE in the project root

# https://www.apache.org/licenses/LICENSE-2.0

"""
Core data models: SLO definitions, burn rate readings, alert states, error budgets.

Design notes
------------
Everything here is a plain dataclass — no ORM, no magic, no base classes.
The goal is that you can read any model definition and immediately understand
what it holds and why. If a field needs explaining, there's a comment.

The Window class handles the one genuinely fiddly thing: time duration parsing.
We accept human strings ("1h", "3d", "30m") because that's what you type in YAML,
and convert to seconds because that's what PromQL and everything else wants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class AlertSeverity(str, Enum):
    """How urgently a firing alert needs attention."""
    PAGE   = "page"    # wake someone up right now
    TICKET = "ticket"  # needs fixing this sprint, not at 3am
    INFO   = "info"    # log and move on, no human action needed


class BurnRateStatus(str, Enum):
    """The current health state of a single (SLO, threshold) pair."""
    HEALTHY   = "healthy"    # burning within budget
    WARNING   = "warning"    # slow burn — ticket severity
    CRITICAL  = "critical"   # fast burn — page severity
    EXHAUSTED = "exhausted"  # error budget fully consumed


@dataclass
class Window:
    """A time duration used in burn rate calculations.

    Accepts human-readable strings so YAML configs stay readable:
        Window.from_str("1h")   → 3600s
        Window.from_str("3d")   → 259200s
        Window.from_str("30m")  → 1800s
        Window.from_str("1h30m")→ 5400s
    """
    duration: timedelta
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self._auto_label()

    def _auto_label(self) -> str:
        total = int(self.duration.total_seconds())
        d = total // 86400
        h = (total % 86400) // 3600
        m = (total % 3600) // 60
        s = total % 60
        parts = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        if s: parts.append(f"{s}s")
        return "".join(parts) or "0s"

    @classmethod
    def from_str(cls, s: str) -> "Window":
        """Parse a duration string. Raises ValueError on invalid input."""
        s = s.strip()
        pat = re.compile(
            r"^(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?$"
        )
        match = pat.fullmatch(s)
        if not match or not any(match.groups()):
            raise ValueError(
                f"Cannot parse duration {s!r}. "
                f"Expected format like '1h', '3d', '30m', '1h30m'."
            )
        td = timedelta(
            days=int(match.group("d") or 0),
            hours=int(match.group("h") or 0),
            minutes=int(match.group("m") or 0),
            seconds=int(match.group("s") or 0),
        )
        if td.total_seconds() == 0:
            raise ValueError(f"Duration {s!r} evaluates to zero seconds.")
        return cls(duration=td, label=s)

    @property
    def seconds(self) -> int:
        return int(self.duration.total_seconds())

    @property
    def prometheus_range(self) -> str:
        """PromQL range string, e.g. '3600s'. Always in seconds to avoid
        timezone and DST surprises with 'd' and 'w' range units."""
        return f"{self.seconds}s"


@dataclass
class BurnRateThreshold:
    """Defines when a (slo, threshold) pair should fire an alert.

    The dual-window requirement is the key property: BOTH short_window AND
    long_window burn rates must exceed the multiplier for the alert to fire.
    This prevents transient spikes from generating pages.

    See docs/algorithm.md for the full derivation.
    """
    severity:     AlertSeverity
    short_window: Window   # fast detection (e.g. 1h)
    long_window:  Window   # spike rejection (e.g. 5h)
    multiplier:   float    # e.g. 14.4 means 14.4× the sustainable burn rate

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ValueError(
                f"Burn rate multiplier must be positive, got {self.multiplier}"
            )
        if self.short_window.seconds >= self.long_window.seconds:
            raise ValueError(
                f"short_window ({self.short_window.label}) must be shorter than "
                f"long_window ({self.long_window.label})"
            )

    @property
    def name(self) -> str:
        speed = "fast" if self.multiplier >= 10 else "slow"
        return f"{speed}-burn-{self.short_window.label}-{self.long_window.label}"


@dataclass
class SLOIndicator:
    """The PromQL (or backend-equivalent) queries that measure SLO compliance.

    Uses the good/total event model from the Google SRE Workbook.
    The placeholder {window} is substituted at evaluation time.

    Example:
        good_query  = "sum(rate(http_requests_total{code!~'5..'}[{window}]))"
        total_query = "sum(rate(http_requests_total[{window}]))"
    """
    good_query:  str
    total_query: str

    def render_good(self, window: Window) -> str:
        return self.good_query.replace("{window}", window.prometheus_range)

    def render_total(self, window: Window) -> str:
        return self.total_query.replace("{window}", window.prometheus_range)


@dataclass
class NotificationTarget:
    """Where to send alert notifications."""
    type:           str                              # pagerduty | slack | opsgenie | webhook
    config:         dict                             # type-specific config
    severity_filter: Optional[list[AlertSeverity]]  # None = send all severities


@dataclass
class SLO:
    """A complete SLO definition."""
    name:        str
    target:      float          # e.g. 99.9 (percent, not ratio)
    window_days: int            # rolling compliance window
    indicator:   SLOIndicator
    thresholds:  list[BurnRateThreshold] = field(default_factory=list)
    notify:      list[NotificationTarget] = field(default_factory=list)
    description: str  = ""
    backend:     str  = "prometheus"
    labels:      dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0 < self.target < 100):
            raise ValueError(
                f"SLO target must be between 0 and 100 (exclusive), got {self.target}"
            )
        if self.window_days <= 0:
            raise ValueError(
                f"window_days must be positive, got {self.window_days}"
            )

    @property
    def error_rate_threshold(self) -> float:
        """Maximum allowed error rate as a ratio (0..1).
        For a 99.9% SLO this is 0.001."""
        return 1.0 - (self.target / 100.0)

    @property
    def compliance_window(self) -> timedelta:
        return timedelta(days=self.window_days)

    @property
    def total_error_budget_minutes(self) -> float:
        """Total allowed downtime in minutes over the compliance window.
        For 99.9% over 30 days: 0.001 × 30 × 24 × 60 = 43.2 minutes."""
        return self.window_days * 24 * 60 * self.error_rate_threshold

    @classmethod
    def with_google_defaults(cls, **kwargs: object) -> "SLO":
        """Create an SLO pre-loaded with the Google SRE Workbook recommended thresholds.

        These are calibrated for 30-day windows. They provide:
          - Fast burn (14.4×): 5-minute detection, ~1% false positive rate
          - Slow burn (3×):    catches budget exhaustion within 10 days

        Reference: https://sre.google/workbook/alerting-on-slos/ Table 5
        """
        slo = cls(**kwargs)  # type: ignore[arg-type]
        slo.thresholds = [
            BurnRateThreshold(
                severity=AlertSeverity.PAGE,
                short_window=Window.from_str("1h"),
                long_window=Window.from_str("5h"),
                multiplier=14.4,
            ),
            BurnRateThreshold(
                severity=AlertSeverity.TICKET,
                short_window=Window.from_str("6h"),
                long_window=Window.from_str("3d"),
                multiplier=3.0,
            ),
        ]
        return slo


@dataclass
class BurnRateReading:
    """A single burn rate measurement at a point in time for one time window."""
    slo_name:    str
    window:      Window
    burn_rate:   float    # current burn rate; 1.0 = sustainable, 14.4 = exhausting 30d budget in 50h
    error_rate:  float    # raw error rate (0..1)
    good_count:  float    # good events over the window
    total_count: float    # total events over the window
    evaluated_at: datetime
    backend:     str = ""

    @property
    def is_valid(self) -> bool:
        """False when there was no traffic — avoids division-by-zero errors upstream."""
        return self.total_count > 0

    @property
    def sli(self) -> float:
        """Current SLI as a percentage. Returns 100.0 when there is no traffic."""
        if self.total_count == 0:
            return 100.0
        return (self.good_count / self.total_count) * 100.0


@dataclass
class AlertState:
    """The current firing/resolved state of a (SLO, threshold) pair.

    One AlertState exists per (SLO, threshold) combination. State transitions
    — HEALTHY→FIRING and FIRING→RESOLVED — are what trigger notifications.
    We don't notify on every evaluation tick.
    """
    slo_name:      str
    threshold:     BurnRateThreshold
    status:        BurnRateStatus
    short_reading: BurnRateReading
    long_reading:  BurnRateReading
    fired_at:      Optional[datetime] = None
    resolved_at:   Optional[datetime] = None
    fingerprint:   str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = f"{self.slo_name}:{self.threshold.name}"

    @property
    def is_firing(self) -> bool:
        return self.status in (BurnRateStatus.CRITICAL, BurnRateStatus.WARNING)

    @property
    def severity(self) -> AlertSeverity:
        return self.threshold.severity

    @property
    def duration(self) -> Optional[timedelta]:
        if self.fired_at is None:
            return None
        end = self.resolved_at or datetime.utcnow()
        return end - self.fired_at


@dataclass
class ErrorBudgetStatus:
    """Current error budget health for one SLO."""
    slo:                       SLO
    evaluated_at:              datetime
    budget_consumed_pct:       float             # 0–100+ (can exceed 100 when exhausted)
    budget_remaining_minutes:  float
    current_burn_rate:         float
    projected_exhaustion:      Optional[datetime]  # None if burn rate ≤ 1.0

    @property
    def is_exhausted(self) -> bool:
        return self.budget_consumed_pct >= 100.0

    @property
    def health(self) -> BurnRateStatus:
        if self.is_exhausted:
            return BurnRateStatus.EXHAUSTED
        if self.current_burn_rate >= 14.4:
            return BurnRateStatus.CRITICAL
        if self.current_burn_rate >= 3.0:
            return BurnRateStatus.WARNING
        return BurnRateStatus.HEALTHY
