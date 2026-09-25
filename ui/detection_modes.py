"""
Detection modes offered in the Analyze page (UX-01).

"AI-assisted" is first and the default.  The former "Automatic" choice is
no longer offered; sessions saved with it open as AI-assisted.  When the AI
model file is not part of the installation (developer checkouts, tests) the
AI card is shown disabled with a "reinstall" hint and Boundary is used.
"""
from __future__ import annotations

import os
import sys

AI_MODE = "sam_astm"
FALLBACK_MODE = "boundary"

# key, title, icon, description (order = order on screen)
MODES = [
    (AI_MODE, "AI-assisted", "layers",
     "Segment-Anything model + ASTM E112 refinement. Most accurate; slowest"),
    ("boundary", "Boundary", "grains",
     "Dense grain mosaics separated by thin dark grooves"),
    ("threshold", "Threshold", "histogram",
     "Distinct grains or particles on a contrasting background"),
]
MODE_KEYS = tuple(m[0] for m in MODES)
SAM_CHECKPOINT = "sam_vit_b_01ec64.pth"


def sam_model_available() -> bool:
    """The bundled AI model file is present (never downloaded)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = getattr(sys, "_MEIPASS", here)
    return any(os.path.isfile(os.path.join(d, SAM_CHECKPOINT)) for d in
               (os.path.join(base, "models"), os.path.join(here, "models"), here,
                os.path.join(here, "core")))


def default_mode() -> str:
    return AI_MODE if sam_model_available() else FALLBACK_MODE


def normalize_mode(mode: str) -> str:
    """A mode the Analyze page can select: unknown / legacy "auto" / empty
    -> the default; AI-assisted without the model file -> Boundary."""
    mode = (mode or "").strip()
    if mode not in MODE_KEYS:
        return default_mode()
    if mode == AI_MODE and not sam_model_available():
        return FALLBACK_MODE
    return mode


__all__ = ["MODES", "MODE_KEYS", "AI_MODE", "FALLBACK_MODE", "sam_model_available",
           "default_mode", "normalize_mode"]
