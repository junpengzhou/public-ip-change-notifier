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
        """Send one aggregated Adaptive Card for an IP change cycle."""

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
        change_facts = [
            {
                "title": change.wan,
                "value": f"{change.previous_ip} -> {change.current_ip}",
            }
            for change in sorted(changes, key=lambda item: item.wan)
        ]
        current_facts = [
            {
                "title": wan,
                "value": current_ip if current_ip is not None else "unknown",
            }
            for wan, current_ip in sorted(current_ips.items())
        ]
        timestamp = observed_at.astimezone(UTC).isoformat()
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": "",
                    "content": {
                        "$schema": (
                            "http://adaptivecards.io/schemas/adaptive-card.json"
                        ),
                        "type": "AdaptiveCard",
                        "version": "1.5",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": "Public IP address changed",
                                "size": "Medium",
                                "weight": "Bolder",
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Observed at: {timestamp}",
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": "Changes",
                                "weight": "Bolder",
                                "spacing": "Medium",
                            },
                            {"type": "FactSet", "facts": change_facts},
                            {
                                "type": "TextBlock",
                                "text": "Current WAN IPs",
                                "weight": "Bolder",
                                "spacing": "Medium",
                            },
                            {"type": "FactSet", "facts": current_facts},
                        ],
                        "msteams": {"entities": []},
                    },
                }
            ],
        }
