from pathlib import Path

import pytest

from public_ip_notifier.infra.state_store import StateStore, StateStoreError


@pytest.mark.asyncio
async def test_state_store_when_mapping_saved_then_loads_same_ips(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data" / "state.json")

    await store.save({"wan1": "203.0.113.10"})

    assert await store.load() == {"wan1": "203.0.113.10"}


@pytest.mark.asyncio
async def test_state_store_when_file_is_absent_then_returns_empty_mapping(
    tmp_path: Path,
) -> None:
    assert await StateStore(tmp_path / "missing.json").load() == {}


@pytest.mark.asyncio
async def test_state_store_when_json_is_corrupt_then_raises_state_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(StateStoreError, match="invalid JSON"):
        await StateStore(path).load()


@pytest.mark.asyncio
async def test_state_store_when_value_is_not_string_then_raises_state_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"wan1": 123}', encoding="utf-8")

    with pytest.raises(StateStoreError, match="string mapping"):
        await StateStore(path).load()
