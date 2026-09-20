"""Asynchronous public IP endpoint probing."""

from __future__ import annotations

import ipaddress
from typing import Protocol

import httpx
import structlog

from public_ip_notifier.domain.models import WanObservation, WanProbeTarget


class ProbeLogger(Protocol):
    """Small logger interface required by the probe adapter."""

    def warning(self, event: str, **values: object) -> object:
        """Record a structured warning."""


class IpProbe:
    """Try a WAN's configured URLs in order and return the first valid IP."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        logger: ProbeLogger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or structlog.get_logger(__name__)

    async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
        """Return the first allowed IP returned by a WAN's configured URLs."""

        for url in target.urls:
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                address = ipaddress.ip_address(response.text.strip())
            except (
                httpx.TimeoutException,
                httpx.RequestError,
                httpx.HTTPStatusError,
                ValueError,
            ) as exc:
                self._logger.warning(
                    "probe_url_failed",
                    wan=wan,
                    url=url,
                    error_type=type(exc).__name__,
                )
                continue

            if not any(address in network for network in target.networks):
                self._logger.warning(
                    "probe_ip_outside_allowed_networks",
                    wan=wan,
                    url=url,
                    ip=str(address),
                    allowed_networks=[str(network) for network in target.networks],
                )
                continue

            return WanObservation(wan, str(address), url)

        return WanObservation(wan, None, None)
