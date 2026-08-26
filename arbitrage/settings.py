from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from arbitrage.config import AppConfig

ROOT = Path(__file__).resolve().parent.parent


def load_app_config(path: str | Path | None = None) -> AppConfig:
    load_dotenv(ROOT / ".env")
    config_path = Path(path) if path else ROOT / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    cfg = AppConfig.model_validate(raw)
    _fill_credential_env_names(cfg)
    return cfg


def _fill_credential_env_names(cfg: AppConfig) -> None:
    for name, exchange in cfg.exchanges.items():
        prefix = name.upper()
        exchange.api_key_env = exchange.api_key_env or f"{prefix}_API_KEY"
        exchange.secret_env = exchange.secret_env or f"{prefix}_SECRET"
        exchange.password_env = exchange.password_env or f"{prefix}_PASSWORD"


def credentials_for(cfg: AppConfig, exchange: str) -> dict[str, str]:
    spec = cfg.exchanges.get(exchange)
    if spec is None:
        return {}
    creds: dict[str, str] = {}
    key = os.getenv(spec.api_key_env or "", "")
    secret = os.getenv(spec.secret_env or "", "")
    password = os.getenv(spec.password_env or "", "")
    if key:
        creds["apiKey"] = key
    if secret:
        creds["secret"] = secret
    if password:
        creds["password"] = password
    return creds


def live_env_enabled() -> bool:
    return os.getenv("LIVE_TRADING", "").strip() == "1"
