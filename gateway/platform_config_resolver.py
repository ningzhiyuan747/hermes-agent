from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_constants import get_default_hermes_root

from .config import HomeChannel, Platform, PlatformConfig


_PLATFORM_PROFILE_DIR_NAMES: dict[str, str] = {
    "feishu": "feishu",
}


def _read_env_value_from_file(path: Path, key: str) -> str:
    try:
        if not path.exists():
            return ""
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current_key, value = line.split("=", 1)
            if current_key.strip() == key:
                return value.strip().strip("'\"")
    except Exception:
        return ""
    return ""


def profile_env_path_for_platform(platform: Platform | str) -> Path | None:
    if isinstance(platform, Platform):
        platform_name = platform.value
    else:
        platform_name = str(platform or "").strip().lower()
    profile_dir_name = _PLATFORM_PROFILE_DIR_NAMES.get(platform_name)
    if not profile_dir_name:
        return None
    root = get_default_hermes_root()
    path = root / "profiles" / profile_dir_name / ".env"
    return path if path.exists() else None


def _feishu_credentials_missing(pconfig: PlatformConfig | None) -> bool:
    extra = getattr(pconfig, "extra", {}) or {}
    app_id = str(extra.get("app_id") or "").strip()
    app_secret = str(extra.get("app_secret") or "").strip()
    return not (app_id and app_secret)


def resolve_profile_backed_platform_config(
    platform: Platform | str,
    pconfig: PlatformConfig | None,
) -> PlatformConfig | None:
    platform_name = platform.value if isinstance(platform, Platform) else str(platform or "").strip().lower()
    if platform_name != Platform.FEISHU.value:
        return pconfig

    if pconfig and pconfig.enabled and not _feishu_credentials_missing(pconfig):
        return pconfig

    env_path = profile_env_path_for_platform(platform_name)
    if env_path is None:
        return pconfig

    app_id = _read_env_value_from_file(env_path, "FEISHU_APP_ID")
    app_secret = _read_env_value_from_file(env_path, "FEISHU_APP_SECRET")
    if not (app_id and app_secret):
        return pconfig

    existing = pconfig or PlatformConfig()
    extra = dict(existing.extra or {})
    extra.update(
        {
            "app_id": app_id,
            "app_secret": app_secret,
            "domain": _read_env_value_from_file(env_path, "FEISHU_DOMAIN") or extra.get("domain") or "feishu",
            "connection_mode": _read_env_value_from_file(env_path, "FEISHU_CONNECTION_MODE")
            or extra.get("connection_mode")
            or "websocket",
        }
    )
    encrypt_key = _read_env_value_from_file(env_path, "FEISHU_ENCRYPT_KEY")
    verification_token = _read_env_value_from_file(env_path, "FEISHU_VERIFICATION_TOKEN")
    if encrypt_key:
        extra["encrypt_key"] = encrypt_key
    if verification_token:
        extra["verification_token"] = verification_token

    home_channel = existing.home_channel
    if not getattr(home_channel, "chat_id", ""):
        home_chat_id = _read_env_value_from_file(env_path, "FEISHU_HOME_CHANNEL")
        if home_chat_id:
            home_channel = HomeChannel(
                platform=Platform.FEISHU,
                chat_id=home_chat_id,
                name=_read_env_value_from_file(env_path, "FEISHU_HOME_CHANNEL_NAME") or "Home",
            )

    return PlatformConfig(
        enabled=True,
        token=existing.token,
        api_key=existing.api_key,
        home_channel=home_channel,
        reply_to_mode=existing.reply_to_mode,
        extra=extra,
    )
