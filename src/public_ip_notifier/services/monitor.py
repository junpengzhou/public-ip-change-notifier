"""Coordinate WAN probes, state comparisons, and notifications."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog

from public_ip_notifier.domain.models import WanChange, WanObservation
from public_ip_notifier.infra.teams import TeamsDeliveryError


class WanProber(Protocol):
    """Probe interface required by the monitor service."""

    async def probe_wan(self, wan: str, urls: Sequence[str]) -> WanObservation:
        """Collect one observation for a WAN."""


class StateRepository(Protocol):
    """State persistence interface required by the monitor service."""

    async def load(self) -> dict[str, str]:
        """Load the previous successful WAN IP mapping."""

    async def save(self, ips: Mapping[str, str]) -> None:
        """Persist a successful WAN IP mapping."""


class Notifier(Protocol):
    """Notification interface required by the monitor service."""

    async def send(
        self,
        changes: Sequence[WanChange],
        current_ips: Mapping[str, str | None],
        observed_at: datetime,
    ) -> None:
        """Deliver one aggregated change notification."""


class MonitorLogger(Protocol):
    """Small structured logger interface used by the service."""

    def warning(self, event: str, **values: object) -> object:
        """Record a warning."""

    def error(self, event: str, **values: object) -> object:
        """Record an error."""


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Summary of one monitor cycle."""

    notified: bool
    notification_failed: bool
    changes: tuple[WanChange, ...]


class MonitorService:
    """Run one complete collection/compare/notify cycle."""

    def __init__(
        self,
        wans: Mapping[str, Sequence[str]],
        prober: WanProber,
        state_store: StateRepository,
        notifier: Notifier | None = None,
        logger: MonitorLogger | None = None,
    ) -> None:
        self._wans = {wan: tuple(urls) for wan, urls in wans.items()}
        self._prober = prober
        self._state_store = state_store
        self._notifier = notifier
        self._logger = logger or structlog.get_logger(__name__)

    async def run_once(self) -> CycleResult:
        """Collect every WAN once and apply the baseline/notification rules."""

        previous = await self._state_store.load()
        observations = await asyncio.gather(
            *(self._prober.probe_wan(wan, urls) for wan, urls in self._wans.items())
        )
        current_ips: dict[str, str | None] = {
            wan: previous.get(wan) for wan in self._wans
        }
        changes: list[WanChange] = []
        for observation in observations:
            if observation.ip is None:
                continue
            previous_ip = previous.get(observation.wan)
            current_ips[observation.wan] = observation.ip
            if previous_ip is not None and previous_ip != observation.ip:
                changes.append(WanChange(observation.wan, previous_ip, observation.ip))

        merged_ips = {wan: ip for wan, ip in current_ips.items() if ip is not None}
        ordered_changes = tuple(sorted(changes, key=lambda change: change.wan))
        if not ordered_changes:
            await self._state_store.save(merged_ips)
            return CycleResult(False, False, ())

        if self._notifier is None:
            self._logger.warning(
                "ip_change_notification_disabled",
                changed_wans=[change.wan for change in ordered_changes],
            )
            await self._state_store.save(merged_ips)
            return CycleResult(False, False, ordered_changes)

        try:
            await self._notifier.send(
                ordered_changes,
                current_ips,
                datetime.now(UTC),
            )
        except TeamsDeliveryError as exc:
            self._logger.error(
                "ip_change_notification_failed",
                error_type=type(exc).__name__,
            )
            return CycleResult(False, True, ordered_changes)

        await self._state_store.save(merged_ips)
        return CycleResult(True, False, ordered_changes)
