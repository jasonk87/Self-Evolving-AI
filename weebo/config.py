"""User-editable settings stored as JSON in ``weebo_data/settings.json``.

Every setting has a default here; unknown keys are rejected on update so a typo
in the UI (or from the model) can never silently create junk configuration.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from . import paths

DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 5050, "open_browser": True,
               "tailnet": False, "tailnet_hostname": "weebo"},  # private HTTPS address on Tailscale
    "codex": {
        "binary": "",  # explicit path to codex(.exe); blank = auto-detect newest
        "model": "",  # blank = account default from model/list
        "chat_effort": "medium",
        "agent_effort": "high",
        "background_effort": "low",
        "fast_mode": False,  # use the "priority" service tier when available
    },
    "autonomy": {
        "level": "balanced",  # cautious | balanced | full
        "proactive": True,
        "usage_ceiling_percent": 70,
        "max_background_turns_per_day": 12,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "07:00",
        "daily_brief": True,
        "daily_brief_time": "08:30",
        "dream": True,
        "dream_idle_minutes": 45,
        "dream_min_hours_between": 6,
        "self_audit": True,
        "self_audit_min_hours_between": 24,
    },
    "agents": {"max_parallel": 3, "default_cwd": "", "max_minutes": 60, "auto_followup": True},
    "evolution": {
        "mode": "build",  # off | propose | build | auto_merge
        "test_command": "",  # blank = built-in gate (pytest tests/weebo + syntax checks)
        "checkpoint_commits": True,  # save uncommitted work in a commit right before merging an upgrade
        "protect_paths": ["weebo/evolution/", "weebo/supervisor.py"],
    },
    "voice": {"speak_replies": False, "voice_name": "", "rate": 1.05, "pitch": 1.25},
    "ui": {"companion": True, "reduce_motion": False},
    "user": {"name": "", "timezone": ""},
}

CHOICES: dict[str, tuple[str, ...]] = {
    "codex.chat_effort": ("minimal", "low", "medium", "high", "xhigh", "ultra"),
    "codex.agent_effort": ("minimal", "low", "medium", "high", "xhigh", "ultra"),
    "codex.background_effort": ("minimal", "low", "medium", "high", "xhigh", "ultra"),
    "autonomy.level": ("cautious", "balanced", "full"),
    "evolution.mode": ("off", "propose", "build", "auto_merge"),
}

RANGES: dict[str, tuple[float, float]] = {
    "server.port": (1, 65535),
    "autonomy.usage_ceiling_percent": (0, 100),
    "autonomy.max_background_turns_per_day": (0, 500),
    "autonomy.dream_idle_minutes": (1, 24 * 60),
    "autonomy.dream_min_hours_between": (0, 24 * 14),
    "autonomy.self_audit_min_hours_between": (0, 24 * 30),
    "agents.max_parallel": (1, 12),
    "agents.max_minutes": (1, 24 * 60),
    "voice.rate": (0.5, 2.0),
    "voice.pitch": (0.0, 2.0),
}


class SettingsError(ValueError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key not in out:
            continue
        if isinstance(out[key], dict):
            if isinstance(value, dict):
                out[key] = _deep_merge(out[key], value)
        elif not isinstance(value, dict):
            out[key] = value
    return out


def _flatten(data: dict, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, full + "."))
        else:
            flat[full] = value
    return flat


def _coerce(key: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise SettingsError(f"{key} must be true or false")
    if isinstance(default, int):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{key} must be a whole number") from None
        value = number
    elif isinstance(default, float):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{key} must be a number") from None
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise SettingsError(f"{key} must be text")
        value = value.strip()
    elif isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SettingsError(f"{key} must be a list of text values")
    if key in CHOICES and value not in CHOICES[key]:
        raise SettingsError(f"{key} must be one of: {', '.join(CHOICES[key])}")
    if key in RANGES:
        low, high = RANGES[key]
        if not (low <= value <= high):
            raise SettingsError(f"{key} must be between {low:g} and {high:g}")
    if key.endswith("_time") or key.endswith("hours_start") or key.endswith("hours_end"):
        _validate_clock(key, value)
    if key == "server.tailnet_hostname" and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise SettingsError(f"{key} must be a lowercase name like 'weebo' (letters, digits, dashes)")
    return value


def _validate_clock(key: str, value: str) -> None:
    try:
        hh, mm = value.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError
    except ValueError:
        raise SettingsError(f"{key} must look like HH:MM (24h)") from None


class Settings:
    def __init__(self, path: Path | None = None):
        self._path = path or paths.settings_path()
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                backup = self._path.with_suffix(".corrupt.json")
                try:
                    self._path.replace(backup)
                except OSError:
                    pass
                raw = {}
        merged = _deep_merge(DEFAULTS, raw if isinstance(raw, dict) else {})
        # Hand-edited files can contain bad values; repair them key by key.
        flat_defaults = _flatten(DEFAULTS)
        for key, value in _flatten(merged).items():
            try:
                coerced = _coerce(key, value, flat_defaults[key])
            except (SettingsError, KeyError):
                coerced = flat_defaults.get(key)
            node = merged
            parts = key.split(".")
            for part in parts[:-1]:
                node = node[part]
            node[parts[-1]] = coerced
        return merged

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def all(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, dotted: str, fallback: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return fallback
            node = node[part]
        return copy.deepcopy(node)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Apply a nested or dotted patch. Returns the flat dict of changed keys."""
        flat_defaults = _flatten(DEFAULTS)
        flat_patch: dict[str, Any] = {}
        for key, value in patch.items():
            if isinstance(value, dict) and key in DEFAULTS:
                flat_patch.update(_flatten(value, key + "."))
            else:
                flat_patch[key] = value
        changed: dict[str, Any] = {}
        with self._lock:
            current = _flatten(self._data)
            for key, value in flat_patch.items():
                if key not in flat_defaults:
                    raise SettingsError(f"Unknown setting: {key}")
                coerced = _coerce(key, value, flat_defaults[key])
                if current.get(key) != coerced:
                    changed[key] = coerced
            for key, value in changed.items():
                node = self._data
                parts = key.split(".")
                for part in parts[:-1]:
                    node = node[part]
                node[parts[-1]] = value
            if changed:
                self._save()
        if changed:
            for listener in list(self._listeners):
                try:
                    listener(changed)
                except Exception:  # listeners must never break a settings save
                    pass
        return changed

    def on_change(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)
