from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from ipaddress import IPv4Network

import pytest

from public_ip_notifier.domain.models import WanChange, WanObservation, WanProbeTarget
from public_ip_notifier.infra.teams import TeamsDeliveryError
from public_ip_notifier.services.monitor import CycleResult, MonitorService


class FakeProber:
    def __init__(self, observations: Mapping[str, str | None]) -> None:
        self._observations = observations

    async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
        ip = self._observations[wan]
        return WanObservation(wan, ip, target.urls[0] if ip is not None else None)


class RecordingProber(FakeProber):
    def __init__(self, observations: Mapping[str, str | None]) -> None:
        super().__init__(observations)
        self.targets: list[tuple[str, WanProbeTarget]] = []

    async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
        self.targets.append((wan, target))
        return await super().probe_wan(wan, target)


class UrlAwareProber:
    def __init__(
        self,
        responses: Mapping[tuple[str, str], str | None],
    ) -> None:
        self._responses = responses
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
        self.calls.append((wan, target.urls))
        for url in target.urls:
            ip = self._responses[(wan, url)]
            if ip is not None:
                return WanObservation(wan, ip, url)
        return WanObservation(wan, None, None)


class SequencedUrlProber:
    def __init__(
        self,
        responses: Mapping[tuple[str, str], Sequence[str | None]],
    ) -> None:
        self._responses = {key: list(values) for key, values in responses.items()}
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
        self.calls.append((wan, target.urls))
        for url in target.urls:
            ip = self._responses[(wan, url)].pop(0)
            if ip is not None:
                return WanObservation(wan, ip, url)
        return WanObservation(wan, None, None)


class RecordingMonitorLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, object]]] = []
        self.errors: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **values: object) -> object:
        self.warnings.append((event, values))
        return None

    def error(self, event: str, **values: object) -> object:
        self.errors.append((event, values))
        return None


class FakeStore:
    def __init__(self, previous: Mapping[str, str]) -> None:
        self.previous = dict(previous)
        self.saved: dict[str, str] | None = None

    async def load(self) -> dict[str, str]:
        return dict(self.previous)

    async def save(self, ips: Mapping[str, str]) -> None:
        self.saved = dict(ips)


@dataclass
class NotificationCall:
    changes: tuple[WanChange, ...]
    current_ips: dict[str, str | None]


@dataclass
class FakeNotifier:
    error: TeamsDeliveryError | None = None
    calls: list[NotificationCall] = field(default_factory=list)

    async def send(
        self,
        changes: Sequence[WanChange],
        current_ips: Mapping[str, str | None],
        observed_at: object,
    ) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(NotificationCall(tuple(changes), dict(current_ips)))


def make_service(
    previous: Mapping[str, str],
    observations: Mapping[str, str | None],
    notifier: FakeNotifier | None = None,
) -> tuple[MonitorService, FakeStore, FakeNotifier | None]:
    store = FakeStore(previous)
    targets = {
        wan: WanProbeTarget(
            urls=(f"https://{wan}.test/ip",),
            networks=(IPv4Network("0.0.0.0/0"),),
        )
        for wan in observations
    }
    service = MonitorService(
        targets,
        FakeProber(observations),
        store,
        notifier,
    )
    return service, store, notifier


def make_url_aware_service(
    previous: Mapping[str, str],
    urls: Mapping[str, tuple[str, ...]],
    responses: Mapping[tuple[str, str], str | None],
    notifier: FakeNotifier | None = None,
    logger: RecordingMonitorLogger | None = None,
) -> tuple[MonitorService, FakeStore, UrlAwareProber]:
    store = FakeStore(previous)
    prober = UrlAwareProber(responses)
    targets = {
        wan: WanProbeTarget(
            urls=wan_urls,
            networks=(IPv4Network("0.0.0.0/0"),),
        )
        for wan, wan_urls in urls.items()
    }
    service = MonitorService(targets, prober, store, notifier, logger)
    return service, store, prober


@pytest.mark.asyncio
async def test_monitor_when_running_then_passes_complete_target_to_prober() -> None:
    target = WanProbeTarget(
        urls=("https://wan1.test/ip",),
        networks=(IPv4Network("218.0.0.0/8"),),
    )
    prober = RecordingProber({"wan1": "218.10.20.30"})
    store = FakeStore({})
    service = MonitorService({"wan1": target}, prober, store)

    await service.run_once()

    assert prober.targets == [("wan1", target)]


@pytest.mark.asyncio
async def test_monitor_when_first_cycle_then_saves_baseline_without_notifying() -> None:
    service, store, notifier = make_service(
        previous={},
        observations={"wan1": "203.0.113.10"},
        notifier=FakeNotifier(),
    )

    result = await service.run_once()

    assert result.notified is False
    assert notifier is not None and notifier.calls == []
    assert store.saved == {"wan1": "203.0.113.10"}


@pytest.mark.asyncio
async def test_monitor_when_known_ip_changes_then_notifies_and_saves_all_servers() -> (
    None
):
    service, store, notifier = make_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        observations={"wan1": "203.0.113.11", "wan2": "198.51.100.20"},
        notifier=FakeNotifier(),
    )

    result = await service.run_once()

    assert result.notified is True
    assert notifier is not None
    assert notifier.calls[0].current_ips == {
        "wan1": "203.0.113.11",
        "wan2": "198.51.100.20",
    }
    assert store.saved == notifier.calls[0].current_ips


@pytest.mark.asyncio
async def test_monitor_when_webhook_fails_then_keeps_previous_state_for_retry() -> None:
    service, store, notifier = make_service(
        previous={"wan1": "203.0.113.10"},
        observations={"wan1": "203.0.113.11"},
        notifier=FakeNotifier(TeamsDeliveryError("webhook down")),
    )

    result = await service.run_once()

    assert result.notification_failed is True
    assert notifier is not None and notifier.calls == []
    assert store.saved is None


@pytest.mark.asyncio
async def test_monitor_when_probe_fails_then_preserves_previous_ip() -> None:
    service, store, notifier = make_service(
        previous={"wan1": "203.0.113.10"},
        observations={"wan1": None},
        notifier=FakeNotifier(),
    )

    result = await service.run_once()

    assert result.changes == ()
    assert store.saved == {"wan1": "203.0.113.10"}
    assert notifier is not None and notifier.calls == []


@pytest.mark.asyncio
async def test_monitor_when_new_wan_appears_then_establishes_baseline_only() -> None:
    service, store, notifier = make_service(
        previous={"wan1": "203.0.113.10"},
        observations={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        notifier=FakeNotifier(),
    )

    result = await service.run_once()

    assert result.notified is False
    assert store.saved == {
        "wan1": "203.0.113.10",
        "wan2": "198.51.100.20",
    }
    assert notifier is not None and notifier.calls == []


@pytest.mark.asyncio
async def test_monitor_when_multiple_servers_change_then_sends_one_notification() -> (
    None
):
    service, store, notifier = make_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        observations={"wan1": "203.0.113.11", "wan2": "198.51.100.21"},
        notifier=FakeNotifier(),
    )

    result = await service.run_once()

    assert len(result.changes) == 2
    assert notifier is not None and len(notifier.calls) == 1
    assert store.saved == {
        "wan1": "203.0.113.11",
        "wan2": "198.51.100.21",
    }


@pytest.mark.asyncio
async def test_monitor_when_initial_ips_repeat_then_uses_remaining_url() -> None:
    notifier = FakeNotifier()
    service, store, prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        urls={
            "wan1": ("https://wan1-a.test/ip", "https://wan1-b.test/ip"),
            "wan2": ("https://wan2-a.test/ip", "https://wan2-b.test/ip"),
        },
        responses={
            ("wan1", "https://wan1-a.test/ip"): "192.0.2.10",
            ("wan1", "https://wan1-b.test/ip"): "192.0.2.11",
            ("wan2", "https://wan2-a.test/ip"): "192.0.2.10",
            ("wan2", "https://wan2-b.test/ip"): "198.51.100.21",
        },
        notifier=notifier,
    )

    result = await service.run_once()

    assert result.notified is True
    assert store.saved == {
        "wan1": "192.0.2.10",
        "wan2": "198.51.100.21",
    }
    assert ("wan2", ("https://wan2-b.test/ip",)) in prober.calls
    assert ("wan1", ("https://wan1-b.test/ip",)) not in prober.calls


@pytest.mark.asyncio
async def test_monitor_when_ip_matches_other_wan_state_then_uses_remaining_url() -> (
    None
):
    notifier = FakeNotifier()
    service, store, _prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        urls={
            "wan1": ("https://wan1-a.test/ip", "https://wan1-b.test/ip"),
            "wan2": ("https://wan2-a.test/ip",),
        },
        responses={
            ("wan1", "https://wan1-a.test/ip"): "198.51.100.20",
            ("wan1", "https://wan1-b.test/ip"): "203.0.113.11",
            ("wan2", "https://wan2-a.test/ip"): "198.51.100.20",
        },
        notifier=notifier,
    )

    result = await service.run_once()

    assert result.changes == (WanChange("wan1", "203.0.113.10", "203.0.113.11"),)
    assert store.saved == {
        "wan1": "203.0.113.11",
        "wan2": "198.51.100.20",
    }


@pytest.mark.asyncio
async def test_monitor_when_ip_matches_unconfigured_wan_state_then_uses_next_url() -> (
    None
):
    service, store, _prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "retired-wan": "198.51.100.20"},
        urls={"wan1": ("https://wan1-a.test/ip", "https://wan1-b.test/ip")},
        responses={
            ("wan1", "https://wan1-a.test/ip"): "198.51.100.20",
            ("wan1", "https://wan1-b.test/ip"): "203.0.113.11",
        },
    )

    result = await service.run_once()

    assert result.changes == (WanChange("wan1", "203.0.113.10", "203.0.113.11"),)
    assert store.saved == {"wan1": "203.0.113.11"}


@pytest.mark.asyncio
async def test_monitor_when_greedy_choice_blocks_wan_then_backtracks() -> None:
    service, store, _prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        urls={
            "wan1": ("https://wan1-a.test/ip", "https://wan1-b.test/ip"),
            "wan2": ("https://wan2-a.test/ip",),
        },
        responses={
            ("wan1", "https://wan1-a.test/ip"): "192.0.2.10",
            ("wan1", "https://wan1-b.test/ip"): "192.0.2.11",
            ("wan2", "https://wan2-a.test/ip"): "192.0.2.10",
        },
    )

    result = await service.run_once()

    assert result.changes == (
        WanChange("wan1", "203.0.113.10", "192.0.2.11"),
        WanChange("wan2", "198.51.100.20", "192.0.2.10"),
    )
    assert store.saved == {"wan1": "192.0.2.11", "wan2": "192.0.2.10"}


@pytest.mark.asyncio
async def test_monitor_when_all_ip_combinations_conflict_then_discards_cycle() -> None:
    notifier = FakeNotifier()
    logger = RecordingMonitorLogger()
    service, store, _prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        urls={
            "wan1": ("https://wan1-a.test/ip",),
            "wan2": ("https://wan2-a.test/ip", "https://wan2-b.test/ip"),
        },
        responses={
            ("wan1", "https://wan1-a.test/ip"): "192.0.2.10",
            ("wan2", "https://wan2-a.test/ip"): "192.0.2.10",
            ("wan2", "https://wan2-b.test/ip"): "192.0.2.10",
        },
        notifier=notifier,
        logger=logger,
    )

    result = await service.run_once()

    assert result == CycleResult(False, False, ())
    assert store.saved is None
    assert notifier.calls == []
    assert logger.warnings[0][0] == "wan_ip_cycle_discarded"


@pytest.mark.asyncio
async def test_monitor_when_url_is_duplicated_then_consumes_each_position_once() -> (
    None
):
    duplicate_url = "https://duplicate.test/ip"
    fallback_url = "https://fallback.test/ip"
    target = WanProbeTarget(
        urls=(duplicate_url, duplicate_url, fallback_url),
        networks=(IPv4Network("0.0.0.0/0"),),
    )
    prober = SequencedUrlProber(
        {
            ("wan1", duplicate_url): (
                None,
                "198.51.100.20",
                "192.0.2.99",
            ),
            ("wan1", fallback_url): ("203.0.113.11",),
        }
    )
    store = FakeStore({"wan1": "203.0.113.10", "retired-wan": "198.51.100.20"})
    service = MonitorService({"wan1": target}, prober, store)

    result = await service.run_once()

    assert result.changes == (WanChange("wan1", "203.0.113.10", "203.0.113.11"),)
    assert store.saved == {"wan1": "203.0.113.11"}
    assert prober.calls == [
        ("wan1", (duplicate_url,)),
        ("wan1", (duplicate_url,)),
        ("wan1", (fallback_url,)),
    ]


@pytest.mark.asyncio
async def test_monitor_when_candidate_ip_repeats_then_uses_later_distinct_ip() -> None:
    urls = (
        "https://first.test/ip",
        "https://second.test/ip",
        "https://third.test/ip",
    )
    service, store, prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "retired-wan": "198.51.100.20"},
        urls={"wan1": urls},
        responses={
            ("wan1", urls[0]): "198.51.100.20",
            ("wan1", urls[1]): "198.51.100.20",
            ("wan1", urls[2]): "203.0.113.11",
        },
    )

    result = await service.run_once()

    assert result.changes == (WanChange("wan1", "203.0.113.10", "203.0.113.11"),)
    assert store.saved == {"wan1": "203.0.113.11"}
    assert prober.calls == [
        ("wan1", (urls[0],)),
        ("wan1", (urls[1],)),
        ("wan1", (urls[2],)),
    ]


@pytest.mark.asyncio
async def test_monitor_when_one_wan_probe_fails_then_other_wan_change_continues() -> (
    None
):
    notifier = FakeNotifier()
    urls = {
        "wan1": ("https://wan1-a.test/ip", "https://wan1-b.test/ip"),
        "wan2": ("https://wan2.test/ip",),
    }
    service, store, _prober = make_url_aware_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        urls=urls,
        responses={
            ("wan1", urls["wan1"][0]): None,
            ("wan1", urls["wan1"][1]): None,
            ("wan2", urls["wan2"][0]): "198.51.100.21",
        },
        notifier=notifier,
    )

    result = await service.run_once()

    assert result.notified is True
    assert result.changes == (WanChange("wan2", "198.51.100.20", "198.51.100.21"),)
    assert store.saved == {
        "wan1": "203.0.113.10",
        "wan2": "198.51.100.21",
    }
