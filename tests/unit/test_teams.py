import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from public_ip_notifier.domain.models import WanChange
from public_ip_notifier.infra.teams import TeamsDeliveryError, TeamsNotifier


@pytest.mark.asyncio
async def test_teams_notifier_when_change_then_posts_adaptive_card_attachment() -> None:
    observed_at = datetime(2026, 9, 18, 8, 30, tzinfo=UTC)
    changes = (WanChange("wan1", "203.0.113.10", "203.0.113.11"),)

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://teams.test/webhook").mock(
            return_value=httpx.Response(202)
        )
        async with httpx.AsyncClient() as client:
            await TeamsNotifier(
                client,
                "https://teams.test/webhook",
                mentions=(),
            ).send(
                changes,
                {"wan1": "203.0.113.11", "wan2": None},
                observed_at,
            )

    request = route.calls[0].request
    payload = json.loads(request.content)
    assert request.headers["content-type"].startswith("application/json")
    assert payload["type"] == "message"
    assert isinstance(payload["attachments"], list)
    assert len(payload["attachments"]) == 1
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["contentUrl"] == ""
    assert attachment["content"]["type"] == "AdaptiveCard"
    assert attachment["content"]["version"] == "1.5"
    assert attachment["content"]["msteams"] == {"entities": []}
    payload_text = json.dumps(payload)
    assert "wan1" in payload_text
    assert "203.0.113.10" in payload_text
    assert "203.0.113.11" in payload_text
    assert "unknown" in payload_text
    assert observed_at.isoformat() in payload_text


@pytest.mark.asyncio
async def test_teams_notifier_when_webhook_fails_then_raises_delivery_error() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post("https://teams.test/webhook").mock(return_value=httpx.Response(500))
        async with httpx.AsyncClient() as client:
            notifier = TeamsNotifier(client, "https://teams.test/webhook")

            with pytest.raises(TeamsDeliveryError):
                await notifier.send(
                    (WanChange("wan1", "203.0.113.10", "203.0.113.11"),),
                    {"wan1": "203.0.113.11"},
                    datetime.now(UTC),
                )
