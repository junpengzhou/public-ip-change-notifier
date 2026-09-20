"""Pure domain models used by the notifier service."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network
from typing import TypeAlias

IpNetwork: TypeAlias = IPv4Network | IPv6Network


@dataclass(frozen=True, slots=True)
class WanProbeTarget:
    """Probe URLs and allowed IP networks for one WAN."""

    urls: tuple[str, ...]
    networks: tuple[IpNetwork, ...]


@dataclass(frozen=True, slots=True)
class WanObservation:
    """Result of trying all configured probe URLs for one WAN."""

    wan: str
    ip: str | None
    source_url: str | None


@dataclass(frozen=True, slots=True)
class WanChange:
    """A known WAN IP change detected during one monitoring cycle."""

    wan: str
    previous_ip: str
    current_ip: str
