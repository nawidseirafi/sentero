from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_PATH, PROJECT_DIR


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def load_global_config() -> dict[str, Any]:
    return read_yaml(CONFIG_PATH)


def load_agent_section(agent_id: str, section: str | None = None) -> dict[str, Any]:
    config = load_global_config()
    key = section or agent_id
    value = config.get(key, {})
    return value if isinstance(value, dict) else {}


def resolve_project_path(value: Any, default: Path | str) -> Path:
    raw = value if value is not None else default
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (PROJECT_DIR / path).resolve()

