from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.paths import DATA_DIR


class NetworkSecretStore:
    """Small local fallback for development and appliance images.

    Production deployments should prefer NetworkManager's own connection
    store or an OS secret backend. This file intentionally lives outside the
    normal SQLite application data and is never exposed through APIs.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_DIR / "system" / "network-secrets.json"

    def get(self, key: str) -> dict[str, Any]:
        return dict(self._read().get(key) or {})

    def set(self, key: str, value: dict[str, Any]) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> None:
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)

