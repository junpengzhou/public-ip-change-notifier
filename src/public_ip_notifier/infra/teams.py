"""Microsoft Teams Incoming Webhook delivery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx

from public_ip_notifier.domain.models import WanChange


class TeamsDeliveryError(RuntimeError):
    """Raised when a Teams webhook request does not complete successfully."""


class TeamsNotifier:
    """Send aggregated public IP change cards to a Teams webhook."""

    def __init__(self, client: httpx.AsyncClient, webhook_url: str) -> None:
        self._client = client
        self._webhook_url = webhook_url

    async def send(
        self,
        changes: Sequence[WanChange],
        current_ips: Mapping[str, str | None],
        observed_at: datetime,
    ) -> None:
        """Send one aggregated Teams MessageCard for an IP change cycle."""

        payload = self._build_payload(changes, current_ips, observed_at)
        try:
            response = await self._client.post(self._webhook_url, json=payload)
            response.raise_for_status()
        except (
            httpx.TimeoutException,
            httpx.RequestError,
            httpx.HTTPStatusError,
        ) as exc:
            raise TeamsDeliveryError("Teams webhook delivery failed") from exc

    @staticmethod
    def _build_payload(
        changes: Sequence[WanChange],
        current_ips: Mapping[str, str | None],
        observed_at: datetime,
    ) -> dict[str, object]:
        change_text = "\n".join(
            f"{change.wan}: {change.previous_ip} -> {change.current_ip}"
            for change in sorted(changes, key=lambda item: item.wan)
        )
        facts = [
            {
                "name": wan,
                "value": current_ip if current_ip is not None else "unknown",
            }
            for wan, current_ip in sorted(current_ips.items())
        ]
        timestamp = observed_at.astimezone(UTC).isoformat()
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": "Public IP address changed",
            "themeColor": "0076D7",
            "title": "Public IP address changed",
            "sections": [
                {
                    "activityTitle": "WAN IP change detected",
                    "activitySubtitle": timestamp,
                    "text": change_text,
                    "facts": facts,
                }
            ],
        }
