# WAN IP Conflict Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject cross-WAN IP collisions, try remaining probe URLs for a conflict-free candidate combination, and discard the complete cycle when no such combination exists.

**Architecture:** Keep `IpProbe` unchanged and add a private lazy candidate stream to `MonitorService`. Prime one candidate per WAN concurrently, then use deterministic asynchronous backtracking to request remaining URLs only when earlier candidates conflict with another WAN's persisted or current IP.

**Tech Stack:** Python 3.11+, asyncio, pytest, structlog, uv, Ruff, mypy

---

## File Map

- Modify `tests/unit/test_monitor.py`: add a URL-aware fake prober, a recording logger, and conflict/fallback regression tests.
- Modify `src/public_ip_notifier/services/monitor.py`: lazily enumerate each WAN's valid URL results and choose a conflict-free combination before state comparison.
- Modify `README.md`: document cross-WAN collision fallback and atomic cycle discard behavior.

### Task 1: Add Cross-WAN Conflict Regression Coverage And Selection

**Files:**
- Modify: `tests/unit/test_monitor.py`
- Modify: `src/public_ip_notifier/services/monitor.py`

- [x] **Step 1: Add URL-aware test doubles**

Add these test doubles after `RecordingProber` in `tests/unit/test_monitor.py`:

```python
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
```

Add this factory after the existing `make_service` helper:

```python
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
```

- [x] **Step 2: Write the failing current-cycle duplicate fallback test**

Append this test to `tests/unit/test_monitor.py`:

```python
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
```

- [x] **Step 3: Write the failing persisted-state collision fallback test**

Append:

```python
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

    assert result.changes == (
        WanChange("wan1", "203.0.113.10", "203.0.113.11"),
    )
    assert store.saved == {
        "wan1": "203.0.113.11",
        "wan2": "198.51.100.20",
    }
```

- [x] **Step 4: Write the failing backtracking test**

This case proves the implementation does not greedily discard a cycle when the
first WAN can move to another candidate and leave its first candidate for the
second WAN:

```python
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
```

- [x] **Step 5: Write the failing atomic-discard test**

Append:

```python
@pytest.mark.asyncio
async def test_monitor_when_all_ip_combinations_conflict_then_discards_cycle() -> (
    None
):
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
```

Update the service import at the top of the test file so the final assertion
can construct the result:

```python
from public_ip_notifier.services.monitor import CycleResult, MonitorService
```

- [x] **Step 6: Run the four tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_monitor.py -q
```

Expected: the four new tests fail because `MonitorService` accepts the first
successful observation from each WAN, does not try remaining URLs after a
cross-WAN conflict, and still persists duplicate results.

- [x] **Step 7: Add the lazy WAN candidate stream**

Add this private class immediately before `MonitorService` in
`src/public_ip_notifier/services/monitor.py`:

```python
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
            candidate.ip
            for candidate in self._candidates
            if candidate.ip is not None
        )

    async def candidate(self, index: int) -> WanObservation | None:
        """Return a candidate by URL order, loading remaining URLs as needed."""

        while len(self._candidates) <= index and not self._exhausted:
            await self._load_next()
        if index >= len(self._candidates):
            return None
        return self._candidates[index]

    async def _load_next(self) -> None:
        remaining_urls = self._target.urls[self._next_url_index :]
        if not remaining_urls:
            self._exhausted = True
            return

        remaining_target = WanProbeTarget(
            urls=remaining_urls,
            networks=self._target.networks,
        )
        observation = await self._prober.probe_wan(self._wan, remaining_target)
        if observation.ip is None:
            self._exhausted = True
            return

        source_url = observation.source_url
        if source_url is None or source_url not in remaining_urls:
            raise ValueError("probe returned an IP without a configured source URL")
        self._next_url_index += remaining_urls.index(source_url) + 1

        if observation.ip not in self.ips:
            self._candidates.append(observation)
```

- [x] **Step 8: Add deterministic asynchronous backtracking**

Add this private method inside `MonitorService`, immediately before
`run_once()`:

```python
    async def _select_consistent_observations(
        self,
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
```

- [x] **Step 9: Integrate candidate selection before change comparison**

Replace the initial probe block in `MonitorService.run_once()`:

```python
        observations = await asyncio.gather(
            *(
                self._prober.probe_wan(wan, target)
                for wan, target in self._servers.items()
            )
        )
```

with:

```python
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
```

Leave the existing `current_ips`, `changes`, notifier, and state-save logic
unchanged. Empty streams represent ordinary all-URL probe failures and are
omitted from selection, so their previous state continues to be preserved.

- [x] **Step 10: Run monitor tests and verify GREEN**

Run:

```powershell
uv run pytest tests/unit/test_monitor.py -q
```

Expected: all monitor tests pass, including the four new conflict cases and the
existing ordinary probe-failure behavior.

- [x] **Step 11: Run focused lint, formatting, and type checks**

Run:

```powershell
uv run ruff check src/public_ip_notifier/services/monitor.py tests/unit/test_monitor.py
uv run ruff format --check src/public_ip_notifier/services/monitor.py tests/unit/test_monitor.py
uv run mypy src/public_ip_notifier/services/monitor.py
```

Expected: every command exits with code 0. If formatting is required, run
`uv run ruff format` on the two files, review the diff, and repeat all three
checks.

- [x] **Step 12: Commit the tested behavior**

```powershell
git add src/public_ip_notifier/services/monitor.py tests/unit/test_monitor.py
git commit -m "fix(monitor): reject cross-WAN IP conflicts"
```

### Task 2: Document Conflict Fallback Behavior

**Files:**
- Modify: `README.md`

- [x] **Step 1: Add the runtime behavior paragraph**

After the paragraph ending in `no change notification is sent.` add:

```markdown
The monitor also rejects an IP returned for more than one WAN, or an IP that
matches another WAN's persisted value. It tries the affected WAN's remaining
URLs and uses the first conflict-free combination in configured order. If no
conflict-free combination exists, the complete cycle is discarded without a
state update or notification.
```

- [x] **Step 2: Check documentation formatting and scope**

Run:

```powershell
git diff --check
git diff -- README.md
```

Expected: `git diff --check` exits with code 0 and the README diff contains
only the new conflict behavior paragraph.

- [x] **Step 3: Commit the documentation**

```powershell
git add README.md
git commit -m "docs(monitor): explain WAN IP conflict fallback"
```

### Task 3: Run Full Verification

**Files:**
- Verify all source, tests, and documentation changed by this plan.

- [x] **Step 1: Run Ruff lint**

Run:

```powershell
uv run ruff check .
```

Expected: exit code 0 and `All checks passed!`.

- [x] **Step 2: Run Ruff formatting check**

Run:

```powershell
uv run ruff format --check .
```

Expected: exit code 0 and all Python files already formatted.

- [x] **Step 3: Run strict type checking on the changed module**

Run:

```powershell
uv run mypy src/public_ip_notifier/services/monitor.py
```

Expected: exit code 0 with no type errors.

- [x] **Step 4: Run the full offline test suite**

Run:

```powershell
uv run pytest -q
```

Expected: exit code 0 with every test passing and no real network access.

- [x] **Step 5: Inspect final repository state**

Run:

```powershell
git status --short
git diff --check HEAD
git log -3 --oneline
```

Expected: no uncommitted files, no whitespace errors, and the history contains
the scoped implementation and documentation commits. Confirm that
`pyproject.toml`, `uv.lock`, configuration files, secrets, migrations, and
generated files were not changed.
