from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from public_ip_notifier.domain.models import WanChange, WanObservation
from public_ip_notifier.infra.teams import TeamsDeliveryError
from public_ip_notifier.services.monitor import MonitorService


class FakeProber:
    def __init__(self, observations: Mapping[str, str | None]) -> None:
        self._observations = observations

    async def probe_wan(self, wan: str, urls: Sequence[str]) -> WanObservation:
        ip = self._observations[wan]
        return WanObservation(wan, ip, urls[0] if ip is not None else None)


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
    service = MonitorService(
        {wan: (f"https://{wan}.test/ip",) for wan in observations},
        FakeProber(observations),
        store,
        notifier,
    )
    return service, store, notifier


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
async def test_monitor_when_known_ip_changes_then_notifies_and_saves_all_servers() -> None:
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
async def test_monitor_when_multiple_servers_change_then_sends_one_notification() -> None:
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
