# Copyright 2026 Ajay Mulukutla (Ajay150313)
# MIT License — see LICENSE in the project root


"""Built-in SLO templates. These cover 90% of what teams actually need."""

from __future__ import annotations

TEMPLATES: dict[str, dict] = {
    "http-availability": {
        "description": "HTTP service availability — measures 5xx error rate",
        "defaults": {"service": "my-service", "target": 99.9},
        "yaml": """\
slos:
  - name: {service}-availability
    description: "{service} HTTP availability"
    target: {target}
    window_days: 30
    indicators:
      good_query: |
        sum(rate(http_requests_total{{
          service="{service}", code!~"5.."
        }}[{{window}}]))
      total_query: |
        sum(rate(http_requests_total{{
          service="{service}"
        }}[{{window}}]))
    alerts:
      - severity: page
        short_window: 1h
        long_window: 5h
        multiplier: 14x
      - severity: ticket
        short_window: 6h
        long_window: 3d
        multiplier: 3x
""",
    },
    "http-latency": {
        "description": "HTTP response time — good = requests under latency threshold",
        "defaults": {"service": "my-service", "target": 99.5, "threshold_s": "0.3"},
        "yaml": """\
slos:
  - name: {service}-latency
    description: "{service} P99 latency < {threshold_s}s"
    target: {target}
    window_days: 30
    indicators:
      good_query: |
        sum(rate(http_request_duration_seconds_bucket{{
          service="{service}", le="{threshold_s}"
        }}[{{window}}]))
      total_query: |
        sum(rate(http_request_duration_seconds_count{{
          service="{service}"
        }}[{{window}}]))
    alerts:
      - severity: page
        short_window: 1h
        long_window: 5h
        multiplier: 14x
      - severity: ticket
        short_window: 6h
        long_window: 3d
        multiplier: 3x
""",
    },
    "grpc-availability": {
        "description": "gRPC service availability — OK vs non-OK status codes",
        "defaults": {"service": "my.GrpcService", "target": 99.9},
        "yaml": """\
slos:
  - name: {service}-grpc-availability
    description: "{service} gRPC availability"
    target: {target}
    window_days: 30
    indicators:
      good_query: |
        sum(rate(grpc_server_handled_total{{
          grpc_service="{service}", grpc_code="OK"
        }}[{{window}}]))
      total_query: |
        sum(rate(grpc_server_handled_total{{
          grpc_service="{service}"
        }}[{{window}}]))
    alerts:
      - severity: page
        short_window: 1h
        long_window: 5h
        multiplier: 14x
      - severity: ticket
        short_window: 6h
        long_window: 3d
        multiplier: 3x
""",
    },
    "database-query-time": {
        "description": "Database query latency percentile",
        "defaults": {"db": "postgres", "target": 99.5, "threshold_s": "0.1"},
        "yaml": """\
slos:
  - name: {db}-query-latency
    description: "{db} P95 query time < {threshold_s}s"
    target: {target}
    window_days: 30
    indicators:
      good_query: |
        sum(rate(db_query_duration_seconds_bucket{{
          database="{db}", le="{threshold_s}"
        }}[{{window}}]))
      total_query: |
        sum(rate(db_query_duration_seconds_count{{
          database="{db}"
        }}[{{window}}]))
    alerts:
      - severity: page
        short_window: 1h
        long_window: 5h
        multiplier: 14x
      - severity: ticket
        short_window: 6h
        long_window: 3d
        multiplier: 3x
""",
    },
    "queue-processing": {
        "description": "Message queue consumer success rate",
        "defaults": {"queue": "my-queue", "target": 99.9},
        "yaml": """\
slos:
  - name: {queue}-processing
    description: "{queue} message processing success rate"
    target: {target}
    window_days: 30
    indicators:
      good_query: |
        sum(rate(queue_messages_processed_total{{
          queue="{queue}", status="success"
        }}[{{window}}]))
      total_query: |
        sum(rate(queue_messages_processed_total{{
          queue="{queue}"
        }}[{{window}}]))
    alerts:
      - severity: ticket
        short_window: 6h
        long_window: 3d
        multiplier: 3x
""",
    },
    "cronjob-success": {
        "description": "Scheduled job success rate",
        "defaults": {"job": "my-job", "target": 99.0},
        "yaml": """\
slos:
  - name: {job}-success
    description: "{job} must succeed reliably"
    target: {target}
    window_days: 30
    indicators:
      good_query: |
        sum(rate(job_executions_total{{
          job="{job}", status="success"
        }}[{{window}}]))
      total_query: |
        sum(rate(job_executions_total{{
          job="{job}"
        }}[{{window}}]))
    alerts:
      - severity: ticket
        short_window: 6h
        long_window: 3d
        multiplier: 3x
""",
    },
}


def list_templates() -> list[dict]:  # type: ignore[type-arg]
    return [{"name": n, "description": t["description"]} for n, t in TEMPLATES.items()]


def get_template(name: str, **kwargs: object) -> str:
    if name not in TEMPLATES:
        available = ", ".join(TEMPLATES)
        raise ValueError(f"Unknown template {name!r}. Available: {available}")
    t = TEMPLATES[name]
    values = {**t["defaults"], **kwargs}
    return t["yaml"].format(**values)
