"""Shared palette, unit scaling and bin-building helpers.

Single source of truth so the Excel and PowerPoint renderers produce charts
that look like one system. Mirrors the whole-number-bin behaviour of the
legacy ``utils/excel_export.py`` (``_unit`` / ``_build_bins``) so existing
users see familiar bin edges.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Palette — one dict shared by both renderers.
# ---------------------------------------------------------------------------

TAB_COLORS = {
    "overview": "#1A2B4A",   # navy
    "charts": "#2E7D32",     # green
    "image": "#00796B",      # teal
    "methods": "#F9A825",    # amber
    "raw": "#757575",        # grey
    "custom_text": "#6A1B9A",  # purple — Notes sheets (Excel) / text slides (PowerPoint)
}

# Chart series / accent colours (hex, no leading # stripped where needed).
SERIES = {
    "area_bar": "#6478DC",
    "diameter_bar": "#48B07A",
    "normal_fit": "#DC3278",
    "count_bar": "#1A2B4A",
    "error_bar": "#757575",
    "navy": "#1A2B4A",
    "teal": "#00796B",
    "amber": "#F9A825",
    "grey": "#757575",
    "white": "#FFFFFF",
    "text_dark": "#1A1A2E",
    "band": "#F2F4F7",
    "band_alt": "#EEF3FF",
    "gridline": "#D9D9D9",
}

# ---------------------------------------------------------------------------
# Theme palettes — chosen from the report designer's "Document ▸ Palette"
# combo (``ReportModel.theme``).  Note tab colours are NOT part of a palette
# — they stay kind-coded (``TAB_COLORS`` above) so a workbook always reads
# the same way regardless of theme. A palette only re-colours: chart series
# (bars/line), section/title header backgrounds, and PowerPoint accents.
# Single source of truth: ``ui/pages/report_inspector.py`` reads
# ``palette_choices()`` to populate the combo, so the UI never hardcodes ids.
# ---------------------------------------------------------------------------

PALETTES: Dict[str, Dict[str, str]] = {
    "default": {
        "name": "Corporate navy",
        "header": "#2E5FA3",
        "accent": "#1A2B4A",
        "accent2": "#00796B",
        "area_bar": "#6478DC",
        "diameter_bar": "#48B07A",
        "normal_fit": "#DC3278",
        "count_bar": "#1A2B4A",
    },
    "slate_teal": {
        "name": "Slate teal",
        "header": "#0E7C86",
        "accent": "#0B4F57",
        "accent2": "#1AA6B0",
        "area_bar": "#1AA6B0",
        "diameter_bar": "#5FBF8F",
        "normal_fit": "#E0708C",
        "count_bar": "#0B4F57",
    },
    "forge_amber": {
        "name": "Forge amber",
        "header": "#B4650A",
        "accent": "#7A4404",
        "accent2": "#E08A2B",
        "area_bar": "#E08A2B",
        "diameter_bar": "#4F8F5B",
        "normal_fit": "#C0392B",
        "count_bar": "#7A4404",
    },
    "graphite_mono": {
        "name": "Graphite mono",
        "header": "#4A4A4A",
        "accent": "#2B2B2B",
        "accent2": "#7A7A7A",
        "area_bar": "#7A7A7A",
        "diameter_bar": "#9E9E9E",
        "normal_fit": "#2B2B2B",
        "count_bar": "#4A4A4A",
    },
}

DEFAULT_PALETTE_ID = "default"
CUSTOM_PALETTE_ID = "custom"

HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def palette_ids() -> List[str]:
    return list(PALETTES.keys())


def palette_choices() -> List[Tuple[str, str]]:
    """``[(id, display name), ...]`` — what the palette combo box shows."""
    return [(k, v["name"]) for k, v in PALETTES.items()]


def resolve_palette(theme_id: str, custom: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """UX-15: ``theme_id == "custom"`` (or any unrecognised id) with a
    resolved ``custom`` palette dict (``ReportModel.custom_palette``) uses
    that instead of the built-ins — the model is self-contained, so a report
    still renders identically even if the user later edits/deletes the
    saved palette it was built from."""
    if custom and (theme_id == CUSTOM_PALETTE_ID or theme_id not in PALETTES):
        return custom
    return PALETTES.get(theme_id, PALETTES[DEFAULT_PALETTE_ID])


def series_for(theme_id: str, custom: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """``SERIES`` with the chart-series/header/accent entries swapped for
    the chosen palette. ``navy``/``header_bg``/``accent2`` are the
    section-background and PowerPoint-accent colours; everything else
    (typography, bands, gridlines) stays constant across palettes."""
    p = resolve_palette(theme_id, custom)
    out = dict(SERIES)
    out["area_bar"] = p["area_bar"]
    out["diameter_bar"] = p["diameter_bar"]
    out["normal_fit"] = p["normal_fit"]
    out["count_bar"] = p["count_bar"]
    out["navy"] = p["accent"]
    out["accent2"] = p["accent2"]
    out["header_bg"] = p["header"]
    return out


# ---------------------------------------------------------------------------
# UX-15: custom (user-picked, 3-colour) palettes
# ---------------------------------------------------------------------------

def normalize_hex(value: str) -> Optional[str]:
    """``"#a1b2c3"`` / ``"A1B2C3"`` -> ``"#A1B2C3"``; ``None`` if not a
    6-digit hex colour."""
    m = HEX_RE.match(str(value or "").strip())
    return f"#{m.group(1).upper()}" if m else None


def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _to_hex(rgb: Tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def shade(hex_color: str, factor: float) -> str:
    """Darken (``factor < 1``) or lighten (``factor > 1``) a hex colour by
    scaling its RGB components — used to derive a readable dark "accent"
    (section headers, cover, PowerPoint titles) from a user-picked bar
    colour without asking for a 4th colour."""
    r, g, b = _rgb(hex_color)
    if factor <= 1.0:
        return _to_hex((r * factor, g * factor, b * factor))
    return _to_hex((r + (255 - r) * (factor - 1.0), g + (255 - g) * (factor - 1.0),
                    b + (255 - b) * (factor - 1.0)))


def derive_custom_palette(colors: Sequence[str], name: str = "Custom") -> Dict[str, str]:
    """Expand 3 user-picked hex colours into the full palette shape used by
    ``PALETTES`` entries (``header``/``accent``/``accent2``/the 4 chart
    series). Raises ``ValueError`` if fewer than 3 colours are given or any
    is not a valid ``#RRGGBB`` hex colour.

    Mapping: colour 1 drives the header band and the area-histogram bars
    (plus a darkened shade for the navy accent used on covers/section
    headers and the per-image-count bar); colour 2 drives the secondary
    accent and the diameter-histogram bars; colour 3 is the normal-fit
    line/contrast colour — the same three roles a built-in palette's
    ``area_bar``/``diameter_bar``/``normal_fit`` play.
    """
    if len(colors) < 3:
        raise ValueError(f"derive_custom_palette needs 3 colours, got {len(colors)}.")
    c1, c2, c3 = (normalize_hex(c) for c in colors[:3])
    bad = [orig for orig, norm in zip(colors[:3], (c1, c2, c3)) if norm is None]
    if bad:
        raise ValueError(f"Not a #RRGGBB hex colour: {bad!r}")
    accent = shade(c1, 0.55)
    return {"name": name or "Custom", "header": c1, "accent": accent, "accent2": c2,
            "area_bar": c1, "diameter_bar": c2, "normal_fit": c3, "count_bar": accent}


def new_custom_palette_id(existing: Sequence[dict]) -> str:
    """A ``"custom-N"`` id not already used by ``existing`` (AppSettings.
    custom_palettes) — stable/human-scannable, unlike a uuid."""
    used = {str(p.get("id", "")) for p in existing}
    n = 1
    while f"custom-{n}" in used:
        n += 1
    return f"custom-{n}"


def _unit(ppu: float) -> Tuple[str, float, str, float]:
    """Return (area_unit, area_mult, length_unit, length_mult).

    Mirrors ``utils/excel_export.py::_unit``: values stored in the model are
    always in um (area_um2 / diameter_um from ``core.grain_detector``); the
    multiplier scales those into a more readable unit (nm) when the pixel
    size is tiny, or reports px when there is no calibration at all.
    """
    if ppu is None or ppu <= 0:
        return "px²", 1.0, "px", 1.0
    if 1000.0 / ppu < 50:
        return "nm²", 1e6, "nm", 1000.0
    return "µm²", 1.0, "µm", 1.0


def resolve_units(ppu: float, units_pref: str = "auto") -> Tuple[str, float, str, float]:
    """Apply the model's ``units`` preference (auto|um|nm) on top of ``_unit``.

    ``units_pref == "auto"`` reproduces legacy auto-scaling. ``"um"``/``"nm"``
    force a scale (still falls back to px if uncalibrated).
    """
    if ppu is None or ppu <= 0:
        return "px²", 1.0, "px", 1.0
    if units_pref == "um":
        return "µm²", 1.0, "µm", 1.0
    if units_pref == "nm":
        return "nm²", 1e6, "nm", 1000.0
    return _unit(ppu)


def fmt(val: float) -> str:
    if val == 0:
        return "0"
    av = abs(val)
    if av >= 100:
        return f"{val:.0f}"
    if av >= 10:
        return f"{val:.1f}"
    if av >= 1:
        return f"{val:.2f}"
    if av >= 0.1:
        return f"{val:.3f}"
    return f"{val:.3g}"


def build_bins(values: Sequence[float], n_bins: int = 0) -> Tuple[List[str], List[int], List[float]]:
    """Whole-number bins starting at 0 (legacy ``_build_bins`` behaviour).

    Returns (labels-without-unit-suffix, counts, edges). Caller appends the
    unit string to labels so this function stays unit-agnostic.
    """
    vals = np.asarray(list(values), dtype=float)
    if len(vals) < 2:
        return [], [], []

    vmax = float(np.max(vals))
    nb = n_bins if n_bins and n_bins >= 3 else min(max(int(math.sqrt(len(vals))), 5), 25)
    bw = max(1, math.ceil(vmax / nb)) if vmax > 0 else 1

    edges = []
    e = 0.0
    while e <= vmax + bw:
        edges.append(e)
        e += bw
    edges_arr = np.array(edges, dtype=float)
    counts, _ = np.histogram(vals, bins=edges_arr)

    while len(counts) > 1 and counts[-1] == 0:
        counts = counts[:-1]
        edges_arr = edges_arr[: len(counts) + 1]

    labels = [f"{int(edges_arr[i])}-{int(edges_arr[i + 1])}" for i in range(len(counts))]
    return labels, counts.tolist(), edges_arr.tolist()


def normal_fit(values: Sequence[float], edges: Sequence[float]) -> List[float]:
    """Scaled normal-density values at each bin midpoint (matches legacy)."""
    vals = np.asarray(list(values), dtype=float)
    if len(vals) < 2 or len(edges) < 2:
        return [0.0] * max(len(edges) - 1, 0)
    mu, sigma = float(np.mean(vals)), float(np.std(vals))
    bw = edges[1] - edges[0]
    nt = len(vals)
    out = []
    for i in range(len(edges) - 1):
        bc = (edges[i] + edges[i + 1]) / 2.0
        if sigma > 0:
            nv = (1.0 / (sigma * math.sqrt(2 * math.pi))) * math.exp(-0.5 * ((bc - mu) / sigma) ** 2) * bw * nt
        else:
            nv = 0.0
        out.append(round(nv, 2))
    return out


def estimate_astm_g(result) -> float | None:
    """Primary ASTM E112 grain-size number G for an AnalysisResult (None if
    uncalibrated). Delegates to core.astm, which uses the stored value or
    recomputes it for results saved before G was introduced."""
    from core.astm import astm_g_from_result
    try:
        return astm_g_from_result(result)
    except Exception:
        return None
