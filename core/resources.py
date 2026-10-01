"""
Bundled read-only assets (batch 4, D-39) -- no Qt, no network.

``resource_path("assets", "tutorial")`` works from the source tree and from
the PyInstaller bundle (``sys._MEIPASS``).  Assets are installed with the
app; when one is missing the caller tells the user to reinstall (never a
download link).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parents[1]          # project root (source tree)

TUTORIAL_DIR = ("assets", "tutorial")
TUTORIAL_IMAGES = ("tutorial_sample_1.png", "tutorial_sample_2.png", "tutorial_sample_3.png")
REINSTALL_MESSAGE = ("The tutorial's sample images are missing from this installation. "
                     "Please reinstall the Grain Analyzer from the installer.")


def bases() -> List[Path]:
    """Folders searched, in order: the frozen bundle, then the source tree."""
    out = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass))
    out.append(_HERE)
    return out


def resource_path(*parts: str) -> Path:
    """First existing ``<base>/<parts...>``; the bundle path when none exists."""
    for b in bases():
        p = b.joinpath(*parts)
        if p.exists():
            return p
    return bases()[0].joinpath(*parts)


def tutorial_images() -> List[Path]:
    """The three bundled sample images; ``FileNotFoundError`` (with the
    reinstall message) when any of them is missing."""
    d = resource_path(*TUTORIAL_DIR)
    paths = [d / n for n in TUTORIAL_IMAGES]
    if not all(p.is_file() for p in paths):
        raise FileNotFoundError(REINSTALL_MESSAGE)
    return paths


__all__ = ["resource_path", "tutorial_images", "TUTORIAL_IMAGES", "REINSTALL_MESSAGE"]
