#!/usr/bin/env python3
# Copyright 2026 Ajay Mulukutla (Ajay150313)
# MIT License — see LICENSE in the project root


"""slo-burn command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("slo-burn")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slo-burn",
        description="Multi-window SLO burn rate alerting engine",
    )
    p.add_argument("--version", action="version", version="slo-burn 0.3.0")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="cmd", required=True)

    srv = sub.add_parser("serve", help="Start the evaluation engine + dashboard")
    srv.add_argument("--config",   "-c", default="slos.yaml")
    srv.add_argument("--port",     type=int, default=9000)
    srv.add_argument("--interval", type=int, default=60,
                     help="Evaluation interval in seconds")

    val = sub.add_parser("validate", help="Validate config without running")
    val.add_argument("--config", "-c", default="slos.yaml")

    sta = sub.add_parser("status", help="Print current burn rates")
    sta.add_argument("--config", "-c", default="slos.yaml")
    sta.add_argument("--json", dest="as_json", action="store_true")

    bud = sub.add_parser("budget", help="Show error budget remaining")
    bud.add_argument("--config", "-c", default="slos.yaml")
    bud.add_argument("--json", dest="as_json", action="store_true")

    tmpl = sub.add_parser("template", help="List and generate SLO templates")
    ts   = tmpl.add_subparsers(dest="tcmd", required=True)
    ts.add_parser("list", help="List available templates")
    use  = ts.add_parser("use", help="Generate YAML from a template")
    use.add_argument("name")
    use.add_argument("--service")
    use.add_argument("--target", type=float)
    use.add_argument("--output", "-o")

    return p


# ── validate ──────────────────────────────────────────────────────────────────

def cmd_validate(args: argparse.Namespace) -> int:
    from .core.config import validate_config
    errors = validate_config(args.config)
    if errors:
        print(f"❌  {args.config}: {len(errors)} error(s)", file=sys.stderr)
        for e in errors:
            print(f"    • {e}", file=sys.stderr)
        return 1
    print(f"✅  {args.config}: valid")
    return 0


# ── template ──────────────────────────────────────────────────────────────────

def cmd_template(args: argparse.Namespace) -> int:
    from .rules.templates import list_templates, get_template

    if args.tcmd == "list":
        print()
        for t in list_templates():
            print(f"  {t['name']:<30} {t['description']}")
        print()
        print("Usage: slo-burn template use <name> [--service NAME] [--target 99.9]")
        print()
        return 0

    kw: dict = {}
    if getattr(args, "service", None):
        kw["service"] = args.service
    if getattr(args, "target", None):
        kw["target"]  = args.target
    try:
        yaml_out = get_template(args.name, **kw)
    except ValueError as exc:
        print(f"❌  {exc}", file=sys.stderr)
        return 1

    if getattr(args, "output", None):
        Path(args.output).write_text(yaml_out)
        print(f"✅  Written to {args.output}")
    else:
        print(yaml_out)
    return 0


# ── status ────────────────────────────────────────────────────────────────────

async def cmd_status_async(args: argparse.Namespace) -> int:
    from .core.config import load_config
    from .exporters.prometheus import PrometheusBackend
    from .core.evaluator import BurnRateEvaluator

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"❌  {exc}", file=sys.stderr)
        return 1

    prom_url = cfg.get("backends", {}).get("prometheus", {}).get("url", "http://localhost:9090")
    async with PrometheusBackend(url=prom_url) as backend:
        states = await BurnRateEvaluator(backend=backend).evaluate_all(cfg["slos"])

    if args.as_json:
        out = [
            {
                "slo":              s.slo_name,
                "threshold":        s.threshold.name,
                "status":           s.status.value,
                "is_firing":        s.is_firing,
                "burn_rate_short":  round(s.short_reading.burn_rate, 3),
                "burn_rate_long":   round(s.long_reading.burn_rate,  3),
                "sli":              round(s.short_reading.sli, 5),
            }
            for s in states
        ]
        print(json.dumps(out, indent=2))
    else:
        print()
        print(f"{'SLO':<28} {'Threshold':<26} {'Status':<10} {'Short':>8} {'Long':>8} {'SLI':>10}")
        print("─" * 95)
        for s in states:
            icon = "🔥" if s.is_firing else "✅"
            print(
                f"{s.slo_name:<28} {s.threshold.name:<26} "
                f"{icon} {s.status.value:<8} "
                f"{s.short_reading.burn_rate:>7.2f}× "
                f"{s.long_reading.burn_rate:>7.2f}× "
                f"{s.short_reading.sli:>9.4f}%"
            )
        print()
    return 0


# ── serve ─────────────────────────────────────────────────────────────────────

async def cmd_serve_async(args: argparse.Namespace) -> int:
    from .core.config import load_config
    from .exporters.prometheus import PrometheusBackend
    from .core.evaluator import BurnRateEvaluator
    from .rules.alerter import AlertRouter
    from .dashboard.server import render_prometheus_metrics

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        log.error("Cannot load config: %s", exc)
        return 1

    slos     = cfg["slos"]
    prom_url = cfg.get("backends", {}).get("prometheus", {}).get("url", "http://localhost:9090")

    log.info("slo-burn v0.3.0 starting")
    log.info("  SLOs:     %d", len(slos))
    log.info("  Backend:  %s", prom_url)
    log.info("  Interval: %ds", args.interval)
    log.info("  Dashboard: http://localhost:%d", args.port)
    for slo in slos:
        log.info("    • %s (%.3f%% / %dd)", slo.name, slo.target, slo.window_days)

    backend   = PrometheusBackend(url=prom_url)
    evaluator = BurnRateEvaluator(backend=backend, evaluation_interval_seconds=args.interval)
    router    = AlertRouter()

    if not await backend.health_check():
        log.warning("Prometheus health check failed at %s — will retry each cycle", prom_url)

    try:
        while True:
            t0     = time.monotonic()
            states = await evaluator.evaluate_all(slos)
            duration = time.monotonic() - t0

            # Route state transitions and send notifications
            for state in states:
                if not router.has_transitioned(state):
                    continue
                router.record(state)

                slo = next((s for s in slos if s.name == state.slo_name), None)
                if not slo:
                    continue

                for target in slo.notify:
                    sf = target.severity_filter
                    if sf and state.severity not in sf:
                        continue
                    try:
                        if target.type == "pagerduty":
                            key = target.config.get("integration_key", "")
                            if key:
                                await router.send_to_pagerduty(state, integration_key=key)
                        elif target.type == "slack":
                            url = target.config.get("webhook_url", "")
                            if url:
                                await router.send_to_slack(
                                    state,
                                    webhook_url=url,
                                    channel=target.config.get("channel"),
                                )
                        elif target.type == "opsgenie":
                            key = target.config.get("api_key", "")
                            if key:
                                await router.send_to_opsgenie(
                                    state,
                                    api_key=key,
                                    team=target.config.get("team"),
                                )
                    except Exception as exc:
                        log.error("Notification failed (%s → %s): %s",
                                  state.slo_name, target.type, exc)

            firing = [s for s in states if s.is_firing]
            if firing:
                names = ", ".join(s.slo_name for s in firing)
                log.warning("🔥 %d alert(s) firing: %s [eval=%.2fs]",
                            len(firing), names, duration)
            else:
                log.info("✅ all %d SLOs healthy [eval=%.2fs]", len(slos), duration)

            await asyncio.sleep(args.interval)

    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        await backend.close()
        await router.close()

    return 0


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parser().parse_args()
    logging.getLogger().setLevel(args.log_level)

    if args.cmd == "validate":
        sys.exit(cmd_validate(args))
    elif args.cmd == "template":
        sys.exit(cmd_template(args))
    elif args.cmd in ("status", "budget"):
        sys.exit(asyncio.run(cmd_status_async(args)))
    elif args.cmd == "serve":
        sys.exit(asyncio.run(cmd_serve_async(args)))
    else:
        _parser().print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
