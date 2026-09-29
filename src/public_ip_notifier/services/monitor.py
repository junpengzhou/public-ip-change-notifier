"""Coordinate WAN probes, state comparisons, and notifications."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog

from public_ip_notifier.domain.models import WanChange, WanObservation, WanProbeTarget
from public_ip_notifier.infra.teams import TeamsDeliveryError


class WanProber(Protocol):
    """Probe interface required by the monitor service."""

    async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
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


class _WanCandidateStream:
    """Lazily load valid observations from one WAN's remaining URLs."""

    def __init__(
            self,
            wan: str,
            target: WanProbeTarget,
            prober: WanProber,
    ) -> None:
        self._wan = wan
        self._target = target
        self._prober = prober
        self._next_url_index = 0
        self._candidates: list[WanObservation] = []
        self._exhausted = False

    @property
    def has_candidate(self) -> bool:
        """Return whether at least one valid IP has been observed."""

        return bool(self._candidates)

    @property
    def ips(self) -> tuple[str, ...]:
        """Return the distinct IP candidates loaded so far."""

        return tuple(
            candidate.ip for candidate in self._candidates if candidate.ip is not None
        )

    async def candidate(self, index: int) -> WanObservation | None:
        """Return a candidate by URL order, loading remaining URLs as needed."""

        while len(self._candidates) <= index and not self._exhausted:
            await self._load_next()
        if index >= len(self._candidates):
            return None
        return self._candidates[index]

    async def _load_next(self) -> None:
        while self._next_url_index < len(self._target.urls):
            url = self._target.urls[self._next_url_index]
            self._next_url_index += 1
            single_url_target = WanProbeTarget(
                urls=(url,),
                networks=self._target.networks,
            )
            observation = await self._prober.probe_wan(
                self._wan,
                single_url_target,
            )
            if observation.ip is None:
                continue
            if observation.source_url != url:
                raise ValueError("probe returned an IP from an unexpected source URL")
            if observation.ip in self.ips:
                continue

            self._candidates.append(observation)
            return

        self._exhausted = True


class MonitorService:
    """Run one complete collection/compare/notify cycle."""

    def __init__(
            self,
            servers: Mapping[str, WanProbeTarget],
            prober: WanProber,
            state_store: StateRepository,
            notifier: Notifier | None = None,
            logger: MonitorLogger | None = None,
    ) -> None:
        self._servers = dict(servers)
        self._prober = prober
        self._state_store = state_store
        self._notifier = notifier
        self._logger = logger or structlog.get_logger(__name__)

    @staticmethod
    async def _select_consistent_observations(
            streams: Mapping[str, _WanCandidateStream],
            previous: Mapping[str, str],
    ) -> tuple[WanObservation, ...] | None:
        active_streams = [
            (wan, stream) for wan, stream in streams.items() if stream.has_candidate
        ]
        selected: list[WanObservation] = []

        async def search(index: int, used_ips: set[str]) -> bool:
            if index == len(active_streams):
                return True

            wan, stream = active_streams[index]
            forbidden_ips = {
                ip for other_wan, ip in previous.items() if other_wan != wan
            }
            candidate_index = 0
            while (candidate := await stream.candidate(candidate_index)) is not None:
                candidate_index += 1
                candidate_ip = candidate.ip
                if candidate_ip is None:
                    continue
                if candidate_ip in forbidden_ips or candidate_ip in used_ips:
                    continue

                selected.append(candidate)
                if await search(index + 1, used_ips | {candidate_ip}):
                    return True
                selected.pop()

            return False

        if not await search(0, set()):
            return None
        return tuple(selected)

    async def run_once(self) -> CycleResult:
        """Collect every WAN once and apply the baseline/notification rules."""

        previous = await self._state_store.load()
        streams = {
            wan: _WanCandidateStream(wan, target, self._prober)
            for wan, target in self._servers.items()
        }
        await asyncio.gather(*(stream.candidate(0) for stream in streams.values()))
        observations = await self._select_consistent_observations(streams, previous)
        if observations is None:
            self._logger.warning(
                "wan_ip_cycle_discarded",
                reason="cross_wan_ip_conflict",
                candidates={wan: stream.ips for wan, stream in streams.items()},
            )
            return CycleResult(False, False, ())

        current_ips: dict[str, str | None] = {
            wan: previous.get(wan) for wan in self._servers
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
                changed_servers=[change.wan for change in ordered_changes],
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
