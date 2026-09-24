"""Number/unit formatting shared by every page (keeps v2.3's readable units:
whole-number-ish values, automatic nm/µm switching)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from ui.results_panel import smart_format, smart_unit  # v2.3 behaviour, preserved

__all__ = ["smart_unit", "smart_format", "area_value", "diam_value", "units_for",
           "fmt_int", "fmt_opt", "fmt_date_utc", "fmt_px_per_um", "astm_g"]


def units_for(result) -> Tuple[str, float, str, float]:
    """(area_unit, area_mult, diam_unit, diam_mult) for a result; px units when
    uncalibrated."""
    if result is None or not getattr(result, "has_calibration", False):
        return "px²", 1.0, "px", 1.0
    return smart_unit(result.px_per_um)


def area_value(result, grain) -> float:
    au, am, _, _ = units_for(result)
    return grain.area_um2 * am if au != "px²" else grain.area_px


def diam_value(result, grain) -> float:
    _, _, du, dm = units_for(result)
    return grain.equivalent_diameter_um * dm if du != "px" else grain.equivalent_diameter_px


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_opt(v: Optional[float], nd: int = 1, suffix: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def fmt_date_utc(iso: str, with_time: bool = True) -> str:
    """'2026-09-23T12:32:00Z' -> local '23 Sep 2026 · 14:32'."""
    if not iso:
        return ""
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
    except ValueError:
        return iso
    return dt.strftime("%d %b %Y · %H:%M" if with_time else "%d %b %Y")


def fmt_px_per_um(ppu: float) -> str:
    if not ppu or ppu <= 0:
        return "Not calibrated"
    nm = 1000.0 / ppu
    return f"{ppu:.4g} px/µm  ({nm:.4g} nm/px)"


def astm_g(result) -> Optional[float]:
    """ASTM E112 G number if the detector provided one (read defensively)."""
    g = getattr(result, "astm_g", None)
    try:
        return None if g is None else float(g)
    except (TypeError, ValueError):
        return None
