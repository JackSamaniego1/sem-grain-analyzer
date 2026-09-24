"""
Plain-JSON application settings — no Qt (the UI wraps this in QSettings-like
convenience if it wants; this module is the actual source of truth so it
stays testable headlessly).

File: ``%LOCALAPPDATA%\\GrainAnalyzer\\settings.json`` on Windows, falling
back to ``~/.grainanalyzer/settings.json`` if ``LOCALAPPDATA`` is unset
(non-Windows dev/test environments).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from data.models import AppSettings, read_json, write_json_atomic

_MAX_RECENT = 10


def get_settings_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "GrainAnalyzer"
    return Path.home() / ".grainanalyzer"


def get_settings_path() -> Path:
    return get_settings_dir() / "settings.json"


def load_settings(path: Optional[Path] = None) -> AppSettings:
    """Load settings, tolerating a missing or corrupt file (returns
    defaults)."""
    p = path or get_settings_path()
    if not p.exists():
        return AppSettings()
    try:
        return AppSettings.from_dict(read_json(p))
    except Exception:
        # Corrupt/unreadable settings file: never crash the app over it.
        return AppSettings()


def save_settings(settings: AppSettings, path: Optional[Path] = None) -> None:
    p = path or get_settings_path()
    write_json_atomic(p, settings.to_dict())


def add_recent_session(settings: AppSettings, session_path: str,
                        path: Optional[Path] = None, *, save: bool = True) -> AppSettings:
    """Prepend ``session_path`` to the recent-sessions list, de-duplicating
    and capping at ``_MAX_RECENT`` entries."""
    session_path = str(session_path)
    recent = [p for p in settings.recent_sessions if p != session_path]
    recent.insert(0, session_path)
    settings.recent_sessions = recent[:_MAX_RECENT]
    if save:
        save_settings(settings, path)
    return settings
