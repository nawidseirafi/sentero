from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from backend.paths import CONFIG_PATH, DATA_DIR, ENV_PATH, PROJECT_DIR

load_dotenv(ENV_PATH)

VERSION_FILE = PROJECT_DIR / "version.json"
MANIFEST_FILE = PROJECT_DIR / "update-manifest.json"
STATE_FILE = DATA_DIR / "system" / "update_state.json"
BACKUP_DIR = PROJECT_DIR / "backups"
DEFAULT_VERSION = "0.1.0"
DEFAULT_CHANNEL = "stable"
VALID_CHANNELS = {"stable", "beta", "dev"}
COPY_NAMES = {
    "backend",
    "frontend",
    "config",
    "docker",
    "requirements.txt",
    "docker-compose.yml",
    "README.md",
    "version.json",
    "update-manifest.json",
}
NEVER_OVERWRITE = {".env", "data", "backups", ".venv", "venv", "node_modules", "__pycache__"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SenteroUpdateService:
    def status(self) -> dict[str, Any]:
        state = self._read_json(STATE_FILE, {})
        version = self.version()
        latest = state.get("latest") if isinstance(state.get("latest"), dict) else None
        return {
            "product": "Sentero",
            "current_version": version["version"],
            "latest_version": latest.get("latest_version") if latest else state.get("latest_version"),
            "status": state.get("status") or state.get("state") or "idle",
            "state": state.get("state") or state.get("status") or "idle",
            "last_checked": state.get("last_checked"),
            "release_notes": state.get("release_notes") or (latest.get("release_notes") if latest else []),
            "steps": state.get("steps") or [],
            "message": state.get("message") or "Ihre Installation ist auf dem neuesten Stand.",
            "version": version,
            "channel": self.channel(),
            "execution_mode": self.execution_mode(),
            "update_server_url": self.manifest_url() or str(self.manifest_path()),
            "latest": latest,
            "update_available": bool(state.get("update_available")),
            "install": state.get("install") or {"status": "idle", "steps": []},
            "rollback": state.get("rollback") or {"status": "idle", "available": False},
            "last_error": state.get("last_error"),
            "dev_mode": self.execution_mode() == "dry_run",
        }

    def version(self) -> dict[str, Any]:
        metadata = self._read_json(VERSION_FILE, {})
        return {
            "edition": "sentero",
            "app_version": str(metadata.get("version") or DEFAULT_VERSION),
            "version": str(metadata.get("version") or DEFAULT_VERSION),
            "build": str(metadata.get("build") or datetime.now().strftime("%Y.%m.%d")),
            "commit": str(metadata.get("commit") or "unknown"),
            "channel": self.channel(),
            "updated_at": metadata.get("updated_at"),
        }

    def check_for_updates(self, channel: str | None = None) -> dict[str, Any]:
        selected_channel = self._valid_channel(channel or self.channel())
        checked_at = utc_now()
        current = self.version()
        try:
            manifest = self._load_manifest()
            latest = self._latest_from_manifest(manifest, selected_channel)
            available = self._is_newer(str(latest.get("latest_version") or ""), current["version"])
            state = {
                **self.status(),
                "status": "update_available" if available else "idle",
                "state": "update_available" if available else "idle",
                "last_checked": checked_at,
                "latest": latest,
                "latest_version": latest.get("latest_version"),
                "release_notes": latest.get("release_notes") or [],
                "update_available": available,
                "message": "Ein Update ist verfuegbar." if available else "Ihre Installation ist auf dem neuesten Stand.",
                "last_error": None,
            }
            self._write_json(STATE_FILE, state)
            return {
                "ok": True,
                "offline": False,
                "product": "Sentero",
                "current": current,
                "current_version": current["version"],
                "channel": selected_channel,
                "latest": latest,
                "available": available,
                "update_available": available,
                "latest_version": latest.get("latest_version"),
                "release_notes": latest.get("release_notes") or [],
                "checked_at": checked_at,
                "last_checked": checked_at,
                "status": state["status"],
                "message": state["message"],
            }
        except Exception as exc:
            state = {
                **self.status(),
                "status": "check_failed",
                "state": "check_failed",
                "last_checked": checked_at,
                "update_available": False,
                "last_error": str(exc),
                "message": "Update-Pruefung fehlgeschlagen.",
            }
            self._write_json(STATE_FILE, state)
            return {
                "ok": False,
                "offline": True,
                "product": "Sentero",
                "current": current,
                "current_version": current["version"],
                "channel": selected_channel,
                "latest": None,
                "available": False,
                "update_available": False,
                "checked_at": checked_at,
                "last_checked": checked_at,
                "status": "check_failed",
                "message": "Update-Pruefung fehlgeschlagen.",
                "error": str(exc),
            }

    def install_update(self, username: str = "sentero", layer: str = "auto") -> dict[str, Any]:
        status = self.status()
        latest = status.get("latest")
        if not latest:
            self.check_for_updates()
            status = self.status()
            latest = status.get("latest")
        if not latest or not status.get("update_available"):
            return {**status, "message": "Kein Update verfuegbar."}

        steps = [
            {"key": "prepare", "label": "Vorbereitung", "status": "pending"},
            {"key": "backup", "label": "Sicherung", "status": "pending"},
            {"key": "install", "label": "Installation", "status": "pending"},
            {"key": "done", "label": "Fertig", "status": "pending"},
        ]
        state = {**status, "status": "running", "state": "running", "steps": steps, "install": {"status": "running", "layer": layer, "steps": steps, "started_at": utc_now()}}
        self._write_json(STATE_FILE, state)
        try:
            self._step(steps, "prepare", "running")
            self._persist_install_progress(state, steps, layer)
            self._step(steps, "prepare", "success")

            self._step(steps, "backup", "running")
            self._persist_install_progress(state, steps, layer)
            backup = self._backup()
            self._step(steps, "backup", "success")

            self._step(steps, "install", "running")
            self._persist_install_progress(state, steps, layer)
            if self.execution_mode() == "dry_run":
                self._step(steps, "install", "success")
            elif self.execution_mode() == "zip":
                self._install_zip(latest)
                self._step(steps, "install", "success")
            else:
                raise RuntimeError(f"Unsupported update mode: {self.execution_mode()}")

            self._step(steps, "done", "running")
            self._persist_install_progress(state, steps, layer)
            self._step(steps, "done", "success")
            final = {
                **state,
                "status": "success",
                "state": "success",
                "steps": steps,
                "update_available": False,
                "message": "Update erfolgreich vorbereitet." if self.execution_mode() == "dry_run" else "Update erfolgreich installiert.",
                "backup": backup,
                "install": {"status": "success", "layer": layer, "target_version": latest.get("latest_version"), "steps": steps, "finished_at": utc_now()},
                "rollback": {"status": "idle", "available": bool(backup), "previous_version": status.get("current_version")},
            }
            self._write_json(STATE_FILE, final)
            return final
        except Exception as exc:
            self._step(steps, "done", "failed")
            failed = {
                **state,
                "status": "failed",
                "state": "failed",
                "steps": steps,
                "last_error": str(exc),
                "message": "Update fehlgeschlagen.",
                "install": {"status": "failed", "layer": layer, "steps": steps, "finished_at": utc_now()},
            }
            self._write_json(STATE_FILE, failed)
            return failed

    def channel(self) -> str:
        return self._valid_channel(str(self._update_config().get("channel") or os.getenv("SENTERO_UPDATE_CHANNEL") or DEFAULT_CHANNEL))

    def execution_mode(self) -> str:
        mode = str(self._update_config().get("mode") or os.getenv("SENTERO_UPDATE_MODE") or "dry_run").strip().lower()
        return mode if mode in {"dry_run", "zip"} else "dry_run"

    def manifest_url(self) -> str:
        config = self._update_config()
        configured = str(
            config.get("manifest_url")
            or os.getenv("SENTERO_UPDATE_MANIFEST_URL")
            or os.getenv("UPDATE_MANIFEST_URL")
            or ""
        ).strip()
        if configured:
            return configured
        base_url = str(config.get("base_url") or os.getenv("UPDATE_BASE_URL") or os.getenv("SENTERO_UPDATE_BASE_URL") or "").strip()
        return f"{base_url.rstrip('/')}/stable/latest.json" if base_url else ""

    def manifest_path(self) -> Path:
        raw = self._update_config().get("manifest_path") or os.getenv("SENTERO_UPDATE_MANIFEST_PATH")
        if raw:
            path = Path(str(raw)).expanduser()
            return path if path.is_absolute() else PROJECT_DIR / path
        return MANIFEST_FILE

    def _load_manifest(self) -> dict[str, Any]:
        url = self.manifest_url()
        if url:
            with urllib.request.urlopen(url, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        path = self.manifest_path()
        if not path.exists():
            raise FileNotFoundError(f"Update manifest not found: {path}")
        return self._read_json(path, {})

    def _latest_from_manifest(self, manifest: dict[str, Any], channel: str) -> dict[str, Any]:
        if "channels" in manifest:
            channels = manifest.get("channels") if isinstance(manifest.get("channels"), dict) else {}
            latest = channels.get(channel) or channels.get(DEFAULT_CHANNEL) or {}
        else:
            latest = manifest
        if not isinstance(latest, dict):
            raise ValueError("Invalid update manifest")
        version = latest.get("latest_version") or latest.get("version")
        if not version:
            raise ValueError("Update manifest has no latest_version")
        return {
            "latest_version": str(version),
            "download_url": str(latest.get("download_url") or ""),
            "sha256": str(latest.get("sha256") or ""),
            "size_bytes": int(latest.get("size_bytes") or 0),
            "mandatory": bool(latest.get("mandatory", False)),
            "release_notes": latest.get("release_notes") or [],
            "channel": channel,
            "layers": latest.get("layers") or ["application"],
        }

    def _install_zip(self, latest: dict[str, Any]) -> None:
        url = str(latest.get("download_url") or "").strip()
        if not url:
            raise ValueError("Update manifest has no download_url")
        with tempfile.TemporaryDirectory(prefix="sentero-update-") as tmp:
            archive_path = Path(tmp) / "update.zip"
            self._download_update(url, archive_path)
            self._verify_archive_integrity(archive_path, latest)
            extract_dir = Path(tmp) / "extract"
            with zipfile.ZipFile(archive_path) as archive:
                self._extract_zip_safely(archive, extract_dir)
            root = self._single_root(extract_dir)
            for name in COPY_NAMES:
                source = root / name
                if source.exists() and name not in NEVER_OVERWRITE:
                    target = PROJECT_DIR / name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    if source.is_dir():
                        shutil.copytree(source, target)
                    else:
                        shutil.copy2(source, target)

    def _backup(self) -> dict[str, str] | None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / f"sentero-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        path.mkdir(parents=True, exist_ok=True)
        for name in COPY_NAMES:
            source = PROJECT_DIR / name
            if not source.exists():
                continue
            target = path / name
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__"))
            else:
                shutil.copy2(source, target)
        return {"path": str(path), "created_at": utc_now()}

    def _update_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return {}
        try:
            data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return {}
        updates = data.get("updates") if isinstance(data, dict) else {}
        return updates if isinstance(updates, dict) else {}

    def _read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(default)
        return data if isinstance(data, dict) else dict(default)

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _valid_channel(self, value: Any) -> str:
        channel = str(value or DEFAULT_CHANNEL).strip().lower()
        return channel if channel in VALID_CHANNELS else DEFAULT_CHANNEL

    def _is_newer(self, latest: str, current: str) -> bool:
        latest_parts = self._version_tuple(latest)
        current_parts = self._version_tuple(current)
        for latest_part, current_part in zip_longest(latest_parts, current_parts, fillvalue=0):
            if latest_part != current_part:
                return latest_part > current_part
        return False

    def _version_tuple(self, value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for part in value.replace("-", ".").split("."):
            try:
                parts.append(int(part))
            except ValueError:
                break
        return tuple(parts or [0])

    def _download_update(self, url: str, archive_path: Path) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in {"http", "https"}:
            with urllib.request.urlopen(url, timeout=60) as response:
                archive_path.write_bytes(response.read())
            return
        if parsed.scheme == "file":
            source = Path(urllib.request.url2pathname(parsed.path))
        elif not parsed.scheme:
            source = Path(url).expanduser()
            if not source.is_absolute():
                source = PROJECT_DIR / source
        else:
            raise ValueError(f"Unsupported update download URL: {url}")
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Update archive not found: {source}")
        shutil.copy2(source, archive_path)

    def _verify_archive_integrity(self, archive_path: Path, latest: dict[str, Any]) -> None:
        expected_sha256 = str(latest.get("sha256") or "").strip().lower()
        if not expected_sha256:
            raise ValueError("Update manifest has no sha256 checksum")
        if not is_valid_sha256(expected_sha256):
            raise ValueError("Update manifest has invalid sha256 checksum")
        actual_sha256 = file_sha256(archive_path)
        if actual_sha256 != expected_sha256:
            raise ValueError("Update archive checksum mismatch")
        expected_size = int(latest.get("size_bytes") or 0)
        if expected_size and archive_path.stat().st_size != expected_size:
            raise ValueError("Update archive size mismatch")

    def _extract_zip_safely(self, archive: zipfile.ZipFile, extract_dir: Path) -> None:
        extract_root = extract_dir.resolve()
        for member in archive.infolist():
            target = (extract_dir / member.filename).resolve()
            if target != extract_root and extract_root not in target.parents:
                raise ValueError(f"Unsafe path in update archive: {member.filename}")
        archive.extractall(extract_dir)

    def _persist_install_progress(self, state: dict[str, Any], steps: list[dict[str, str]], layer: str) -> None:
        current = {
            **state,
            "status": "running",
            "state": "running",
            "steps": steps,
            "install": {**(state.get("install") or {}), "status": "running", "layer": layer, "steps": steps},
        }
        self._write_json(STATE_FILE, current)

    def _step(self, steps: list[dict[str, str]], key: str, status: str) -> None:
        for step in steps:
            if step["key"] == key:
                step["status"] = status
                return

    def _single_root(self, extract_dir: Path) -> Path:
        children = [item for item in extract_dir.iterdir() if item.name != "__MACOSX"]
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return extract_dir
