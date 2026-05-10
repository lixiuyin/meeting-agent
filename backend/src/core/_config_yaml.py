"""YAML config loader — reads defaults from ``config/main.yaml``.

Extracted from ``config.py`` to keep the Settings class focused on field
definitions and validation.
"""

import threading
from typing import Any

import yaml

from .constants import DEFAULT_CONFIG_PATH

_YamlValue = Any
_yaml_cache: dict[str, _YamlValue] | None = None
_yaml_lock = threading.Lock()


def _load_yaml_config() -> dict[str, _YamlValue]:
    """Load default settings from YAML config file (cached)."""
    global _yaml_cache
    if _yaml_cache is None:
        with _yaml_lock:
            if _yaml_cache is None:
                if DEFAULT_CONFIG_PATH.exists():
                    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
                        loaded = yaml.safe_load(f)
                        _yaml_cache = loaded if isinstance(loaded, dict) else {}
                else:
                    _yaml_cache = {}
    return _yaml_cache


def reload_yaml_config() -> None:
    """Clear the YAML config cache so the next read reloads from disk."""
    global _yaml_cache
    with _yaml_lock:
        _yaml_cache = None


def _yaml_get(*keys: str, default: _YamlValue = None) -> _YamlValue:
    """Safely read a nested key from the cached YAML config."""
    d: _YamlValue = _load_yaml_config()
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key, default)
    return d
