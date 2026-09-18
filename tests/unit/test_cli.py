import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from public_ip_notifier.cli import (
    _install_shutdown_handlers,
    build_parser,
    run_loop,
)
from public_ip_notifier.services.monitor import CycleResult


class FakeRunner:
    def __init__(
        self, stop_event: asyncio.Event, stop_after: int | None = None
    ) -> None:
        self.stop_event = stop_event
        self.stop_after = stop_after
        self.calls = 0

    async def run_once(self) -> CycleResult:
        self.calls += 1
        if self.stop_after is not None and self.calls >= self.stop_after:
            self.stop_event.set()
        return CycleResult(False, False, ())


class FakeSignalLoop:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., object]] = []

    def add_signal_handler(
        self,
        signal_value: int,
        callback: Callable[..., object],
        *args: object,
    ) -> None:
        self.callbacks.append(callback)


def test_build_parser_when_config_and_once_are_given_then_parses_arguments() -> None:
    args = build_parser().parse_args(["--config", "config.yaml", "--once"])

    assert args.config == Path("config.yaml")
    assert args.once is True


def test_shutdown_handler_when_called_with_signal_arguments_then_sets_event() -> None:
    stop_event = asyncio.Event()
    loop = FakeSignalLoop()

    with patch("public_ip_notifier.cli.asyncio.get_running_loop", return_value=loop):
        _install_shutdown_handlers(stop_event)

    loop.callbacks[0](object())

    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_run_loop_when_once_is_true_then_runs_one_cycle() -> None:
    stop_event = asyncio.Event()
    runner = FakeRunner(stop_event)

    await run_loop(runner, interval_seconds=60, stop_event=stop_event, once=True)

    assert runner.calls == 1


@pytest.mark.asyncio
async def test_run_loop_when_second_cycle_stops_then_waits_between_cycles() -> None:
    stop_event = asyncio.Event()
    runner = FakeRunner(stop_event, stop_after=2)

    await run_loop(runner, interval_seconds=0.001, stop_event=stop_event, once=False)

    assert runner.calls == 2
