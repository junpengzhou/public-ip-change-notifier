"""Application configuration loading and validation."""

from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, Field, HttpUrl, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigLoadError(RuntimeError):
    """Raised when the YAML configuration cannot be loaded."""


class TeamsMention(BaseModel):
    """A Teams user that can be mentioned in a notification card."""

    name: str
    id: str

    @field_validator("name", "id")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Teams mention values must not be empty")
        return value


class TeamsConfig(BaseModel):
    """Teams notification configuration."""

    webhook_url: SecretStr | None = None
    mentions: tuple[TeamsMention, ...] = ()


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


class AppConfig(BaseModel):
    """Validated settings required by the notifier service."""

    interval_seconds: PositiveInt = 60
    state_file: Path = Path("public-ip-state.json")
    teams: TeamsConfig = Field(default_factory=TeamsConfig)
    servers: dict[str, WanConfig]

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, value: dict[str, WanConfig]) -> dict[str, WanConfig]:
        if not value:
            raise ValueError("at least one WAN must be configured")
        if any(not name.strip() for name in value):
            raise ValueError("WAN names must not be empty")
        return value


class EnvironmentSettings(BaseSettings):
    """Environment variable overrides for file-based settings."""

    model_config = SettingsConfigDict(extra="ignore")

    interval_seconds: int | None = Field(
        default=None,
        validation_alias="PUBLIC_IP_NOTIFIER_INTERVAL_SECONDS",
    )
    state_file: Path | None = Field(
        default=None,
        validation_alias="PUBLIC_IP_NOTIFIER_STATE_FILE",
    )
    teams_webhook_url: SecretStr | None = Field(
        default=None,
        validation_alias="PUBLIC_IP_NOTIFIER_TEAMS_WEBHOOK_URL",
    )


def _read_yaml(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            loaded: object = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigLoadError(f"failed to load configuration from {path}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ConfigLoadError("configuration root must be a mapping")
    if any(not isinstance(key, str) for key in loaded):
        raise ConfigLoadError("configuration keys must be strings")
    return cast(dict[str, object], dict(loaded))


def _apply_environment(
    values: dict[str, object], environment: EnvironmentSettings
) -> dict[str, object]:
    merged = dict(values)
    if environment.interval_seconds is not None:
        merged["interval_seconds"] = environment.interval_seconds
    if environment.state_file is not None:
        merged["state_file"] = environment.state_file
    if environment.teams_webhook_url is not None:
        teams_value = merged.get("teams", {})
        if not isinstance(teams_value, Mapping):
            raise ConfigLoadError("teams configuration must be a mapping")
        teams = dict(teams_value)
        teams["webhook_url"] = environment.teams_webhook_url
        merged["teams"] = teams
    return merged


def load_config(path: Path) -> AppConfig:
    """Load and validate YAML configuration with environment overrides."""

    values = _read_yaml(path)
    environment = EnvironmentSettings()
    return AppConfig.model_validate(_apply_environment(values, environment))
