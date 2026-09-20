# WAN Public Network Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject public IP observations outside each WAN's configured CIDR ranges so router policy-routing failures cannot update state or send false notifications.

**Architecture:** Parse and validate CIDRs in the Pydantic configuration layer, then convert each WAN entry into an immutable domain target containing URLs and networks. Pass that target through the monitor service to `IpProbe`, where every returned address is checked for membership before it becomes a `WanObservation`; rejected results reuse the monitor's existing failed-probe behavior.

**Tech Stack:** Python 3.11, standard-library `ipaddress`, Pydantic 2, httpx, structlog, pytest, respx, mypy, Ruff

---

## File Map

- Modify `src/public_ip_notifier/config.py`: add the typed per-WAN configuration and validate non-empty CIDR/URL lists.
- Modify `src/public_ip_notifier/domain/models.py`: define the immutable probe target and shared IP-network type.
- Modify `src/public_ip_notifier/infra/probes.py`: enforce network membership and log rejected addresses.
- Modify `src/public_ip_notifier/services/monitor.py`: pass complete WAN targets to the probe adapter.
- Modify `src/public_ip_notifier/cli.py`: convert validated configuration into domain targets.
- Modify `tests/unit/test_config.py`: cover the new schema and invalid configurations.
- Modify `tests/unit/test_probes.py`: cover matching, rejection, fallback, and address-family mismatch.
- Modify `tests/unit/test_monitor.py`: adapt the fake and prove the target reaches the prober.
- Modify `tests/unit/test_cli.py`: prove configuration-to-domain conversion preserves URLs and CIDRs.
- Modify `config.yaml`: migrate WAN1 to `218.0.0.0/8` and WAN2 to `120.0.0.0/8` while preserving all current URLs.
- Modify `README.md`: document the required schema and rejection behavior.

### Task 1: Validate Structured WAN Configuration

**Files:**
- Modify: `tests/unit/test_config.py`
- Modify: `src/public_ip_notifier/config.py`

- [ ] **Step 1: Write failing configuration tests**

Change the valid YAML fixture to use the new shape and assert both IPv4 and
IPv6 networks are parsed:

```python
from ipaddress import IPv4Network, IPv6Network


def test_load_config_when_yaml_is_valid_then_reads_networks_and_wan_urls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "interval_seconds: 60\n"
        "state_file: ./state.json\n"
        "teams:\n"
        "  webhook_url: https://example.test/hook\n"
        "servers:\n"
        "  wan1:\n"
        "    networks:\n"
        "      - 203.0.113.0/24\n"
        "      - 2001:db8::/32\n"
        "    urls:\n"
        "      - https://ifconfig.me/ip\n"
        "      - https://ipinfo.io/ip\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.servers["wan1"].networks == (
        IPv4Network("203.0.113.0/24"),
        IPv6Network("2001:db8::/32"),
    )
    assert tuple(str(url) for url in config.servers["wan1"].urls) == (
        "https://ifconfig.me/ip",
        "https://ipinfo.io/ip",
    )
```

Add the strict validation cases:

```python
def test_load_config_when_wan_has_no_networks_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "servers:\n"
        "  wan1:\n"
        "    networks: []\n"
        "    urls:\n"
        "      - https://example.test/ip\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)


def test_load_config_when_wan_network_is_invalid_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "servers:\n"
        "  wan1:\n"
        "    networks:\n"
        "      - not-a-network\n"
        "    urls:\n"
        "      - https://example.test/ip\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)


def test_load_config_when_legacy_url_list_is_used_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "servers:\n  wan1:\n    - https://example.test/ip\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)
```

Update every pre-existing valid test fixture in this file so each `wan1` entry
contains `networks: [203.0.113.0/24]` and a nested `urls` list. Change the
existing empty-URL fixture to this exact shape:

```yaml
servers:
  wan1:
    networks:
      - 203.0.113.0/24
    urls: []
```

- [ ] **Step 2: Run the configuration tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_config.py -q
```

Expected: failures because `config.servers["wan1"]` is still a URL tuple and
the new mapping shape cannot be validated.

- [ ] **Step 3: Implement the typed WAN configuration**

Add these imports and model to `config.py`, then change `AppConfig.servers` and
its validator to use the model:

```python
from ipaddress import IPv4Network, IPv6Network


class WanConfig(BaseModel):
    """Validated probe URLs and allowed public networks for one WAN."""

    networks: tuple[IPv4Network | IPv6Network, ...]
    urls: tuple[HttpUrl, ...]

    @field_validator("networks")
    @classmethod
    def validate_networks(
        cls,
        value: tuple[IPv4Network | IPv6Network, ...],
    ) -> tuple[IPv4Network | IPv6Network, ...]:
        if not value:
            raise ValueError("each WAN must have at least one network")
        return value

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, value: tuple[HttpUrl, ...]) -> tuple[HttpUrl, ...]:
        if not value:
            raise ValueError("each WAN must have at least one URL")
        return value
```

```python
class AppConfig(BaseModel):
    """Validated settings required by the notifier service."""

    interval_seconds: PositiveInt = 60
    state_file: Path = Path("public-ip-state.json")
    teams: TeamsConfig = Field(default_factory=TeamsConfig)
    servers: dict[str, WanConfig]

    @field_validator("servers")
    @classmethod
    def validate_servers(
        cls, value: dict[str, WanConfig]
    ) -> dict[str, WanConfig]:
        if not value:
            raise ValueError("at least one WAN must be configured")
        if any(not name.strip() for name in value):
            raise ValueError("WAN names must not be empty")
        return value
```

- [ ] **Step 4: Run the configuration tests and verify GREEN**

Run `uv run pytest tests/unit/test_config.py -q`.

Expected: all tests in `test_config.py` pass.

- [ ] **Step 5: Commit the configuration schema**

```powershell
git add src/public_ip_notifier/config.py tests/unit/test_config.py
git commit -m "feat(config): require WAN public networks"
```

### Task 2: Reject Probe Results Outside Allowed Networks

**Files:**
- Modify: `tests/unit/test_probes.py`
- Modify: `src/public_ip_notifier/domain/models.py`
- Modify: `src/public_ip_notifier/infra/probes.py`

- [ ] **Step 1: Write a failing domain-target test**

Add these imports and a small contract test to `test_probes.py`. Using
`getattr` keeps pytest collection valid before the new type exists:

```python
from ipaddress import IPv4Network, IPv6Network

import public_ip_notifier.domain.models as domain_models


def test_wan_probe_target_when_created_then_preserves_urls_and_networks() -> None:
    target_type = getattr(domain_models, "WanProbeTarget", None)
    assert target_type is not None

    target = target_type(
        urls=("https://example.test/ip",),
        networks=(IPv4Network("218.0.0.0/8"),),
    )

    assert target.urls == ("https://example.test/ip",)
    assert target.networks == (IPv4Network("218.0.0.0/8"),)
```

- [ ] **Step 2: Run the domain-target test and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_probes.py::test_wan_probe_target_when_created_then_preserves_urls_and_networks -q
```

Expected: FAIL at `assert target_type is not None` because the domain target
does not exist.

- [ ] **Step 3: Add the domain target**

Add to `domain/models.py`:

```python
from ipaddress import IPv4Network, IPv6Network
from typing import TypeAlias

IpNetwork: TypeAlias = IPv4Network | IPv6Network


@dataclass(frozen=True, slots=True)
class WanProbeTarget:
    """Probe URLs and allowed IP networks for one WAN."""

    urls: tuple[str, ...]
    networks: tuple[IpNetwork, ...]
```

- [ ] **Step 4: Run the domain-target test and verify GREEN**

Run the command from Step 2 again.

Expected: PASS.

- [ ] **Step 5: Write failing probe behavior tests**

Import the new type and add a recording logger to `test_probes.py`:

```python
from public_ip_notifier.domain.models import WanObservation, WanProbeTarget


class RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **values: object) -> object:
        self.warnings.append((event, values))
        return None
```

Update each existing probe call to pass a `WanProbeTarget`. Use
`IPv4Network("198.51.100.0/24")` for the existing IPv4 success test,
`IPv6Network("2001:db8::/32")` for the IPv6 success test, and either valid
network for the HTTP-failure test. Then add:

```python
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
async def test_probe_when_all_addresses_are_outside_network_then_returns_empty() -> None:
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
```

- [ ] **Step 6: Run probe tests and verify RED**

Run `uv run pytest tests/unit/test_probes.py -q`.

Expected: probe calls fail because `IpProbe.probe_wan` still accepts a URL
sequence and does not enforce the target's networks.

- [ ] **Step 7: Add the probe membership check**

Remove the now-unused `Sequence` import from `infra/probes.py`. Import
`WanProbeTarget`, change `IpProbe.probe_wan` to accept the target, iterate over
`target.urls`, and insert this check immediately after parsing `address` and
before returning an observation:

```python
async def probe_wan(self, wan: str, target: WanProbeTarget) -> WanObservation:
    """Return the first allowed IP returned by a WAN's configured URLs."""

    for url in target.urls:
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            address = ipaddress.ip_address(response.text.strip())
        except (
            httpx.TimeoutException,
            httpx.RequestError,
            httpx.HTTPStatusError,
            ValueError,
        ) as exc:
            self._logger.warning(
                "probe_url_failed",
                wan=wan,
                url=url,
                error_type=type(exc).__name__,
            )
            continue

        if not any(address in network for network in target.networks):
            self._logger.warning(
                "probe_ip_outside_allowed_networks",
                wan=wan,
                url=url,
                ip=str(address),
                allowed_networks=[str(network) for network in target.networks],
            )
            continue

        return WanObservation(wan, str(address), url)

    return WanObservation(wan, None, None)
```

- [ ] **Step 8: Run probe tests and verify GREEN**

Run `uv run pytest tests/unit/test_probes.py -q`.

Expected: all probe tests pass, including fallback and address-family mismatch.

- [ ] **Step 9: Commit the probe defense**

```powershell
git add src/public_ip_notifier/domain/models.py src/public_ip_notifier/infra/probes.py tests/unit/test_probes.py
git commit -m "feat(probes): reject IPs outside WAN networks"
```

### Task 3: Carry WAN Targets Through Service And CLI

**Files:**
- Modify: `tests/unit/test_monitor.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `src/public_ip_notifier/services/monitor.py`
- Modify: `src/public_ip_notifier/cli.py`

- [ ] **Step 1: Write failing service and conversion tests**

In `test_monitor.py`, import `IPv4Network` and `WanProbeTarget`, replace the
fake prober and `make_service` with these exact versions, then add the
target-recording test:

```python
class FakeProber:
    def __init__(self, observations: Mapping[str, str | None]) -> None:
        self._observations = observations

    async def probe_wan(
        self, wan: str, target: WanProbeTarget
    ) -> WanObservation:
        ip = self._observations[wan]
        return WanObservation(wan, ip, target.urls[0] if ip is not None else None)


class RecordingProber(FakeProber):
    def __init__(self, observations: Mapping[str, str | None]) -> None:
        super().__init__(observations)
        self.targets: list[tuple[str, WanProbeTarget]] = []

    async def probe_wan(
        self, wan: str, target: WanProbeTarget
    ) -> WanObservation:
        self.targets.append((wan, target))
        return await super().probe_wan(wan, target)


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
```

In `test_cli.py`, import `IPv4Network`, `WanConfig`, and the CLI module, then
add:

```python
def test_build_wan_targets_when_config_is_valid_then_preserves_values() -> None:
    server = WanConfig.model_validate(
        {
            "networks": ["218.0.0.0/8"],
            "urls": ["https://example.test/ip"],
        }
    )

    targets = cli._build_wan_targets({"wan1": server})

    assert targets["wan1"].urls == ("https://example.test/ip",)
    assert targets["wan1"].networks == (IPv4Network("218.0.0.0/8"),)
```

- [ ] **Step 2: Run the service and CLI tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_monitor.py tests/unit/test_cli.py -q
```

Expected: failures because `MonitorService` still expects URL sequences and
`_build_wan_targets` does not exist.

- [ ] **Step 3: Update the service contract**

Change the prober protocol and constructor storage in `monitor.py`:

```python
class WanProber(Protocol):
    """Probe interface required by the monitor service."""

    async def probe_wan(
        self, wan: str, target: WanProbeTarget
    ) -> WanObservation:
        """Collect one observation for a WAN."""
```

```python
def __init__(
    self,
    servers: Mapping[str, WanProbeTarget],
    prober: WanProber,
    state_store: StateRepository,
    notifier: Notifier | None = None,
    logger: MonitorLogger | None = None,
) -> None:
    self._servers = dict(servers)
```

Change the gather expression to:

```python
observations = await asyncio.gather(
    *(
        self._prober.probe_wan(wan, target)
        for wan, target in self._servers.items()
    )
)
```

- [ ] **Step 4: Add and use the CLI conversion helper**

Import `Mapping`, `WanConfig`, and `WanProbeTarget` in `cli.py`, then add:

```python
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
```

Replace the URL-only dictionary in `_run` with:

```python
servers = _build_wan_targets(config.servers)
```

- [ ] **Step 5: Run service and CLI tests and verify GREEN**

Run `uv run pytest tests/unit/test_monitor.py tests/unit/test_cli.py -q`.

Expected: all tests in both files pass.

- [ ] **Step 6: Commit the service wiring**

```powershell
git add src/public_ip_notifier/services/monitor.py src/public_ip_notifier/cli.py tests/unit/test_monitor.py tests/unit/test_cli.py
git commit -m "feat(monitor): pass WAN networks to probes"
```

### Task 4: Migrate Runtime And Documented Configuration

**Files:**
- Modify: `config.yaml`
- Modify: `README.md`

- [ ] **Step 1: Migrate `config.yaml` without dropping existing URLs**

Use this exact server section:

```yaml
servers:
  wan1:
    networks:
      - "218.0.0.0/8"
    urls:
      - "https://ifconfig.me/ip"
      - "https://ipinfo.io/ip"
      - "https://api-ipv4.ip.sb/ip"
  wan2:
    networks:
      - "120.0.0.0/8"
    urls:
      - "https://icanhazip.com"
      - "https://ipecho.net/plain"
      - "https://checkip.amazonaws.com"
```

Update the preceding comments to say networks are required CIDRs and URLs are
tried in order.

- [ ] **Step 2: Verify the migrated runtime configuration loads**

Run:

```powershell
uv run python -c "from pathlib import Path; from public_ip_notifier.config import load_config; config = load_config(Path('config.yaml')); assert str(config.servers['wan1'].networks[0]) == '218.0.0.0/8'; assert str(config.servers['wan2'].networks[0]) == '120.0.0.0/8'"
```

Expected: exit code 0 with no output.

- [ ] **Step 3: Update README configuration and behavior**

Replace the README `servers` example with the nested `networks`/`urls` shape.
Use `218.0.0.0/8` and `120.0.0.0/8`, explain that multiple IPv4 or IPv6 CIDRs
may be supplied, and add this behavior paragraph:

```markdown
Each returned address must belong to at least one network configured for that
WAN. An address outside every allowed network is logged and the next URL is
tried. If no URL returns an allowed address, the previous value is retained and
no change notification is sent.
```

- [ ] **Step 4: Commit configuration and documentation**

Review `git diff -- config.yaml` first to confirm the three pre-existing URL
additions remain present, then run:

```powershell
git add config.yaml README.md
git commit -m "docs(config): document WAN network allowlists"
```

### Task 5: Full Verification

**Files:**
- Verify all modified source, tests, and documentation.

- [ ] **Step 1: Run Ruff lint**

Run `uv run ruff check .`.

Expected: exit code 0 and `All checks passed!`.

- [ ] **Step 2: Run Ruff formatting check**

Run `uv run ruff format --check .`.

Expected: exit code 0 and all files already formatted.

- [ ] **Step 3: Run strict type checking on changed modules**

Run:

```powershell
uv run mypy src/public_ip_notifier/config.py src/public_ip_notifier/domain/models.py src/public_ip_notifier/infra/probes.py src/public_ip_notifier/services/monitor.py src/public_ip_notifier/cli.py
```

Expected: exit code 0 with no issues.

- [ ] **Step 4: Run the full offline test suite**

Run `uv run pytest -q`.

Expected: exit code 0 with every test passing and no real network access.

- [ ] **Step 5: Check the final diff**

Run `git status --short`, `git diff --check HEAD`, and inspect `git diff HEAD`.
Confirm no dependency files changed, no debug output was added, all network
tests use `respx`, and only the files listed in this plan changed.
