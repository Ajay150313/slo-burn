# Copyright 2026 Ajay Mulukutla (Ajay150313)
# Apache-2.0 License — see LICENSE in the project root


"""
Prometheus metrics backend.

Works with vanilla Prometheus, Thanos query-frontend, Grafana Mimir,
and VictoriaMetrics — anything that exposes /api/v1/query.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)


class PrometheusBackend:
    """Async Prometheus-compatible backend.

    Args:
        url:        Base URL, e.g. 'http://prometheus:9090'
        timeout:    Per-request timeout in seconds
        headers:    Extra headers — use for auth tokens, org IDs (Mimir/Cortex)
        tls_verify: Set False for self-signed certs in dev environments
        basic_auth: (username, password) tuple for basic auth
    """

    def __init__(
        self,
        url:        str,
        timeout:    float = 30.0,
        headers:    dict[str, str] | None = None,
        tls_verify: bool = True,
        basic_auth: tuple[str, str] | None = None,
    ) -> None:
        self._url   = url.rstrip("/")
        self._query_url = urljoin(self._url + "/", "api/v1/query")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers=headers or {},
            verify=tls_verify,
            auth=basic_auth,
        )

    @property
    def name(self) -> str:
        return f"prometheus({self._url})"

    async def health_check(self) -> bool:
        try:
            r = await self._client.get(urljoin(self._url + "/", "-/healthy"))
            return r.status_code == 200
        except Exception as exc:
            log.warning("Prometheus health check failed: %s", exc)
            return False

    async def query_scalar(self, query: str) -> float | None:
        """Execute an instant PromQL query and return the first scalar result.

        Returns None (not raises) when:
          - the query returns no data series
          - the series value is NaN
          - the HTTP request fails (logged as error, not exception)

        The caller — BurnRateEvaluator._query_window — treats None as zero
        traffic and marks the reading as invalid, avoiding false alerts during
        backend outages or when a service is not yet instrumented.
        """
        try:
            r = await self._client.get(self._query_url, params={"query": query})
            r.raise_for_status()
            data = r.json()

            if data.get("status") != "success":
                log.warning(
                    "Prometheus non-success for query %.80r: %s",
                    query, data.get("error", "unknown"),
                )
                return None

            result_type = data["data"].get("resultType")
            result      = data["data"].get("result", [])

            if result_type == "scalar":
                return float(data["data"]["result"][1])

            if result_type == "vector":
                if not result:
                    log.debug("No series returned for query: %.80r", query)
                    return None
                raw_value = result[0].get("value", [None, None])[1]
                if raw_value is None:
                    return None
                val = float(raw_value)
                # NaN means the metric exists but has no valid value
                import math
                return None if math.isnan(val) else val

            log.warning("Unexpected resultType %r for query %.80r", result_type, query)
            return None

        except httpx.HTTPStatusError as exc:
            log.error("HTTP %d from Prometheus: %s", exc.response.status_code, exc)
            return None
        except httpx.RequestError as exc:
            log.error("Prometheus request failed: %s", exc)
            return None
        except (KeyError, ValueError, TypeError) as exc:
            log.error("Error parsing Prometheus response: %s", exc)
            return None

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PrometheusBackend":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
