"""Lot Summary data (UPDATE 4 item 15) -- ONE Qt-free source of truth.

``lot_summary_data(model)`` returns chart-ready series and table rows so the
on-screen report preview, the Excel sheet and the PowerPoint slides all show
the same numbers. It never touches the network, Qt or the file system.

Return shape (all sizes already in the report's display units)::

    {
      "has_lots": bool,            # False -> no image carries a lot value
      "units": {"calibrated": bool, "length": "um", "area": "um2"},   # unit strings (µm, µm², nm, px ...)
      "part_label": "Part Number", "lot_label": "Lot", "multi_part": bool,
      "lots":  [stats, ...],       # one per (part, lot), first-seen order
      "parts": [stats, ...],       # one per part, pooled over its lots
      "total": stats,              # whole job
      "rows":  [{"kind": "lot"|"part"|"total", **stats}, ...],  # table order
      "charts": [chart, ...],
      "footnotes": [str, ...],     # show under the table (may be empty)
    }

    stats = {"part", "lot", "label", "n_lots", "n_images", "n_grains",
             "mean_diameter", "median_diameter", "d10", "d90", "mean_area",
             "astm_g"}                  # numbers are None when undefined
    chart = {"id", "title", "x_title", "y_title", "num_format",
             "categories": [{"part", "lot", "label"}], "values": [float|None],
             "trend": [float|None] | None, "multi_level": bool}

``trend`` is a least-squares straight line fitted across the lots of each part
(>= 2 lots with a value); it is ``None`` when no part has two such lots.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .charts import resolve_units
from .model import ImageSummary, ReportModel

MAX_LABEL_CHARS = 24
NO_LOT = "(no lot)"
NO_PART = "(no part)"


def _short(text: str, n: int = MAX_LABEL_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _levels(model: ReportModel) -> Tuple[Optional[int], Optional[int], str, str]:
    lot_idx = part_idx = None
    part_label, lot_label = "Part Number", "Lot"
    if model.hierarchy:
        for i, (key, label) in enumerate(model.level_columns()):
            if key == "lot":
                lot_idx, lot_label = i, label or "Lot"
            elif key in ("sample", "part"):
                part_idx, part_label = i, label or "Part Number"
    return lot_idx, part_idx, part_label, lot_label


def _display_units(model: ReportModel, images: List[ImageSummary]):
    if images and all(i.has_calibration for i in images):
        au, am, du, dm = resolve_units(images[0].px_per_um, model.units)
        return True, au, am, du, dm
    return False, "px²", 1.0, "px", 1.0


def _pool(imgs: List[ImageSummary], calibrated: bool, am: float, dm: float):
    dk, ak = ("diameter_um", "area_um2") if calibrated else ("diameter_px", "area_px")
    dmul, amul = (dm, am) if calibrated else (1.0, 1.0)
    diams: List[float] = []
    areas: List[float] = []
    for img in imgs:
        for g in img.grains:
            d, a = g.get(dk), g.get(ak)
            if d is None or a is None:
                continue
            d, a = float(d), float(a)
            if np.isfinite(d) and np.isfinite(a):
                diams.append(d * dmul)
                areas.append(a * amul)
    return np.asarray(diams, dtype=float), np.asarray(areas, dtype=float)


def _stats(part: str, lot: str, imgs: List[ImageSummary], calibrated: bool, am: float,
           dm: float, n_lots: int = 1, label: Optional[str] = None) -> Dict[str, Any]:
    diams, areas = _pool(imgs, calibrated, am, dm)
    gs = [float(i.astm_g) for i in imgs if i.astm_g is not None and np.isfinite(i.astm_g)]
    out: Dict[str, Any] = {
        "part": part, "lot": lot, "label": label if label is not None else lot,
        "n_lots": n_lots, "n_images": len(imgs),
        "n_grains": int(len(diams)),
        "mean_diameter": None, "median_diameter": None, "d10": None, "d90": None,
        "mean_area": None, "astm_g": float(np.mean(gs)) if gs else None,
    }
    if len(diams):
        p10, p50, p90 = np.percentile(diams, [10, 50, 90])
        out.update(mean_diameter=float(diams.mean()), median_diameter=float(p50),
                   d10=float(p10), d90=float(p90), mean_area=float(areas.mean()))
    return out


def _linear_trend(cats: List[Dict[str, str]], values: List[Optional[float]]
                  ) -> Optional[List[Optional[float]]]:
    """Per-part straight-line fit over lot position; None where a part has
    fewer than two lots with a value (a single lot has no trend)."""
    trend: List[Optional[float]] = [None] * len(values)
    any_fit = False
    parts: Dict[str, List[int]] = {}
    for i, c in enumerate(cats):
        parts.setdefault(c["part"], []).append(i)
    for idxs in parts.values():
        pts = [(k, values[i]) for k, i in enumerate(idxs) if values[i] is not None]
        if len(pts) < 2:
            continue
        x = np.array([p[0] for p in pts], dtype=float)
        y = np.array([p[1] for p in pts], dtype=float)
        slope, icpt = np.polyfit(x, y, 1)
        for k, i in enumerate(idxs):
            trend[i] = float(slope * k + icpt)
        any_fit = True
    return trend if any_fit else None


def _num_format(values: List[Optional[float]], kind: str) -> str:
    if kind == "count":
        return "#,##0"
    if kind == "g":
        return "0.0"
    vals = [abs(v) for v in values if v is not None]
    return "0.000" if vals and max(vals) < 1 else "0.00"


def lot_summary_data(model: ReportModel,
                     images: Optional[List[ImageSummary]] = None) -> Dict[str, Any]:
    """See the module docstring for the return shape."""
    if images is None:
        images = model.ordered_images(included_only=True)
    lot_idx, part_idx, part_label, lot_label = _levels(model)
    calibrated, au, am, du, dm = _display_units(model, images)

    order: List[Tuple[str, str]] = []
    groups: Dict[Tuple[str, str], List[ImageSummary]] = {}
    for img in images:
        lv = model.row_levels(img)
        lot = (lv[lot_idx] if lot_idx is not None else img.lot_number) or ""
        part = (lv[part_idx] if part_idx is not None else img.sample_id) or ""
        k = (part, lot)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(img)
    has_lots = any(lot for _p, lot in order)

    part_order: List[str] = []
    for p, _l in order:
        if p not in part_order:
            part_order.append(p)
    # Lots of one part stay together (first-seen part order).
    order = [(p, l) for p in part_order for (pp, l) in order if pp == p]

    lots = [_stats(p or NO_PART, l or NO_LOT, groups[(p, l)], calibrated, am, dm)
            for p, l in order]
    parts, rows = [], []
    for p in part_order:
        plots = [(pp, ll) for pp, ll in order if pp == p]
        pimgs = [i for k in plots for i in groups[k]]
        pst = _stats(p or NO_PART, "", pimgs, calibrated, am, dm, n_lots=len(plots),
                     label=(p or NO_PART) + " total")
        parts.append(pst)
        for k in plots:
            rows.append({"kind": "lot", **lots[order.index(k)]})
        if len(plots) > 1:
            rows.append({"kind": "part", **pst})
    total = _stats("", "", images, calibrated, am, dm, n_lots=len(lots), label="JOB TOTAL")
    total["n_parts"] = len(parts)
    rows.append({"kind": "total", **total})

    multi_part = len(part_order) > 1
    cats = [{"part": s["part"], "lot": s["lot"], "label": _short(s["lot"])} for s in lots]
    x_title = f"{part_label} / {lot_label}" if multi_part else lot_label
    specs = [
        ("mean_diameter", "Mean Grain Diameter by Lot", f"Mean Equivalent Diameter ({du})", "size"),
        ("median_diameter", "Median Grain Diameter (D50) by Lot",
         f"Median Equivalent Diameter, D50 ({du})", "size"),
        ("mean_area", "Mean Grain Area by Lot", f"Mean Grain Area ({au})", "size"),
        ("n_grains", "Grain Count by Lot", "Number of Grains", "count"),
        ("astm_g", "ASTM Grain Size Number by Lot", "ASTM G Number", "g"),
    ]
    charts: List[Dict[str, Any]] = []
    for key, title, y_title, kind in specs:
        values = [s[key] for s in lots]
        if key != "n_grains" and all(v is None for v in values):
            continue                     # e.g. no calibration -> no ASTM G chart
        charts.append({
            "id": key, "title": title, "x_title": x_title, "y_title": y_title,
            "num_format": _num_format(values, kind), "categories": cats, "values": values,
            "trend": _linear_trend(cats, values), "multi_level": multi_part,
        })
    return {"has_lots": has_lots, "units": {"calibrated": calibrated, "length": du, "area": au},
            "part_label": part_label, "lot_label": lot_label, "multi_part": multi_part,
            "lots": lots, "parts": parts, "total": total, "rows": rows, "charts": charts,
            "footnotes": footnotes_for(rows)}


ASTM_AVG_FOOTNOTE = "ASTM G in subtotal and total rows is the average of the image values."


def footnotes_for(rows: List[Dict[str, Any]]) -> List[str]:
    """Footnotes that apply to ``rows`` (a page of the table is fine): the
    ASTM G note appears only when a subtotal/total row with a value is shown."""
    if any(r["kind"] != "lot" and r.get("astm_g") is not None for r in rows):
        return [ASTM_AVG_FOOTNOTE]
    return []


def table_headers(data: Dict[str, Any]) -> List[str]:
    """Job-summary table column headers (with units), matching ``id_cells``
    + the numeric keys in ``NUMERIC_KEYS``."""
    du, au = data["units"]["length"], data["units"]["area"]
    return [data["part_label"], data["lot_label"], "Images", "Grains", f"Mean Diameter ({du})",
            f"Median Diameter, D50 ({du})", f"D10 ({du})", f"D90 ({du})",
            f"Mean Area ({au})", "ASTM G"]


NUMERIC_KEYS = ["n_images", "n_grains", "mean_diameter", "median_diameter", "d10", "d90",
                "mean_area", "astm_g"]


def id_cells(row: Dict[str, Any]) -> Tuple[str, str]:
    """(part cell, lot cell) for a table row of ``lot_summary_data()["rows"]``."""
    if row["kind"] == "total":
        return "JOB TOTAL", ""
    if row["kind"] == "part":
        return row["part"], "All lots"
    return row["part"], row["lot"]
