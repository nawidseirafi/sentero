#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
TARGET_DIR = BUILD_DIR / "sentero"
UPDATE_DIR = BUILD_DIR / "updates" / "sentero" / "stable"
RELEASE_DIR = UPDATE_DIR / "releases"
FRONTEND_DIR = ROOT / "frontend"

COPY_ITEMS = [
    "backend",
    "config",
    "docker",
    "docs",
    "frontend/dist",
    "requirements.txt",
    "docker-compose.yml",
    "README.md",
    ".env.example",
    "version.json",
    "update-manifest.json",
]

NEVER_COPY_NAMES = {
    ".env",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    "data",
    "backups",
    "build",
}

NEVER_COPY_SUFFIXES = {".pyc", ".pyo", ".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite3"}


def main() -> int:
    load_env_file(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Build standalone Sentero deployment artifacts")
    parser.add_argument("--version", default="", help="Override version.json version")
    parser.add_argument("--base-url", default=default_update_base_url(), help="Public base URL for generated update manifest")
    parser.add_argument("--no-zip", action="store_true", help="Only create build/sentero without update ZIP artifacts")
    parser.add_argument("--skip-frontend-build", action="store_true", help="Reuse frontend/dist instead of running npm run build")
    args = parser.parse_args()

    version = args.version.strip() or current_version()
    clean_build_dir()
    build_frontend(skip=args.skip_frontend_build)
    copy_deployment_tree(version)
    write_readme_install()
    if not args.no_zip:
        create_update_artifacts(version=version, base_url=args.base_url.strip())

    print(f"Built Sentero deployment in {TARGET_DIR}")
    if not args.no_zip:
        print(f"Update artifacts in {UPDATE_DIR}")
    return 0


def clean_build_dir() -> None:
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)


def build_frontend(skip: bool = False) -> None:
    dist = FRONTEND_DIR / "dist"
    if skip:
        if not dist.exists():
            raise SystemExit("frontend/dist does not exist. Run without --skip-frontend-build first.")
        return
    try:
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        if dist.exists():
            print(f"Warning: frontend build failed, reusing existing dist: {exc}", file=sys.stderr)
            return
        raise SystemExit(f"Frontend build failed and no existing frontend/dist is available: {exc}") from exc


def copy_deployment_tree(version: str) -> None:
    for item in COPY_ITEMS:
        source = ROOT / item
        if not source.exists():
            continue
        copy_path(source, TARGET_DIR / item)
    write_version_file(TARGET_DIR / "version.json", version)
    write_env_file(TARGET_DIR / ".env.example")
    ensure_runtime_dirs(TARGET_DIR)


def copy_path(source: Path, target: Path) -> None:
    if should_skip(source):
        return
    if source.is_dir():
        shutil.copytree(source, target, ignore=copy_ignore)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in NEVER_COPY_NAMES or path.suffix in NEVER_COPY_SUFFIXES:
            ignored.add(name)
    return ignored


def should_skip(path: Path) -> bool:
    return path.name in NEVER_COPY_NAMES or path.suffix in NEVER_COPY_SUFFIXES


def ensure_runtime_dirs(target: Path) -> None:
    for directory in ("data", "backups"):
        path = target / directory
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").write_text("", encoding="utf-8")


def write_version_file(path: Path, version: str) -> None:
    data = read_json(ROOT / "version.json", {})
    data["version"] = version
    data["app_version"] = version
    data["build"] = data.get("build") or datetime.now(timezone.utc).strftime("%Y.%m.%d")
    data["commit"] = data.get("commit") or git_commit()
    data["updated_at"] = utc_now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_env_file(path: Path) -> None:
    source = ROOT / ".env.example"
    if source.exists():
        copy_path(source, path)
        return
    path.write_text(
        "\n".join(
            [
                "SENTERO_DEV_MODE=false",
                "SENTERO_SENSOR_SOURCE=mqtt",
                "SENTERO_UPDATE_CHANNEL=stable",
                "SENTERO_UPDATE_MODE=dry_run",
                "UPDATE_BASE_URL=",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_readme_install() -> None:
    (TARGET_DIR / "README_INSTALL.md").write_text(
        """# Sentero Installation

1. Copy `.env.example` to `.env` and adjust sensor/update settings.
2. Build and start:

```bash
docker compose up --build -d
```

3. Open `http://<host>:8080`.

Runtime data stays in `data/` and is not part of update ZIP payloads.
""",
        encoding="utf-8",
    )


def create_update_artifacts(version: str, base_url: str) -> None:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RELEASE_DIR / f"sentero-{version}.zip"
    latest = latest_manifest(version=version, zip_path=zip_path, base_url=base_url)
    (TARGET_DIR / "update-manifest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(TARGET_DIR.rglob("*")):
            if should_skip(path):
                continue
            rel = path.relative_to(TARGET_DIR)
            if any(part in NEVER_COPY_NAMES for part in rel.parts):
                continue
            if path.is_file():
                archive.write(path, Path(f"sentero-{version}") / rel)

    (UPDATE_DIR / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    base_url = base_url.rstrip("/")
    (UPDATE_DIR / "deployment-manifest.json").write_text(
        json.dumps(deployment_manifest(version=version, zip_path=zip_path, base_url=base_url), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def deployment_manifest(version: str, zip_path: Path, base_url: str) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    if not base_url:
        raise SystemExit(
            "No public update base URL configured. Set UPDATE_BASE_URL in .env or pass --base-url."
        )
    try:
        artifact = str(zip_path.relative_to(UPDATE_DIR))
    except ValueError:
        artifact = f"releases/{zip_path.name}"
    return {
        "product": "sentero",
        "version": version,
        "created_at": utc_now(),
        "artifact": artifact,
        "artifact_url": f"{base_url}/stable/releases/{zip_path.name}",
        "manifest": "latest.json",
        "manifest_url": f"{base_url}/stable/latest.json",
        "target": str(TARGET_DIR.relative_to(BUILD_DIR)),
        "base_url": base_url,
    }


def latest_manifest(version: str, zip_path: Path, base_url: str) -> dict[str, Any]:
    filename = zip_path.name
    if not base_url:
        raise SystemExit(
            "No public update base URL configured. Set UPDATE_BASE_URL in .env or pass --base-url."
        )
    download_url = f"{base_url.rstrip('/')}/stable/releases/{filename}"
    return {
        "channels": {
            "stable": {
                "latest_version": version,
                "download_url": download_url,
                "mandatory": False,
                "release_notes": [f"Sentero {version} deployment build."],
                "layers": ["application"],
            }
        }
    }


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def default_update_base_url() -> str:
    explicit = (
        os.environ.get("UPDATE_BASE_URL", "")
        or os.environ.get("SENTERO_UPDATE_BASE_URL", "")
    ).strip()
    if explicit:
        return explicit.rstrip("/")

    manifest_url = (
        os.environ.get("SENTERO_UPDATE_MANIFEST_URL", "")
        or os.environ.get("UPDATE_MANIFEST_URL", "")
    ).strip()
    if not manifest_url:
        return ""

    parsed = urlparse(manifest_url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    suffix = "/stable/latest.json"
    if parsed.path.endswith(suffix):
        return manifest_url[: -len(suffix)].rstrip("/")
    if parsed.path.endswith("/latest.json"):
        return manifest_url[: -len("/latest.json")].rstrip("/")
    return manifest_url.rstrip("/")


def current_version() -> str:
    return str(read_json(ROOT / "version.json", {}).get("version") or "0.1.0")


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def git_commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True)
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
