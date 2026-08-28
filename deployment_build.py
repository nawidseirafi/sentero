#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"

# Initial/customer deployment tree, if the Sentero Box v2 `box/` directory is
# part of the project.
BOX_SOURCE_DIR = ROOT / "box"
BOX_TARGET_DIR = BUILD_DIR / "sentero-box"

# Public update-server layout. This deliberately keeps the old directory
# structure so your existing upload/deployment workflow can continue to use:
#
#   build/updates/sentero/stable/latest.json
#   build/updates/sentero/stable/releases/...
#
UPDATE_DIR = BUILD_DIR / "updates" / "sentero" / "stable"
RELEASE_DIR = UPDATE_DIR / "releases"

APPLIANCE_DOCKERFILE = ROOT / "docker" / "Dockerfile.appliance"
VERSION_FILE = ROOT / "version.json"

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
    "ollama",
}

NEVER_COPY_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite3",
}


def main() -> int:
    load_env_file(ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Build Sentero Box Docker deployment and appliance update artifacts"
    )
    parser.add_argument(
        "--version",
        default="",
        help="Release version. Defaults to version.json.",
    )
    parser.add_argument(
        "--base-url",
        default=default_update_base_url(),
        help=(
            "Public update base URL, e.g. "
            "https://sentero.de/sentero"
        ),
    )
    parser.add_argument(
        "--channel",
        default=os.getenv("SENTERO_UPDATE_CHANNEL", "stable"),
        choices=("stable", "beta", "dev"),
        help="Update channel. Default: stable.",
    )
    parser.add_argument(
        "--image",
        default="",
        help="Docker image name without tag. Default: sentero/app",
    )
    parser.add_argument(
        "--no-box",
        action="store_true",
        help="Do not create build/sentero-box initial deployment tree.",
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Only create initial deployment; do not build an update bundle.",
    )
    parser.add_argument(
        "--skip-docker-build",
        action="store_true",
        help=(
            "Do not run docker build. The versioned image must already exist "
            "locally; useful when CI built it beforehand."
        ),
    )
    parser.add_argument(
        "--skip-docker-save",
        action="store_true",
        help=(
            "Generate metadata only and do not create sentero-image.tar/update ZIP. "
            "Intended for manifest/testing only."
        ),
    )
    parser.add_argument(
        "--mandatory",
        action="store_true",
        help="Mark the generated release as mandatory.",
    )
    parser.add_argument(
        "--release-note",
        action="append",
        default=[],
        help="Release note. May be supplied multiple times.",
    )
    args = parser.parse_args()

    version = validate_version(args.version.strip() or current_version())
    base_url = normalize_update_base_url(args.base_url)
    image_repo = (args.image.strip() or os.getenv("SENTERO_DOCKER_IMAGE", "") or "sentero/app").rstrip(":")
    image = f"{image_repo}:{version}"

    if not args.no_update and not base_url:
        raise SystemExit(
            "No public update base URL configured. "
            "Set UPDATE_BASE_URL/SENTERO_UPDATE_BASE_URL or pass --base-url."
        )

    clean_output(channel=args.channel)

    # The appliance Dockerfile is multi-stage and builds the frontend inside
    # Docker. This avoids host/macOS node_modules leaking into the Linux image.
    if not args.no_update and not args.skip_docker_build:
        build_docker_image(image=image, version=version)

    if not args.no_box:
        create_initial_box(version=version, image=image)

    if not args.no_update:
        create_appliance_update(
            version=version,
            image=image,
            channel=args.channel,
            base_url=base_url,
            mandatory=args.mandatory,
            release_notes=args.release_note,
            save_image=not args.skip_docker_save,
        )

    print()
    print(f"Sentero version: {version}")
    print(f"Docker image:    {image}")
    if not args.no_box:
        print(f"Initial box:     {BOX_TARGET_DIR}")
    if not args.no_update:
        channel_dir = BUILD_DIR / "updates" / "sentero" / args.channel
        print(f"Update files:    {channel_dir}")
    return 0


def clean_output(channel: str) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if BOX_TARGET_DIR.exists():
        shutil.rmtree(BOX_TARGET_DIR)

    channel_dir = BUILD_DIR / "updates" / "sentero" / channel
    if channel_dir.exists():
        shutil.rmtree(channel_dir)
    (channel_dir / "releases").mkdir(parents=True, exist_ok=True)


def build_docker_image(*, image: str, version: str) -> None:
    if not APPLIANCE_DOCKERFILE.exists():
        raise SystemExit(
            f"{APPLIANCE_DOCKERFILE} not found. "
            "Install the Sentero Box v2 Dockerfile first."
        )

    # Keep version.json in source synchronized with the image being built so
    # /api/update/version reports the actual container version.
    write_version_file(VERSION_FILE, version)

    run(
        [
            "docker",
            "build",
            "--pull",
            "--file",
            str(APPLIANCE_DOCKERFILE),
            "--tag",
            image,
            str(ROOT),
        ],
        cwd=ROOT,
        purpose="Docker image build",
    )


def create_initial_box(*, version: str, image: str) -> None:
    if not BOX_SOURCE_DIR.exists():
        print(
            "Warning: project has no box/ directory; "
            "skipping initial Sentero Box deployment tree.",
            file=sys.stderr,
        )
        return

    shutil.copytree(
        BOX_SOURCE_DIR,
        BOX_TARGET_DIR,
        ignore=copy_ignore,
    )

    env_example = BOX_TARGET_DIR / ".env.example"
    if env_example.exists():
        replace_env_value(env_example, "SENTERO_IMAGE", image)
        replace_env_value(env_example, "SENTERO_VERSION", version)

    # The customer package must never contain runtime secrets or data.
    for relative in (
        ".env",
        "data",
        "backups",
        "mosquitto/data",
        "mosquitto/log",
        "mosquitto/config/passwords",
        "zigbee2mqtt/data/configuration.yaml",
        "ollama",
    ):
        path = BOX_TARGET_DIR / relative
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    # Recreate empty runtime directories expected by the installation scripts.
    for relative in (
        "data/sentero",
        "backups",
        "mosquitto/data",
        "mosquitto/log",
        "zigbee2mqtt/data",
        "ollama",
    ):
        path = BOX_TARGET_DIR / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").write_text("", encoding="utf-8")


def create_appliance_update(
    *,
    version: str,
    image: str,
    channel: str,
    base_url: str,
    mandatory: bool,
    release_notes: list[str],
    save_image: bool,
) -> None:
    channel_dir = BUILD_DIR / "updates" / "sentero" / channel
    release_dir = channel_dir / "releases"
    release_dir.mkdir(parents=True, exist_ok=True)

    bundle_name = f"sentero-box-{version}.zip"
    bundle_path = release_dir / bundle_name
    image_tar = release_dir / f"sentero-image-{version}.tar"
    release_json = release_dir / f"release-{version}.json"

    release_metadata = {
        "format": 1,
        "product": "Sentero Box",
        "version": version,
        "image": image,
        "image_tar": "sentero-image.tar",
        "created_at": utc_now(),
        "commit": git_commit(),
    }
    release_json.write_text(
        json.dumps(release_metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if save_image:
        ensure_local_image(image)
        run(
            ["docker", "save", image, "-o", str(image_tar)],
            cwd=ROOT,
            purpose="Docker image export",
        )

        create_release_zip(
            bundle_path=bundle_path,
            release_json=release_json,
            image_tar=image_tar,
        )

        # Temporary files are not needed after the self-contained ZIP exists.
        release_json.unlink(missing_ok=True)
        image_tar.unlink(missing_ok=True)

        bundle_sha = file_sha256(bundle_path)
        bundle_size = bundle_path.stat().st_size
        bundle_url = (
            f"{base_url}/{channel}/releases/{bundle_name}"
        )
    else:
        # Metadata-only mode is useful to validate the deployment script, but
        # deliberately produces a manifest that cannot be installed.
        bundle_sha = "0" * 64
        bundle_size = 0
        bundle_url = (
            f"{base_url}/{channel}/releases/{bundle_name}"
        )
        release_json.unlink(missing_ok=True)

    notes = release_notes or [f"Sentero {version} Appliance-Release."]

    latest = {
        "channels": {
            channel: {
                "latest_version": version,
                "mandatory": bool(mandatory),
                "release_notes": notes,
                "layers": ["application"],
                # Legacy fields stay present for older GUI/parser versions.
                # The Docker appliance installer intentionally uses the
                # `appliance` block below instead of modifying container files.
                "download_url": bundle_url,
                "sha256": bundle_sha,
                "size_bytes": bundle_size,
                "appliance": {
                    "bundle_url": bundle_url,
                    "sha256": bundle_sha,
                    "size_bytes": bundle_size,
                    "format": 1,
                    "image": image,
                },
            }
        }
    }

    latest_path = channel_dir / "latest.json"
    latest_path.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    deployment = {
        "product": "sentero-box",
        "version": version,
        "channel": channel,
        "created_at": utc_now(),
        "image": image,
        "manifest": "latest.json",
        "manifest_url": f"{base_url}/{channel}/latest.json",
        "artifact": f"releases/{bundle_name}",
        "artifact_url": bundle_url,
        "sha256": bundle_sha,
        "size_bytes": bundle_size,
        "commit": git_commit(),
        "update_mode": "appliance",
    }
    (channel_dir / "deployment-manifest.json").write_text(
        json.dumps(deployment, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if save_image:
        validate_bundle(bundle_path, expected_version=version, expected_image=image)


def create_release_zip(
    *,
    bundle_path: Path,
    release_json: Path,
    image_tar: Path,
) -> None:
    if bundle_path.exists():
        bundle_path.unlink()

    # ZIP_STORED for the Docker tar avoids wasting large amounts of CPU trying
    # to recompress already-compressed image layers.
    with zipfile.ZipFile(bundle_path, "w") as archive:
        archive.write(
            release_json,
            "release.json",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.write(
            image_tar,
            "sentero-image.tar",
            compress_type=zipfile.ZIP_STORED,
        )


def validate_bundle(
    bundle_path: Path,
    *,
    expected_version: str,
    expected_image: str,
) -> None:
    with zipfile.ZipFile(bundle_path, "r") as archive:
        names = set(archive.namelist())
        required = {"release.json", "sentero-image.tar"}
        missing = required - names
        if missing:
            raise SystemExit(
                f"Invalid appliance bundle; missing: {', '.join(sorted(missing))}"
            )
        release = json.loads(archive.read("release.json").decode("utf-8"))

    if release.get("version") != expected_version:
        raise SystemExit("Release ZIP version does not match requested version.")
    if release.get("image") != expected_image:
        raise SystemExit("Release ZIP image does not match requested image.")


def ensure_local_image(image: str) -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Docker image {image!r} does not exist locally. "
            "Run without --skip-docker-build or build it first."
        )


def copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in NEVER_COPY_NAMES or path.suffix in NEVER_COPY_SUFFIXES:
            ignored.add(name)
    return ignored


def replace_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            found = True
        else:
            output.append(line)
    if not found:
        output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def write_version_file(path: Path, version: str) -> None:
    data = read_json(path, {})
    data["version"] = version
    data["app_version"] = version
    data["build"] = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    data["commit"] = git_commit()
    data["updated_at"] = utc_now()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_version(value: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit("Version must not be empty.")
    allowed = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-_+")
    if len(value) > 64 or any(char not in allowed for char in value):
        raise SystemExit(f"Invalid release version: {value!r}")
    return value


def run(
    command: list[str],
    *,
    cwd: Path,
    purpose: str,
) -> None:
    print(f"{purpose}: {' '.join(command)}")
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"{command[0]!r} is not installed or not in PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"{purpose} failed with exit code {exc.returncode}."
        ) from exc


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


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
        return "https://sentero.de/sentero"

    parsed = urlparse(manifest_url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    for suffix in (
        "/stable/latest.json",
        "/beta/latest.json",
        "/dev/latest.json",
        "/latest.json",
    ):
        if parsed.path.endswith(suffix):
            return normalize_update_base_url(manifest_url[: -len(suffix)])
    return normalize_update_base_url(manifest_url)


def normalize_update_base_url(value: str) -> str:
    return value.strip().rstrip("/")



def current_version() -> str:
    return str(read_json(VERSION_FILE, {}).get("version") or "0.1.0")


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
