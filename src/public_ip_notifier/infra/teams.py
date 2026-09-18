"""Microsoft Teams Incoming Webhook delivery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx

from public_ip_notifier.config import TeamsMention
from public_ip_notifier.domain.models import WanChange


class TeamsDeliveryError(RuntimeError):
    """Raised when a Teams webhook request does not complete successfully."""


class TeamsNotifier:
    """Send aggregated public IP change cards to a Teams webhook."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        webhook_url: str,
        mentions: Sequence[TeamsMention] = (),
    ) -> None:
        self._client = client
        self._webhook_url = webhook_url
        self._mentions = tuple(mentions)

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

    def _build_payload(
        self,
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
        mention_entities: list[dict[str, object]] = [
            {
                "type": "mention",
                "text": f"<at>{mention.name}</at>",
                "mentioned": {"id": mention.id, "name": mention.name},
            }
            for mention in self._mentions
        ]
        body: list[dict[str, object]] = [
            {
                "type": "TextBlock",
                "text": "⚠️公网出口 IP 地址变更通知",
                "size": "Medium",
                "weight": "Bolder",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": f"探测时间: {timestamp}",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "变更内容",
                "weight": "Bolder",
                "spacing": "Medium",
            },
            {"type": "FactSet", "facts": change_facts},
            {
                "type": "TextBlock",
                "text": "当前所有WAN口的公网出口IP",
                "weight": "Bolder",
                "spacing": "Medium",
            },
            {"type": "FactSet", "facts": current_facts},
        ]
        if mention_entities:
            mention_text = " ".join(
                f"<at>{mention.name}</at>" for mention in self._mentions
            )
            body.insert(
                0,
                {"type": "TextBlock", "text": mention_text, "wrap": True},
            )
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
                        "body": body,
                        "msteams": {"entities": mention_entities},
                    },
                }
            ],
        }
