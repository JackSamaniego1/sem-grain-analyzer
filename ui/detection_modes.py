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

# UPDATE 4 item 10b: the AI-assisted entry is shown as two choices, one per
# compute device.  The detection mode stays AI_MODE ("sam_astm", so saved
# sessions load unchanged); the device goes to DetectionParams.sam_device.
AI_DEVICES = ("gpu", "cpu")
AI_DEVICE_TITLES = {"gpu": "AI-Assisted (GPU)", "cpu": "AI-Assisted (CPU)"}
AI_DEVICE_DESC = {
    "gpu": "Segment-Anything model + ASTM E112 refinement on the NVIDIA graphics card. "
           "Most accurate; fast",
    "cpu": "Segment-Anything model + ASTM E112 refinement on the processor. "
           "Most accurate; slowest",
}


def normalize_ai_device(choice) -> str:
    """"gpu" | "cpu" | "" (no preference) from a saved value ("cuda", "GPU",
    "auto", None ...)."""
    c = str(choice or "").strip().lower()
    if c in ("gpu", "cuda", "nvidia"):
        return "gpu"
    return "cpu" if c == "cpu" else ""


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
           "default_mode", "normalize_mode", "AI_DEVICES", "AI_DEVICE_TITLES",
           "AI_DEVICE_DESC", "normalize_ai_device"]
