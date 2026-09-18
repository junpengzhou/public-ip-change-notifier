import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from public_ip_notifier.config import load_config


def test_load_config_when_yaml_is_valid_then_reads_interval_and_wan_urls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "interval_seconds: 60\n"
        "state_file: ./state.json\n"
        "teams:\n"
        "  webhook_url: https://example.test/hook\n"
        "wans:\n"
        "  wan1:\n"
        "    - https://ifconfig.me/ip\n"
        "    - https://ipinfo.io/ip\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.interval_seconds == 60
    assert tuple(str(url) for url in config.wans["wan1"]) == (
        "https://ifconfig.me/ip",
        "https://ipinfo.io/ip",
    )
    assert config.state_file == Path("state.json")
    assert config.teams.webhook_url is not None
    assert config.teams.webhook_url.get_secret_value() == "https://example.test/hook"


def test_load_config_when_environment_overrides_interval_then_uses_override(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "interval_seconds: 60\nwans:\n  wan1:\n    - https://example.test/ip\n",
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"PUBLIC_IP_NOTIFIER_INTERVAL_SECONDS": "15"}):
        config = load_config(path)

    assert config.interval_seconds == 15


def test_load_config_when_interval_is_non_positive_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "interval_seconds: 0\nwans:\n  wan1:\n    - https://example.test/ip\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(path)


def test_load_config_when_wan_has_no_urls_then_rejects_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("wans:\n  wan1: []\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(path)
