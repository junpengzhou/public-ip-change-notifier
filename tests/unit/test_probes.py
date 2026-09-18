import httpx
import pytest
import respx

from public_ip_notifier.domain.models import WanObservation
from public_ip_notifier.infra.probes import IpProbe


@pytest.mark.asyncio
async def test_probe_when_first_url_errors_then_uses_second_url() -> None:
    with respx.mock(assert_all_called=True) as router:
        first = router.get("https://first.test/ip").mock(
            side_effect=httpx.ConnectError("down")
        )
        second = router.get("https://second.test/ip").mock(
            return_value=httpx.Response(200, text="198.51.100.20")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan(
                "wan1", ("https://first.test/ip", "https://second.test/ip")
            )

    assert result == WanObservation("wan1", "198.51.100.20", "https://second.test/ip")
    assert first.called and second.called


@pytest.mark.asyncio
async def test_probe_when_first_response_is_not_ip_then_uses_second_url() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://invalid.test/ip").mock(
            return_value=httpx.Response(200, text="unknown")
        )
        router.get("https://valid.test/ip").mock(
            return_value=httpx.Response(200, text="2001:db8::1")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan(
                "wan1", ("https://invalid.test/ip", "https://valid.test/ip")
            )

    assert result.ip == "2001:db8::1"
    assert result.source_url == "https://valid.test/ip"


@pytest.mark.asyncio
async def test_probe_when_all_urls_fail_then_returns_empty_observation() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://unavailable.test/ip").mock(return_value=httpx.Response(503))
        router.get("https://offline.test/ip").mock(
            side_effect=httpx.ConnectError("down")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan(
                "wan1", ("https://unavailable.test/ip", "https://offline.test/ip")
            )

    assert result == WanObservation("wan1", None, None)
