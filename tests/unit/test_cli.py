import asyncio
from pathlib import Path

import pytest

from public_ip_notifier.cli import build_parser, run_loop
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


def test_build_parser_when_config_and_once_are_given_then_parses_arguments() -> None:
    args = build_parser().parse_args(["--config", "config.yaml", "--once"])

    assert args.config == Path("config.yaml")
    assert args.once is True


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
