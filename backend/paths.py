from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
CONFIG_DIR = PROJECT_DIR / "config"
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
ENV_PATH = PROJECT_DIR / ".env"
CONFIG_PATH = CONFIG_DIR / "sentero.yaml"

