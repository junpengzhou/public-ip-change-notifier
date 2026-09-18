# Public IP Change Notifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python 3.11+ asynchronous CLI that polls configured WAN-specific public-IP endpoints, persists baselines locally, and sends a single Teams summary when a known WAN IP changes.

**Architecture:** Load YAML into validated Pydantic models with environment overrides, probe servers concurrently while trying each WAN's URLs sequentially, and coordinate state comparison/notification in a service layer. Persist state through an atomic JSON store and keep HTTP, Teams, and filesystem adapters behind focused infrastructure classes.

**Tech Stack:** Python 3.11+, `uv`, `asyncio`, `httpx`, `pydantic`, `pydantic-settings`, `PyYAML`, `structlog`, `pytest`, `pytest-asyncio`, `respx`, `ruff`, and `mypy`.

---

## File Map

- Create `pyproject.toml`: runtime/dev dependencies, Ruff/mypy/pytest settings, and the `public-ip-notifier` console script.
- Create `README.md`: configuration example, state semantics, and run instructions.
- Create `src/public_ip_notifier/__init__.py`: package version.
- Create `src/public_ip_notifier/__main__.py`: `python -m public_ip_notifier` entrypoint.
- Create `src/public_ip_notifier/config.py`: YAML loading, environment overrides, and Pydantic validation.
- Create `src/public_ip_notifier/domain/models.py`: immutable probe, state, and change models.
- Create `src/public_ip_notifier/infra/probes.py`: asynchronous URL fallback probing.
- Create `src/public_ip_notifier/infra/state_store.py`: asynchronous JSON load and atomic save.
- Create `src/public_ip_notifier/infra/teams.py`: Teams MessageCard construction and webhook delivery.
- Create `src/public_ip_notifier/services/monitor.py`: one-cycle orchestration and notification/state transaction.
- Create `src/public_ip_notifier/cli.py`: argument parsing, client lifecycle, polling loop, and signal shutdown.
- Create `tests/unit/test_config.py`: configuration and environment behavior.
- Create `tests/unit/test_state_store.py`: state persistence behavior.
- Create `tests/unit/test_probes.py`: fallback and IP parsing behavior.
- Create `tests/unit/test_teams.py`: MessageCard and HTTP response behavior.
- Create `tests/unit/test_monitor.py`: first-run, change, failure, and aggregation behavior.
- Create `tests/unit/test_cli.py`: one-shot and interval/shutdown behavior.

## Task 1: Scaffold the package and toolchain

**Files:**
- Create: `pyproject.toml`
- Create: `src/public_ip_notifier/__init__.py`
- Create: `src/public_ip_notifier/__main__.py`
- Create: `README.md`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the project metadata and smoke test**

Create `pyproject.toml` with the runtime dependencies and the development extra. Keep runtime dependency rationale in comments because YAML, async HTTP, settings validation, and structured logging are not provided together by the standard library.

```toml
[project]
name = "public-ip-change-notifier"
version = "0.1.0"
description = "Monitor public IP addresses by WAN and notify Microsoft Teams"
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27,<1",
  "pydantic>=2.7,<3",
  "pydantic-settings>=2.2,<3",
  "PyYAML>=6.0,<7",
  "structlog>=24.0,<26",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.10,<2",
  "pytest>=8.2,<9",
  "pytest-asyncio>=0.23,<1",
  "respx>=0.21,<1",
  "ruff>=0.5,<1",
]

[project.scripts]
public-ip-notifier = "public_ip_notifier.cli:main"

[build-system]
requires = ["setuptools>=70,<76"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
addopts = "-ra"
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
```

Create the first failing smoke test in `tests/test_package.py`:

```python
from public_ip_notifier import __version__


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: Run the smoke test and confirm the intended failure**

Run `uv run pytest tests/test_package.py::test_package_exposes_version -q`.
Expected result: collection fails because `public_ip_notifier` does not exist yet.

- [ ] **Step 3: Add the minimal package and entrypoint**

Create `src/public_ip_notifier/__init__.py`:

```python
"""Public IP change notifier package."""

__version__ = "0.1.0"
```

Create `src/public_ip_notifier/__main__.py`:

```python
"""Run the public IP notifier as a module."""

from public_ip_notifier.cli import main


if __name__ == "__main__":
    main()
```

Add empty `__init__.py` files under `tests/` and each future package directory when those directories are created.

- [ ] **Step 4: Verify the scaffold is green**

Run `uv sync --extra dev` and then `uv run pytest tests/test_package.py::test_package_exposes_version -q`.
Expected result: one passing test and no import error.

- [ ] **Step 5: Add the initial usage documentation**

Create `README.md` with the approved YAML example, the command `uv run public-ip-notifier --config config.yaml`, the first-run/no-notification rule, and the state-file/webhook retry behavior. Do not place credentials in the example; state that `PUBLIC_IP_NOTIFIER_TEAMS_WEBHOOK_URL` can override the YAML webhook.

- [ ] **Step 6: Commit the scaffold**

Run:

```text
git add pyproject.toml README.md src tests/test_package.py
git commit -m "chore(project): scaffold notifier package"
```

## Task 2: Implement validated YAML configuration

**Files:**
- Create: `src/public_ip_notifier/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Create `tests/unit/test_config.py` with these behaviors:

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from public_ip_notifier.config import load_config


def test_load_config_reads_wan_urls_and_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "interval_seconds: 60\n"
        "state_file: ./state.json\n"
        "teams:\n"
        "  webhook_url: https://example.test/hook\n"
        "servers:\n"
        "  wan1:\n"
        "    - https://ifconfig.me/ip\n"
        "    - https://ipinfo.io/ip\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.interval_seconds == 60
    assert config.servers["wan1"] == (
        "https://ifconfig.me/ip",
        "https://ipinfo.io/ip",
    )
    assert config.state_file == Path("state.json")


def test_load_config_rejects_non_positive_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("interval_seconds: 0\nservers: {wan1: [https://example.test]}\n")

    with pytest.raises(ValidationError):
        load_config(path)
```

Add an environment override test using `monkeypatch.setenv("PUBLIC_IP_NOTIFIER_INTERVAL_SECONDS", "15")` and assert the loaded interval is `15`; use `monkeypatch` only for the test's process environment, not application globals.

- [ ] **Step 2: Run the configuration tests and verify they fail**

Run `uv run pytest tests/unit/test_config.py -q`.
Expected result: collection fails because `public_ip_notifier.config` is missing.

- [ ] **Step 3: Implement the configuration models and loader**

Implement these public types in `config.py`:

```python
class TeamsConfig(BaseModel):
    webhook_url: SecretStr | None = None


class AppConfig(BaseModel):
    interval_seconds: PositiveInt = 60
    state_file: Path = Path("public-ip-state.json")
    teams: TeamsConfig = TeamsConfig()
    servers: dict[str, tuple[HttpUrl, ...]]


def load_config(path: Path) -> AppConfig:
    """Load and validate YAML configuration with environment overrides."""
```

Use a private `EnvironmentSettings(BaseSettings)` with `validation_alias` fields for `PUBLIC_IP_NOTIFIER_INTERVAL_SECONDS`, `PUBLIC_IP_NOTIFIER_STATE_FILE`, and `PUBLIC_IP_NOTIFIER_TEAMS_WEBHOOK_URL`. Read YAML with `yaml.safe_load`, require a mapping, merge only non-`None` environment values into the corresponding top-level/nested fields, and call `AppConfig.model_validate`. Convert each URL to `str` at the boundary where `HttpUrl` values are passed to the probe. Validate that every WAN name is non-empty and every URL tuple is non-empty with a Pydantic model validator. Catch only `OSError` and `yaml.YAMLError` while reading/parsing and raise a typed `ConfigLoadError` with the original exception as its cause.

- [ ] **Step 4: Run the focused tests and format the module**

Run `uv run pytest tests/unit/test_config.py -q`, then `uv run ruff format src/public_ip_notifier/config.py tests/unit/test_config.py` and `uv run ruff check src/public_ip_notifier/config.py tests/unit/test_config.py`.
Expected result: all configuration tests pass and Ruff reports no violations.

- [ ] **Step 5: Commit configuration support**

Run `git add src/public_ip_notifier/config.py tests/unit/test_config.py && git commit -m "feat(config): load validated yaml settings"`.

## Task 3: Add domain models and atomic state persistence

**Files:**
- Create: `src/public_ip_notifier/domain/__init__.py`
- Create: `src/public_ip_notifier/domain/models.py`
- Create: `src/public_ip_notifier/infra/__init__.py`
- Create: `src/public_ip_notifier/infra/state_store.py`
- Create: `tests/unit/test_state_store.py`

- [ ] **Step 1: Write failing state tests**

```python
import json
from pathlib import Path

import pytest

from public_ip_notifier.infra.state_store import StateStore, StateStoreError


@pytest.mark.asyncio
async def test_state_store_saves_and_loads_ip_mapping(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")

    await store.save({"wan1": "203.0.113.10"})

    assert await store.load() == {"wan1": "203.0.113.10"}


@pytest.mark.asyncio
async def test_state_store_returns_empty_mapping_when_file_is_absent(
    tmp_path: Path,
) -> None:
    assert await StateStore(tmp_path / "missing.json").load() == {}


@pytest.mark.asyncio
async def test_state_store_rejects_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(StateStoreError, match="invalid JSON"):
        await StateStore(path).load()
```

- [ ] **Step 2: Run the state tests and confirm they fail**

Run `uv run pytest tests/unit/test_state_store.py -q`.
Expected result: collection fails because `StateStore` is not defined.

- [ ] **Step 3: Implement models and the store**

Define frozen dataclasses in `domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class WanObservation:
    wan: str
    ip: str | None
    source_url: str | None


@dataclass(frozen=True, slots=True)
class WanChange:
    wan: str
    previous_ip: str
    current_ip: str
```

Implement `StateStore(path: Path)` with async `load() -> dict[str, str]` and `save(ips: Mapping[str, str]) -> None` methods. Delegate the small blocking file operations to `asyncio.to_thread`. `load` returns `{}` for `FileNotFoundError`, parses JSON, verifies the root is a JSON object whose keys and values are strings, and wraps `OSError`, `json.JSONDecodeError`, and invalid shape errors in `StateStoreError`. `save` creates the parent directory, writes UTF-8 JSON to a named temporary file in the target directory, calls `flush()` and `os.fsync()`, then uses `os.replace`; delete a leftover temporary file on write failure and re-raise a `StateStoreError` with the original `OSError`.

- [ ] **Step 4: Verify state behavior and static checks**

Run `uv run pytest tests/unit/test_state_store.py -q`, `uv run ruff check src/public_ip_notifier/domain src/public_ip_notifier/infra/state_store.py tests/unit/test_state_store.py`, and `uv run mypy src/public_ip_notifier/domain src/public_ip_notifier/infra/state_store.py`.
Expected result: all tests pass and both checkers exit successfully.

- [ ] **Step 5: Commit state persistence**

Run `git add src/public_ip_notifier/domain src/public_ip_notifier/infra tests/unit/test_state_store.py && git commit -m "feat(state): persist WAN IP baselines atomically"`.

## Task 4: Implement asynchronous URL fallback probing

**Files:**
- Create: `src/public_ip_notifier/infra/probes.py`
- Create: `tests/unit/test_probes.py`

- [ ] **Step 1: Write failing probe tests**

Use `respx.mock` with an injected `httpx.AsyncClient` and test these exact cases:

```python
@pytest.mark.asyncio
async def test_probe_uses_second_url_after_first_request_error() -> None:
    first = respx.get("https://first.test/ip").mock(side_effect=httpx.ConnectError("down"))
    second = respx.get("https://second.test/ip").mock(return_value=httpx.Response(200, text="198.51.100.20"))

    async with httpx.AsyncClient() as client:
        result = await IpProbe(client).probe_wan("wan1", (str(first.url), str(second.url)))

    assert result == WanObservation("wan1", "198.51.100.20", str(second.url))
    assert first.called and second.called


@pytest.mark.asyncio
async def test_probe_skips_invalid_ip_response() -> None:
    respx.get("https://invalid.test/ip").mock(return_value=httpx.Response(200, text="unknown"))
    respx.get("https://valid.test/ip").mock(return_value=httpx.Response(200, text="2001:db8::1"))

    async with httpx.AsyncClient() as client:
        result = await IpProbe(client).probe_wan(
            "wan1", ("https://invalid.test/ip", "https://valid.test/ip")
        )

    assert result.ip == "2001:db8::1"
```

Add a test that all URLs failing returns `WanObservation("wan1", None, None)` and does not raise.

- [ ] **Step 2: Run the probe tests and confirm the expected failure**

Run `uv run pytest tests/unit/test_probes.py -q`.
Expected result: collection fails because `IpProbe` is missing.

- [ ] **Step 3: Implement `IpProbe`**

Implement `IpProbe(client: httpx.AsyncClient, logger: structlog.stdlib.BoundLogger | None = None)` and:

```python
async def probe_wan(
    self, wan: str, urls: Sequence[str]
) -> WanObservation:
    """Return the first valid IP returned by a WAN's configured URLs."""
```

For each URL, call `client.get(url)`, then `response.raise_for_status()`, strip the response text, and parse it with `ipaddress.ip_address`. Catch only `httpx.TimeoutException`, `httpx.RequestError`, `httpx.HTTPStatusError`, and `ValueError`; log the WAN, URL, and failure category with `logger.warning`. Return the first valid string representation from the response text and its URL. After the loop, return an observation with `None` values. Do not perform retries outside the configured URL fallback.

- [ ] **Step 4: Verify probe behavior**

Run `uv run pytest tests/unit/test_probes.py -q` and `uv run ruff check src/public_ip_notifier/infra/probes.py tests/unit/test_probes.py`.
Expected result: all fallback tests pass without network access.

- [ ] **Step 5: Commit probing**

Run `git add src/public_ip_notifier/infra/probes.py tests/unit/test_probes.py && git commit -m "feat(probe): add WAN URL fallback probing"`.

## Task 5: Add Teams MessageCard delivery

**Files:**
- Create: `src/public_ip_notifier/infra/teams.py`
- Create: `tests/unit/test_teams.py`

- [ ] **Step 1: Write failing Teams tests**

Test that `TeamsNotifier.send(changes, current_ips, observed_at)` POSTs to the configured URL with a MessageCard containing every current WAN IP and each old/new value. Register the URL with `respx`, assert `request.headers["content-type"]` begins with `application/json`, decode `request.content`, and assert `payload["@type"] == "MessageCard"`. Add a second test where the route returns HTTP 500 and assert a typed `TeamsDeliveryError` is raised.

- [ ] **Step 2: Run the Teams tests and confirm failure**

Run `uv run pytest tests/unit/test_teams.py -q`.
Expected result: collection fails because `TeamsNotifier` is missing.

- [ ] **Step 3: Implement the notifier**

Define `TeamsDeliveryError(RuntimeError)` and:

```python
class TeamsNotifier:
    def __init__(self, client: httpx.AsyncClient, webhook_url: str) -> None:
        self._client = client
        self._webhook_url = webhook_url

    async def send(
        self,
        changes: Sequence[WanChange],
        current_ips: Mapping[str, str | None],
        observed_at: datetime,
    ) -> None:
        """Send one aggregated Teams MessageCard for an IP change cycle."""
```

Build deterministic facts sorted by WAN name. Use `unknown` for `None`, include the ISO-8601 UTC timestamp, and include old/new values for every `WanChange`. POST JSON with `client.post`; catch `httpx.TimeoutException`, `httpx.RequestError`, and `httpx.HTTPStatusError`, then raise `TeamsDeliveryError` from the original exception. Call `response.raise_for_status()` so every non-2xx response follows the same error path.

- [ ] **Step 4: Verify and commit Teams delivery**

Run `uv run pytest tests/unit/test_teams.py -q` and `uv run ruff check src/public_ip_notifier/infra/teams.py tests/unit/test_teams.py`, then commit with:

```text
git add src/public_ip_notifier/infra/teams.py tests/unit/test_teams.py
git commit -m "feat(teams): send aggregated IP change cards"
```

## Task 6: Orchestrate a monitor cycle with transactional notification

**Files:**
- Create: `src/public_ip_notifier/services/__init__.py`
- Create: `src/public_ip_notifier/services/monitor.py`
- Create: `tests/unit/test_monitor.py`

- [ ] **Step 1: Write failing monitor tests**

Use fake probe/store/notifier objects with explicit async methods. Cover:

```python
@pytest.mark.asyncio
async def test_first_cycle_saves_baseline_without_notifying() -> None:
    service = make_service(previous={}, observations={"wan1": "203.0.113.10"})

    result = await service.run_once()

    assert result.notified is False
    assert service.notifier.calls == []
    assert service.store.saved == {"wan1": "203.0.113.10"}


@pytest.mark.asyncio
async def test_known_ip_change_notifies_all_current_servers_and_then_saves() -> None:
    service = make_service(
        previous={"wan1": "203.0.113.10", "wan2": "198.51.100.20"},
        observations={"wan1": "203.0.113.11", "wan2": "198.51.100.20"},
    )

    result = await service.run_once()

    assert result.notified is True
    assert service.notifier.calls[0].current_ips == {
        "wan1": "203.0.113.11",
        "wan2": "198.51.100.20",
    }
    assert service.store.saved == service.notifier.calls[0].current_ips


@pytest.mark.asyncio
async def test_webhook_failure_keeps_previous_state_for_retry() -> None:
    service = make_service(
        previous={"wan1": "203.0.113.10"},
        observations={"wan1": "203.0.113.11"},
        notifier_error=TeamsDeliveryError("webhook down"),
    )

    result = await service.run_once()

    assert result.notification_failed is True
    assert service.store.saved is None
```

Also test that a `None` observation keeps the prior IP, that a WAN with no prior value establishes a baseline without notification, and that multiple changed servers produce one notifier call.

- [ ] **Step 2: Run monitor tests and confirm failure**

Run `uv run pytest tests/unit/test_monitor.py -q`.
Expected result: collection fails because `MonitorService` is missing.

- [ ] **Step 3: Implement the monitor service**

Define `CycleResult` with `notified: bool`, `notification_failed: bool`, and `changes: tuple[WanChange, ...]`. Implement:

```python
class MonitorService:
    async def run_once(self) -> CycleResult:
        """Collect every WAN once and apply the baseline/notification rules."""
```

Load the previous mapping, call all `probe_wan` methods through `asyncio.gather`, merge successful observations over previous values, and build changes only where a previous value exists and differs from a non-`None` observation. If there are no changes, save the merged mapping and return. If changes exist and the notifier is `None`, log a warning, save the merged mapping, and return `notified=False`; this is the intentional disabled-notification mode. If a notifier exists, call it once with sorted current IPs. Catch only `TeamsDeliveryError`, log it, return `notification_failed=True`, and do not save. Save the merged mapping only after a successful notification or when no notification is required.

- [ ] **Step 4: Verify orchestration and commit**

Run `uv run pytest tests/unit/test_monitor.py -q`, `uv run ruff check src/public_ip_notifier/services/monitor.py tests/unit/test_monitor.py`, and `uv run mypy src/public_ip_notifier/services/monitor.py`.
Expected result: all monitor tests pass.

Commit with `git add src/public_ip_notifier/services tests/unit/test_monitor.py && git commit -m "feat(service): coordinate IP baselines and notifications"`.

## Task 7: Build the CLI, polling loop, and graceful shutdown

**Files:**
- Create: `src/public_ip_notifier/cli.py`
- Create: `tests/unit/test_cli.py`
- Modify: `src/public_ip_notifier/__main__.py`

- [ ] **Step 1: Write failing CLI tests**

Use a fake service and a stop event. Test that `run_loop(..., once=True)` invokes `run_once` exactly once. Test the interval branch by setting `interval_seconds=0.01`, having the fake service set the stop event on its first call, and asserting the loop exits without `time.sleep`. Test `build_parser()` accepts `--config config.yaml` and `--once`.

- [ ] **Step 2: Run the CLI tests and confirm failure**

Run `uv run pytest tests/unit/test_cli.py -q`.
Expected result: collection fails because `run_loop` and `build_parser` are missing.

- [ ] **Step 3: Implement the CLI**

Implement these typed functions:

```python
def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""


async def run_loop(
    service: MonitorService,
    interval_seconds: int,
    stop_event: asyncio.Event,
    once: bool,
) -> None:
    """Run an immediate cycle and then wait between cycles until stopped."""


def main() -> None:
    """Parse arguments and run the asynchronous notifier."""
```

The loop runs one cycle immediately. For recurring mode, use `await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)` and run another cycle only after `asyncio.TimeoutError`; this makes shutdown interrupt the wait without blocking. In `main`, load the config, construct one `httpx.AsyncClient(timeout=10.0)`, `IpProbe`, `StateStore`, optional `TeamsNotifier`, and `MonitorService` inside an `async with` lifecycle. Register `SIGINT` and `SIGTERM` with `loop.add_signal_handler` where supported, catching only `NotImplementedError`/`RuntimeError` for platforms without it. Let configuration/state errors produce a non-zero exit; log expected Teams delivery failures through the service.

- [ ] **Step 4: Verify CLI behavior and module entrypoint**

Run `uv run pytest tests/unit/test_cli.py -q`, `uv run python -m public_ip_notifier --help`, and `uv run ruff check src/public_ip_notifier/cli.py src/public_ip_notifier/__main__.py tests/unit/test_cli.py`.
Expected result: tests pass and help lists `--config` and `--once`.

- [ ] **Step 5: Commit the CLI**

Run `git add src/public_ip_notifier/cli.py src/public_ip_notifier/__main__.py tests/unit/test_cli.py && git commit -m "feat(cli): add configurable polling service"`.

## Task 8: Documentation, full verification, and release checks

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-18-public-ip-notifier-design.md` only if implementation reveals a confirmed contract correction

- [ ] **Step 1: Complete README operational instructions**

Document installation with `uv sync --extra dev`, the YAML schema, environment variable names, state-file permissions, `--once` for a single offline/operational cycle, and the fact that the first successful baseline does not notify. Include a systemd-style command example without hard-coded credentials and a Windows Task Scheduler note that the normal mode is a long-running process.

- [ ] **Step 2: Run the complete test suite**

Run `uv run pytest -q`.
Expected result: every test passes; no test performs real network IO.

- [ ] **Step 3: Run formatting and lint checks**

Run `uv run ruff format --check .` and `uv run ruff check .`.
Expected result: both commands exit 0 with no suggested changes.

- [ ] **Step 4: Run type checks**

Run `uv run mypy src/public_ip_notifier`.
Expected result: success with no errors under the configured strict settings.

- [ ] **Step 5: Exercise the installed CLI without network access**

Run `uv run public-ip-notifier --help` and verify it exits 0. Run `uv run public-ip-notifier --config tests/fixtures/config.yaml --once` only with an HTTP-mocked test harness or a deliberately invalid endpoint fixture; do not call public services from verification.

- [ ] **Step 6: Inspect the final diff and status**

Run `git diff --check`, `git status --short`, and `git log --oneline -8`. Confirm no `.env`, webhook secret, generated file, debug `print()`, broad exception handler, or untracked runtime state file is included.

- [ ] **Step 7: Final commit for documentation/verification changes**

Run `git add README.md` and commit with `docs(readme): document notifier operations` if README changed. Do not amend or rewrite earlier commits; preserve unrelated user changes.

## Plan Self-Review

- Spec coverage: configuration and env overrides are Task 2; URL fallback and parsing are Task 4; atomic state is Task 3; Teams aggregation and failure transaction are Task 5/6; lifecycle and signals are Task 7; offline tests and all required checks are Task 8.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation step is required; every task names files, signatures, commands, and expected outcomes.
- Type consistency: `WanObservation` and `WanChange` are defined in Task 3 and consumed by Tasks 4-6; `TeamsDeliveryError` is defined in Task 5 and caught only by Task 6; `MonitorService.run_once` and `CycleResult` are defined in Task 6 and consumed by Task 7.
- Scope check: all tasks implement one cohesive notifier service; there are no unrelated migrations, UI changes, or infrastructure refactors.
