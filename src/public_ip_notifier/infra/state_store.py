"""Atomic JSON persistence for the latest successful WAN IPs."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path


class StateStoreError(RuntimeError):
    """Raised when the persisted state cannot be read or written."""


class StateStore:
    """Persist a mapping of WAN names to their latest known IPs."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> dict[str, str]:
        """Load the state mapping, returning an empty mapping when absent."""

        return await asyncio.to_thread(self._load_sync)

    async def save(self, ips: Mapping[str, str]) -> None:
        """Atomically replace the state file with the supplied mapping."""

        await asyncio.to_thread(self._save_sync, dict(ips))

    def _load_sync(self) -> dict[str, str]:
        try:
            content = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise StateStoreError(f"failed to read state file {self._path}") from exc

        try:
            loaded: object = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StateStoreError(f"invalid JSON in state file {self._path}") from exc

        if not isinstance(loaded, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in loaded.items()
        ):
            raise StateStoreError("state file must contain a string mapping")
        return {str(key): str(value) for key, value in loaded.items()}

    def _save_sync(self, ips: Mapping[str, str]) -> None:
        parent = self._path.parent
        temporary_path: Path | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=parent,
                text=True,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(dict(ips), temporary_file, ensure_ascii=True, sort_keys=True)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
        except OSError as exc:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
            raise StateStoreError(f"failed to write state file {self._path}") from exc
