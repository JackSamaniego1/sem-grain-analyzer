"""Shared palette, unit scaling and bin-building helpers.

Single source of truth so the Excel and PowerPoint renderers produce charts
that look like one system. Mirrors the whole-number-bin behaviour of the
legacy ``utils/excel_export.py`` (``_unit`` / ``_build_bins``) so existing
users see familiar bin edges.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

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


def estimate_astm_g(mean_diameter_um: float) -> float | None:
    """Hook for the ASTM E112 grain-size number (DET-04 computes this
    properly from planimetric/intercept counts). Returns ``None`` until that
    lands; renderers must display "—" in that case. Deliberately NOT
    implementing a provisional formula here — a naive diameter-only
    conversion would be misleading on a report."""
    return None
