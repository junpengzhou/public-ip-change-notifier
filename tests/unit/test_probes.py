from ipaddress import IPv4Network, IPv6Network

import httpx
import pytest
import respx

import public_ip_notifier.domain.models as domain_models
from public_ip_notifier.domain.models import WanObservation, WanProbeTarget
from public_ip_notifier.infra.probes import IpProbe


class RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **values: object) -> object:
        self.warnings.append((event, values))
        return None


def test_wan_probe_target_when_created_then_preserves_urls_and_networks() -> None:
    target_type = getattr(domain_models, "WanProbeTarget", None)
    assert target_type is not None

    target = target_type(
        urls=("https://example.test/ip",),
        networks=(IPv4Network("218.0.0.0/8"),),
    )

    assert target.urls == ("https://example.test/ip",)
    assert target.networks == (IPv4Network("218.0.0.0/8"),)


@pytest.mark.asyncio
async def test_probe_when_first_url_errors_then_uses_second_url() -> None:
    target = WanProbeTarget(
        urls=("https://first.test/ip", "https://second.test/ip"),
        networks=(IPv4Network("198.51.100.0/24"),),
    )
    with respx.mock(assert_all_called=True) as router:
        first = router.get("https://first.test/ip").mock(
            side_effect=httpx.ConnectError("down")
        )
        second = router.get("https://second.test/ip").mock(
            return_value=httpx.Response(200, text="198.51.100.20")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan("wan1", target)

    assert result == WanObservation("wan1", "198.51.100.20", "https://second.test/ip")
    assert first.called and second.called


@pytest.mark.asyncio
async def test_probe_when_first_response_is_not_ip_then_uses_second_url() -> None:
    target = WanProbeTarget(
        urls=("https://invalid.test/ip", "https://valid.test/ip"),
        networks=(IPv6Network("2001:db8::/32"),),
    )
    with respx.mock(assert_all_called=True) as router:
        router.get("https://invalid.test/ip").mock(
            return_value=httpx.Response(200, text="unknown")
        )
        router.get("https://valid.test/ip").mock(
            return_value=httpx.Response(200, text="2001:db8::1")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan("wan1", target)

    assert result.ip == "2001:db8::1"
    assert result.source_url == "https://valid.test/ip"


@pytest.mark.asyncio
async def test_probe_when_all_urls_fail_then_returns_empty_observation() -> None:
    target = WanProbeTarget(
        urls=("https://unavailable.test/ip", "https://offline.test/ip"),
        networks=(IPv4Network("198.51.100.0/24"),),
    )
    with respx.mock(assert_all_called=True) as router:
        router.get("https://unavailable.test/ip").mock(return_value=httpx.Response(503))
        router.get("https://offline.test/ip").mock(
            side_effect=httpx.ConnectError("down")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan("wan1", target)

    assert result == WanObservation("wan1", None, None)


@pytest.mark.asyncio
async def test_probe_when_address_is_outside_network_then_uses_next_url() -> None:
    logger = RecordingLogger()
    target = WanProbeTarget(
        urls=("https://wrong.test/ip", "https://right.test/ip"),
        networks=(IPv4Network("218.0.0.0/8"),),
    )
    with respx.mock(assert_all_called=True) as router:
        router.get("https://wrong.test/ip").mock(
            return_value=httpx.Response(200, text="120.10.20.30")
        )
        router.get("https://right.test/ip").mock(
            return_value=httpx.Response(200, text="218.10.20.30")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client, logger).probe_wan("wan1", target)

    assert result == WanObservation("wan1", "218.10.20.30", target.urls[1])
    assert logger.warnings[0][0] == "probe_ip_outside_allowed_networks"
    assert logger.warnings[0][1]["ip"] == "120.10.20.30"


@pytest.mark.asyncio
async def test_probe_when_all_addresses_are_outside_network_then_returns_empty() -> (
    None
):
    target = WanProbeTarget(
        urls=("https://wrong.test/ip",),
        networks=(IPv4Network("218.0.0.0/8"),),
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(target.urls[0]).mock(
            return_value=httpx.Response(200, text="120.10.20.30")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan("wan1", target)

    assert result == WanObservation("wan1", None, None)


@pytest.mark.asyncio
async def test_probe_when_address_family_differs_then_returns_empty() -> None:
    target = WanProbeTarget(
        urls=("https://ipv6.test/ip",),
        networks=(IPv4Network("218.0.0.0/8"),),
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(target.urls[0]).mock(
            return_value=httpx.Response(200, text="2001:db8::1")
        )

        async with httpx.AsyncClient() as client:
            result = await IpProbe(client).probe_wan("wan1", target)

    assert result == WanObservation("wan1", None, None)
