# Copyright 2026 Ajay Mulukutla (Ajay150313)
# Apache-2.0 License — see LICENSE in the project root


"""
YAML configuration loader.

Parses slos.yaml into typed model objects. Supports ${VAR_NAME} interpolation
for secrets — never put API keys literally in config files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from ..core.models import (
    AlertSeverity,
    BurnRateThreshold,
    NotificationTarget,
    SLO,
    SLOIndicator,
    Window,
)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: str) -> str:
    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            raise ValueError(
                f"Environment variable ${{{var}}} is referenced in config but not set."
            )
        return val
    return _ENV_RE.sub(_replace, value)


def _expand(obj: Any) -> Any:
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(i) for i in obj]
    return obj


def _parse_multiplier(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return float(raw.lower().rstrip("x").strip())
    raise ValueError(f"Cannot parse multiplier from {raw!r}")


def _parse_threshold(raw: dict) -> BurnRateThreshold:  # type: ignore[type-arg]
    try:
        severity = AlertSeverity(raw.get("severity", "ticket").lower())
    except ValueError:
        valid = [s.value for s in AlertSeverity]
        raise ValueError(f"Invalid severity {raw.get('severity')!r}. Must be one of {valid}")

    return BurnRateThreshold(
        severity=severity,
        short_window=Window.from_str(raw.get("short_window", "1h")),
        long_window=Window.from_str(raw.get("long_window", "5h")),
        multiplier=_parse_multiplier(raw.get("multiplier", 14.4)),
    )


def _parse_notify(raw: dict) -> NotificationTarget:  # type: ignore[type-arg]
    ntype = raw.get("type", "").lower()
    if not ntype:
        # shorthand: {pagerduty: KEY} or {slack: URL}
        for known in ("pagerduty", "slack", "opsgenie", "webhook"):
            if known in raw:
                return NotificationTarget(type=known, config={known: raw[known]}, severity_filter=None)
        raise ValueError(f"Cannot determine notification type from: {raw}")

    sf_raw = raw.get("severity_filter")
    sf = [AlertSeverity(s.lower()) for s in sf_raw] if sf_raw else None
    config = {k: v for k, v in raw.items() if k not in ("type", "severity_filter")}
    return NotificationTarget(type=ntype, config=config, severity_filter=sf)


def _parse_slo(raw: dict) -> SLO:  # type: ignore[type-arg]
    name = raw.get("name")
    if not name:
        raise ValueError("Every SLO definition requires a 'name' field.")

    ind_raw = raw.get("indicators", {})
    if not ind_raw:
        raise ValueError(f"SLO {name!r} is missing the required 'indicators' section.")

    indicator = SLOIndicator(
        good_query=ind_raw.get("good_query", ""),
        total_query=ind_raw.get("total_query", ""),
    )

    alerts_raw = raw.get("alerts", [])
    if alerts_raw:
        thresholds = [_parse_threshold(t) for t in alerts_raw]
    else:
        # No alerts defined — use Google SRE Workbook defaults
        thresholds = SLO.with_google_defaults(
            name=name,
            target=float(raw.get("target", 99.9)),
            window_days=int(raw.get("window_days", 30)),
            indicator=indicator,
        ).thresholds

    return SLO(
        name=name,
        target=float(raw.get("target", 99.9)),
        window_days=int(raw.get("window_days", 30)),
        indicator=indicator,
        thresholds=thresholds,
        notify=[_parse_notify(n) for n in raw.get("notify", [])],
        description=raw.get("description", ""),
        backend=raw.get("backend", "prometheus"),
        labels=raw.get("labels", {}),
    )


def load_config(path: str | Path) -> dict:  # type: ignore[type-arg]
    """Load and parse a slos.yaml file.

    Returns::
        {
          "slos":     list[SLO],
          "backends": dict,   # raw backend config
          "server":   dict,   # raw server/dashboard config
        }
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")

    with p.open() as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level must be a YAML mapping.")

    raw = _expand(raw)

    slos_raw = raw.get("slos", [])
    if not slos_raw:
        raise ValueError(f"{p}: no SLOs defined.")

    slos = [_parse_slo(s) for s in slos_raw]

    # Catch duplicate names early — they'd cause silent metric label collisions later
    seen: set[str] = set()
    for slo in slos:
        if slo.name in seen:
            raise ValueError(f"Duplicate SLO name: {slo.name!r}")
        seen.add(slo.name)

    return {
        "slos":     slos,
        "backends": raw.get("backends", {}),
        "server":   raw.get("server",   {}),
    }


def validate_config(path: str | Path) -> list[str]:
    """Validate a config file. Returns a list of error messages; empty = valid."""
    errors: list[str] = []
    try:
        cfg = load_config(path)
        for slo in cfg["slos"]:
            if not slo.thresholds:
                errors.append(f"SLO {slo.name!r} has no alert thresholds.")
            if not slo.indicator.good_query:
                errors.append(f"SLO {slo.name!r}: indicators.good_query is empty.")
            if not slo.indicator.total_query:
                errors.append(f"SLO {slo.name!r}: indicators.total_query is empty.")
    except Exception as exc:
        errors.append(str(exc))
    return errors
