# Copyright 2026 Ajay Mulukutla (Ajay150313)
# Apache-2.0 License — see LICENSE in the project root


"""
Datadog metrics backend.

Uses the Datadog Metrics Query API v1. Datadog queries use DQL syntax,
not PromQL — {window} placeholder substitution is not supported here because
Datadog rollup windows are specified inside the DQL expression itself.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_SITES: dict[str, str] = {
    "us1": "api.datadoghq.com",
    "us3": "api.us3.datadoghq.com",
    "us5": "api.us5.datadoghq.com",
    "eu1": "api.datadoghq.eu",
    "ap1": "api.ap1.datadoghq.com",
}


class DatadogBackend:
    """Async Datadog metrics backend.

    Args:
        api_key: Datadog API key
        app_key: Datadog Application key
        site:    Datadog regional site (us1 | us3 | us5 | eu1 | ap1)
        timeout: Per-request timeout in seconds
    """

    def __init__(
        self,
        api_key: str,
        app_key: str,
        site:    str   = "us1",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("Datadog api_key is required")
        if not app_key:
            raise ValueError("Datadog app_key is required")

        host = _SITES.get(site, _SITES["us1"])
        self._base  = f"https://{host}"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "DD-API-KEY":         api_key,
                "DD-APPLICATION-KEY": app_key,
            },
        )

    @property
    def name(self) -> str:
        return f"datadog({self._base})"

    async def health_check(self) -> bool:
        try:
            r = await self._client.get(f"{self._base}/api/v1/validate")
            return r.status_code == 200
        except Exception as exc:
            log.warning("Datadog health check failed: %s", exc)
            return False

    async def query_scalar(self, query: str) -> float | None:
        """Query a 5-minute window ending now and return the last point value.

        Note: Datadog indicators must use full DQL with rollup built in.
        The {window} placeholder from slos.yaml is NOT applied here.
        """
        now  = int(time.time())
        frm  = now - 300
        try:
            r = await self._client.get(
                f"{self._base}/api/v1/query",
                params={"from": frm, "to": now, "query": query},
            )
            r.raise_for_status()
            series = r.json().get("series", [])
            if not series:
                return None
            points = series[0].get("pointlist", [])
            if not points:
                return None
            # pointlist entries are [timestamp_ms, value]
            val = points[-1][1] if len(points[-1]) > 1 else None
            return float(val) if val is not None else None
        except httpx.HTTPStatusError as exc:
            log.error("Datadog HTTP %d: %s", exc.response.status_code, exc)
            return None
        except httpx.RequestError as exc:
            log.error("Datadog request failed: %s", exc)
            return None
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            log.error("Datadog response parse error: %s", exc)
            return None

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DatadogBackend":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
