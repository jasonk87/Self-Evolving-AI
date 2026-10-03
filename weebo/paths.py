"""Filesystem layout for Weebo. Everything mutable lives under ``weebo_data/``."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
WEB_DIR = PACKAGE_DIR / "web"


def data_dir() -> Path:
    override = os.environ.get("WEEBO_DATA_DIR")
    base = Path(override).expanduser().resolve() if override else PROJECT_ROOT / "weebo_data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "weebo.db"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def logs_dir() -> Path:
    return sub("logs")


def workspace_dir() -> Path:
    return sub("workspace")


def skills_dir() -> Path:
    return sub("skills")


def worktrees_dir() -> Path:
    return sub("worktrees")


def cache_dir() -> Path:
    return sub("cache")


def uploads_dir() -> Path:
    return sub("uploads")
