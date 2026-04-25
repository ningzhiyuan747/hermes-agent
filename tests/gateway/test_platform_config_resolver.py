from __future__ import annotations

from pathlib import Path

from gateway.config import HomeChannel, Platform, PlatformConfig
from gateway.platform_config_resolver import resolve_profile_backed_platform_config


def _write_feishu_profile_env(root: Path, *lines: str) -> None:
    env_path = root / "profiles" / "feishu" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("\n".join(lines), encoding="utf-8")


def test_resolver_passes_through_non_feishu_platform(tmp_path, monkeypatch):
    cfg = PlatformConfig(enabled=True, token="tok")

    monkeypatch.setattr(
        "gateway.platform_config_resolver.get_default_hermes_root",
        lambda: tmp_path,
    )

    resolved = resolve_profile_backed_platform_config(Platform.TELEGRAM, cfg)

    assert resolved is cfg


def test_resolver_uses_existing_feishu_credentials_when_present(tmp_path, monkeypatch):
    cfg = PlatformConfig(
        enabled=True,
        extra={"app_id": "existing-app", "app_secret": "existing-secret"},
    )
    _write_feishu_profile_env(
        tmp_path,
        "FEISHU_APP_ID=profile-app",
        "FEISHU_APP_SECRET=profile-secret",
    )
    monkeypatch.setattr(
        "gateway.platform_config_resolver.get_default_hermes_root",
        lambda: tmp_path,
    )

    resolved = resolve_profile_backed_platform_config(Platform.FEISHU, cfg)

    assert resolved is cfg
    assert resolved.extra["app_id"] == "existing-app"


def test_resolver_loads_feishu_credentials_and_home_channel_from_profile_env(tmp_path, monkeypatch):
    _write_feishu_profile_env(
        tmp_path,
        "FEISHU_APP_ID=profile-app",
        "FEISHU_APP_SECRET=profile-secret",
        "FEISHU_DOMAIN=feishu",
        "FEISHU_CONNECTION_MODE=websocket",
        "FEISHU_HOME_CHANNEL=oc_profilehome",
        "FEISHU_HOME_CHANNEL_NAME=Profile Home",
    )
    monkeypatch.setattr(
        "gateway.platform_config_resolver.get_default_hermes_root",
        lambda: tmp_path,
    )

    resolved = resolve_profile_backed_platform_config(Platform.FEISHU, None)

    assert resolved is not None
    assert resolved.enabled is True
    assert resolved.extra["app_id"] == "profile-app"
    assert resolved.extra["app_secret"] == "profile-secret"
    assert resolved.extra["domain"] == "feishu"
    assert resolved.extra["connection_mode"] == "websocket"
    assert isinstance(resolved.home_channel, HomeChannel)
    assert resolved.home_channel.chat_id == "oc_profilehome"
    assert resolved.home_channel.name == "Profile Home"
