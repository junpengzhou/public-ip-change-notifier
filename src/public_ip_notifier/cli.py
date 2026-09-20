"""Command-line entrypoint and long-running monitor loop."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import httpx
import structlog
from pydantic import ValidationError

from public_ip_notifier.config import ConfigLoadError, WanConfig, load_config
from public_ip_notifier.domain.models import WanProbeTarget
from public_ip_notifier.infra.probes import IpProbe
from public_ip_notifier.infra.state_store import StateStore, StateStoreError
from public_ip_notifier.infra.teams import TeamsNotifier
from public_ip_notifier.services.monitor import CycleResult, MonitorService


class CycleRunner(Protocol):
    """One-cycle interface used by the polling loop."""

    async def run_once(self) -> CycleResult:
        """Run one collection cycle."""


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Monitor public IP addresses by WAN and notify Teams."
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the YAML configuration file.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one collection cycle and exit.",
    )
    return parser


async def run_loop(
    service: CycleRunner,
    interval_seconds: float,
    stop_event: asyncio.Event,
    once: bool,
) -> None:
    """Run an immediate cycle and then wait between cycles until stopped."""

    await service.run_once()
    if once:
        return

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            await service.run_once()


def _install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    def request_shutdown(*_args: object) -> object:
        stop_event.set()
        return None

    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is None:
            continue
        try:
            loop.add_signal_handler(signal_value, request_shutdown)
        except (NotImplementedError, RuntimeError, ValueError):
            signal.signal(signal_value, request_shutdown)


def _build_wan_targets(
    servers: Mapping[str, WanConfig],
) -> dict[str, WanProbeTarget]:
    """Convert validated WAN configuration into domain probe targets."""

    return {
        wan: WanProbeTarget(
            urls=tuple(str(url) for url in server.urls),
            networks=server.networks,
        )
        for wan, server in servers.items()
    }


async def _run(config_path: Path, once: bool) -> None:
    config = load_config(config_path)
    stop_event = asyncio.Event()
    _install_shutdown_handlers(stop_event)
    logger = structlog.get_logger("public_ip_notifier")
    servers = _build_wan_targets(config.servers)
    webhook_url = config.teams.webhook_url

    async with httpx.AsyncClient(timeout=10.0) as client:
        prober = IpProbe(client, logger)
        state_store = StateStore(config.state_file)
        notifier = (
            TeamsNotifier(
                client,
                webhook_url.get_secret_value(),
                mentions=config.teams.mentions,
            )
            if webhook_url is not None
            else None
        )
        service = MonitorService(servers, prober, state_store, notifier, logger)
        await run_loop(service, config.interval_seconds, stop_event, once)


def main() -> None:
    """Parse arguments and run the asynchronous notifier."""

    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()
    logger = structlog.get_logger("public_ip_notifier")
    try:
        asyncio.run(_run(args.config, args.once))
    except (ConfigLoadError, StateStoreError, ValidationError) as exc:
        logger.error("notifier_startup_failed", error_type=type(exc).__name__)
        raise SystemExit(1) from exc
