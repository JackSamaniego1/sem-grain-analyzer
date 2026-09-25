"""Multi-lot report builder (UX-13).

Builds one :class:`~reports.model.ReportModel` spanning several lots across
an arbitrary **Job > Part > Lot > images** hierarchy — a whole job, one
part/sample with several lots, or a single lot are all just different
shapes of the same input, so a single-lot report still renders exactly like
:meth:`ReportModel.from_results` (nothing else in ``reports/`` changes for
existing single-lot callers).

Public entry point
-------------------
``build_multi_lot_report_model(groups, ...)`` — ``groups`` is a sequence of
:class:`LotGroup`, one per (job, part, lot) triple, each carrying the same
list of :class:`~reports.model.ReportImageInput` that
``ReportModel.from_results`` already takes for that lot's images (i.e.
"whatever per-lot results structure the Analyze page/session already has",
wrapped one ``ReportImageInput`` per image). The Analyze page (ui-designer,
later) builds one ``LotGroup`` per lot it has open — however many
jobs/parts/lots that spans — and calls this function once for the whole
report::

    from reports.multi_lot import LotGroup, build_multi_lot_report_model

    groups = [
        LotGroup(job="24-117", part="7718-A", lot="L-1", items=[...]),
        LotGroup(job="24-117", part="7718-A", lot="L-2", items=[...]),
        LotGroup(job="24-117", part="7718-B", lot="L-1", items=[...]),
    ]
    model = build_multi_lot_report_model(
        groups, title="Job 24-117", operator="J. Samaniego",
        organization="Acme Metallography",
        baseline={"7718-A": "L-1"},   # part -> baseline lot label (optional)
    )
    # model.save(...) / reports.excel_renderer.render_excel(model, ...) /
    # reports.pptx_renderer.render_pptx(model, ...) — same as any other
    # ReportModel.

A part or the whole report with only a single lot renders cleanly: the
per-part summary still lists that one lot, and the lot-comparison matrix
degenerates to its trivial 1x1 diagonal (no equivalence verdicts unless a
baseline is set and there is at least one other lot to compare it to).

What this adds on top of ``ReportModel.from_results``
-------------------------------------------------------
* ``model.hierarchy`` — the Job #/Part Number/Lot column schema; every
  image's ``levels`` carries its own job/part/lot, so the existing Overview
  table, raw-data sheets (Job/Part/Lot header line — HIER-01) and cover
  header already show/label them correctly. No Excel/PowerPoint renderer
  change was needed for that part; it is the same machinery single-lot
  hierarchy reports already use, just populated per row.
* ``model.sample_statistics`` — one ``core.metrics.SampleStatistics`` per
  lot (INN-27), labelled "<part> / <lot>" so the existing Overview
  lot-summary block and PPTX G +/- CI tiles stay meaningful when several
  parts are present.
* A new ``Section(type="lot_comparison")`` (blue "Summary" tab in Excel),
  placed right after the Overview section. Its ``payload["parts"]`` holds,
  per part: the lots in it and a ``core.lot_compare.compare_lots`` result
  (delta matrix + Welch ANOVA + TOST equivalence vs that part's baseline,
  reusing ``core/lot_compare.py`` exactly as the in-app lot-comparison page
  does) — rendered as a dedicated sheet (Excel) / slide(s) (PowerPoint) by
  the two renderers.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.lot_compare import DEFAULT_ALPHA, DEFAULT_MARGIN_G, compare_lots
from core.metrics import FieldResult, sample_statistics
from reports.model import ReportImageInput, ReportModel, Section

__all__ = ["LotGroup", "build_multi_lot_report_model"]


@dataclass
class LotGroup:
    """One lot's images + where it sits in the Job > Part > Lot hierarchy.

    ``items``: the lot's images, exactly as passed to
    ``ReportModel.from_results`` for a single-lot report (``sample_id``/
    ``lot_number``/``levels`` on individual items are overwritten with
    ``part``/``lot`` — set them on the group, not the items).
    """
    job: str
    part: str
    lot: str
    items: List[ReportImageInput] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.job}␟{self.part}␟{self.lot}"   # unit-separator, never user text


def _part_key(g: LotGroup) -> str:
    return f"{g.job}␟{g.part}"


def build_multi_lot_report_model(
    groups: Sequence[LotGroup],
    *,
    title: str = "Multi-Lot Grain Analysis Report",
    operator: str = "",
    organization: str = "",
    logo_path: Optional[str] = None,
    units: str = "auto",
    bins: Optional[Dict[str, int]] = None,
    theme: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
    asset_dir: Optional[str] = None,
    baseline: Optional[Dict[str, str]] = None,
    margin: float = DEFAULT_MARGIN_G,
    alpha: float = DEFAULT_ALPHA,
    stats_cfg: Any = None,
    overlay_opacity: float = 1.0,
    chart_options: Optional[Dict[str, Any]] = None,
    calibration: Any = None,
) -> ReportModel:
    """One ``ReportModel`` for every image in ``groups``.

    ``baseline``: ``{part: baseline_lot_label}`` — a part with no entry (or
    a label that names a lot not in that part) gets no equivalence verdicts,
    only the delta matrix + Welch ANOVA (mirrors ``core.lot_compare.
    compare_lots(..., baseline=None)``). ``margin``/``alpha``: TOST
    equivalence parameters, same defaults as the in-app lot-comparison page.
    ``stats_cfg``: forwarded to ``core.metrics.sample_statistics`` (``
    required_fields``/``target_RA_pct`` — an ``AppSettings`` works directly).
    """
    if not groups:
        raise ValueError("build_multi_lot_report_model needs at least one LotGroup.")
    baseline = baseline or {}
    multi_part = len({_part_key(g) for g in groups}) > 1

    all_items: List[ReportImageInput] = []
    for g in groups:
        for it in g.items:
            # dataclasses.replace: never mutate the caller's ReportImageInput
            # (they may reuse the list elsewhere).
            all_items.append(dataclasses.replace(
                it, sample_id=g.part, lot_number=g.lot,
                levels={"job": g.job, "part": g.part, "lot": g.lot}))

    model = ReportModel.from_results(
        all_items, title=title, operator=operator, organization=organization,
        logo_path=logo_path, units=units, bins=bins, theme=theme,
        metadata=metadata, asset_dir=asset_dir,
        hierarchy=[{"key": "job", "label": "Job #"}, {"key": "part", "label": "Part Number"},
                  {"key": "lot", "label": "Lot"}],
        overlay_opacity=overlay_opacity, chart_options=chart_options, calibration=calibration,
    )

    # INN-27 per-lot summary (unchanged rendering) -- one entry per LotGroup,
    # labelled with the part too once more than one part is present so two
    # lots that happen to share a label (different parts) stay distinguishable.
    stats = []
    for g in groups:
        fields = [FieldResult.from_analysis(it.result, field_id=_field_id(it, n))
                  for n, it in enumerate(g.items, 1)]
        st = sample_statistics(fields, stats_cfg)
        st.label = f"{g.part} / {g.lot}" if multi_part else g.lot
        stats.append(st.to_dict())
    model.sample_statistics = stats

    model.sections = _insert_lot_comparison_section(
        model.sections, _lot_comparison_payload(groups, baseline, margin, alpha))
    return model


def _field_id(item: ReportImageInput, n: int) -> str:
    import os
    return os.path.basename(item.image_path or "") or f"image_{n}"


def _lot_comparison_payload(groups: Sequence[LotGroup], baseline: Dict[str, str],
                            margin: float, alpha: float) -> Dict[str, Any]:
    parts: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for g in groups:
        pk = _part_key(g)
        if pk not in parts:
            parts[pk] = {"job": g.job, "part": g.part, "lots": {}}
            order.append(pk)
        fields = [FieldResult.from_analysis(it.result, field_id=_field_id(it, n))
                  for n, it in enumerate(g.items, 1)]
        parts[pk]["lots"][g.lot] = fields

    payload_parts = []
    for pk in order:
        p = parts[pk]
        lots_map = p["lots"]
        base = baseline.get(p["part"])
        base = base if base in lots_map else None
        comparison = compare_lots(lots_map, baseline=base, margin=margin, alpha=alpha)
        payload_parts.append({
            "job": p["job"], "part": p["part"], "lots": list(lots_map.keys()),
            "baseline": base, "comparison": comparison.to_dict(),
        })
    return {"parts": payload_parts}


def _insert_lot_comparison_section(sections: List[Section], payload: Dict[str, Any]
                                   ) -> List[Section]:
    """Right after the Overview section (order 1 in
    ``ReportModel._default_sections``); every later section's order shifts
    by 1 so drag order / raw-data-last stay intact."""
    for s in sections:
        if s.order >= 2:
            s.order += 1
    sections.append(Section(id="lot_comparison", type="lot_comparison", title="Lot Comparison",
                            enabled=True, order=2, payload=payload))
    sections.sort(key=lambda s: s.order)
    return sections
