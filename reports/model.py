"""ReportModel — the editable, serializable source of truth for reports.

``ReportModel`` holds everything the renderers need as plain data (numbers,
strings, paths) — never the original ``AnalysisResult``/ndarray objects —
so a report can be saved as ``report.json``, reloaded later, edited (toggle
a section, reorder images, change a caption) and re-rendered without the
original in-memory analysis session.

Input adapter
-------------
``ReportImageInput`` is the boundary between the analysis layer
(``core.grain_detector.AnalysisResult``) and this package.
``ReportModel.from_results(items, **meta)`` consumes a list of those and
produces a model. Nothing in this module imports Qt or touches the network.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - cv2 is a hard dependency elsewhere
    cv2 = None

from reports.charts import estimate_astm_g

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Input adapter (not part of the JSON model)
# ---------------------------------------------------------------------------

@dataclass
class ReportImageInput:
    """One analysed image, as handed to the report builder.

    ``result`` is a ``core.grain_detector.AnalysisResult``. ``image_bgr`` /
    ``overlay_bgr`` are optional raw arrays (e.g. when the caller has not
    persisted them to disk yet); if omitted, ``image_path`` is used as-is
    and no overlay is embedded.
    """
    image_path: str
    result: Any
    image_bgr: Optional[np.ndarray] = None
    overlay_bgr: Optional[np.ndarray] = None
    sample_id: str = ""
    lot_number: str = ""
    notes: str = ""
    display_name: str = ""
    levels: Dict[str, str] = field(default_factory=dict)
    # Memory for large loads: a zero-argument callable returning the overlay
    # (BGR ndarray or None), called once while the report assets are written
    # and released right after -- so a report over hundreds of images never
    # holds every overlay at once. Used when ``overlay_bgr`` is None.
    overlay_loader: Optional[Callable[[], Optional[np.ndarray]]] = None


# ---------------------------------------------------------------------------
# Plain-data pieces of the model
# ---------------------------------------------------------------------------

@dataclass
class Section:
    id: str
    type: str  # cover|overview_table|combined_distribution|image|parameters|raw_data|custom_text
    title: str = ""
    enabled: bool = True
    order: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Section":
        return cls(
            id=d["id"], type=d["type"], title=d.get("title", ""),
            enabled=d.get("enabled", True), order=d.get("order", 0),
            payload=d.get("payload", {}) or {},
        )


@dataclass
class ImageSummary:
    """Per-image editable fields + plain-data summary stats + grain rows.

    ``grains`` is a list of plain dicts (one per grain) with both px and um
    columns always present so the renderer can pick units without needing
    the original ``AnalysisResult``.
    """
    id: str
    image_path: str
    overlay_path: Optional[str] = None

    # editable
    include: bool = True
    caption: str = ""
    notes: str = ""
    order: int = 0

    # identity / metadata
    sample_id: str = ""
    lot_number: str = ""

    # HIER-01: user-defined folder hierarchy support. ``display_name`` is the
    # image name rendered from the profile's image_name_template (falls back
    # to the file's basename when empty); ``original_name`` preserves the
    # source file name even if the image was renamed on import.
    # ``levels`` optionally overrides the report-level ``hierarchy`` values
    # per row, so one report can span several lots.
    display_name: str = ""
    original_name: str = ""
    levels: Dict[str, str] = field(default_factory=dict)

    # calibration
    px_per_um: float = 0.0
    has_calibration: bool = False

    # summary stats (plain floats/ints, copied out of AnalysisResult)
    grain_count: int = 0
    mean_area_um2: float = 0.0
    std_area_um2: float = 0.0
    median_area_um2: float = 0.0
    min_area_um2: float = 0.0
    max_area_um2: float = 0.0
    mean_diameter_um: float = 0.0
    std_diameter_um: float = 0.0
    mean_circularity: float = 0.0
    mean_aspect_ratio: float = 0.0
    grain_coverage_pct: float = 0.0
    valid_area_um2: float = 0.0
    invalid_area_pct: float = 0.0
    astm_g: Optional[float] = None

    grains: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ImageSummary":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)

    def display(self) -> str:
        """Name to show for this image everywhere (sheet/slide titles,
        Overview table, hyperlinked Image column): the profile-rendered
        ``display_name`` if set, else the file's basename — identical to
        the pre-HIER-01 behaviour when ``display_name`` is empty."""
        return self.display_name or os.path.basename(self.image_path)

    def level_value(self, key: str, default: str = "") -> str:
        """Per-row override of a hierarchy level's value, if any."""
        return self.levels.get(key, default) if self.levels else default


def _grain_row(g: Any) -> Dict[str, Any]:
    return {
        "id": g.grain_id,
        "area_px": g.area_px,
        "area_um2": g.area_um2,
        "diameter_px": g.equivalent_diameter_px,
        "diameter_um": g.equivalent_diameter_um,
        "major_um": g.major_axis_um,
        "minor_um": g.minor_axis_um,
        "perimeter_px": g.perimeter_px,
        "perimeter_um": g.perimeter_um,
        "circularity": g.circularity,
        "aspect_ratio": g.aspect_ratio,
        "eccentricity": g.eccentricity,
        "centroid_x": g.centroid_x,
        "centroid_y": g.centroid_y,
    }


def lot_statistics_from_items(items: List[ReportImageInput], cfg: Any = None) -> List[Dict[str, Any]]:
    """INN-27: one ``SampleStatistics`` dict per lot number among ``items``
    (all items form one group when none carries a lot number).  Every item
    counts as an included field; for audited include/exclude use
    ``data.catalog.fields_for_lot`` + ``core.metrics.sample_statistics``
    and pass the result as ``from_results(sample_statistics=[...])``."""
    from core.metrics import FieldResult, sample_statistics as _stats
    groups: Dict[str, List[Any]] = {}
    for idx, it in enumerate(items, 1):
        fid = os.path.basename(it.image_path or "") or f"image_{idx}"
        groups.setdefault(it.lot_number or "", []).append(
            FieldResult.from_analysis(it.result, field_id=fid))
    out = []
    for lot, fields in groups.items():
        st = _stats(fields, cfg)
        st.label = lot or "All images"
        out.append(st.to_dict())
    return out


def normalize_verdict(v: Any) -> Optional[Dict[str, Any]]:
    """A plain ``dict`` for ``ReportModel.verdict``, or ``None``.

    Accepts a ``data.specs.Verdict``, an equivalent dict, or ``None``.
    Collapses the "no spec attached" case (``overall`` missing/empty/
    ``"no_spec"``) to ``None`` so callers never need to special-case it and
    a report with no spec renders byte-for-byte like before INN-02.
    """
    if v is None:
        return None
    d = v.to_dict() if hasattr(v, "to_dict") else dict(v)
    if not d or not d.get("overall") or d.get("overall") == "no_spec":
        return None
    return d


def _resolve_calibration(explicit: Any, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """FIX-07 backward compatibility: an explicit ``calibration`` payload
    wins; otherwise fall back to ``metadata["calibration"]`` (the only place
    it lived before this field existed, and where
    ``ui.pages.report_builder.apply_calibration`` still mirrors it) so old
    ``report.json`` files keep showing their calibration block after being
    loaded with the new code. ``None``/empty in both -> ``None``."""
    if isinstance(explicit, dict) and explicit:
        return dict(explicit)
    meta_cal = (metadata or {}).get("calibration")
    if isinstance(meta_cal, dict) and meta_cal:
        return dict(meta_cal)
    return None


def _save_bgr(bgr: np.ndarray, asset_dir: str, name: str) -> str:
    os.makedirs(asset_dir, exist_ok=True)
    path = os.path.join(asset_dir, name)
    if cv2 is not None:
        cv2.imwrite(path, bgr, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return path


# ---------------------------------------------------------------------------
# ReportModel
# ---------------------------------------------------------------------------

@dataclass
class ReportModel:
    schema_version: int = SCHEMA_VERSION
    title: str = "Grain Analysis Report"
    operator: str = ""
    organization: str = ""
    logo_path: Optional[str] = None
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    units: str = "auto"  # auto|um|nm
    bins: Dict[str, int] = field(default_factory=lambda: {"area": 0, "diameter": 0})
    theme: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)
    sections: List[Section] = field(default_factory=list)
    images: List[ImageSummary] = field(default_factory=list)

    # HIER-01: user-defined folder hierarchy (Job #/Part Number/Lot, or any
    # profile the workspace defines). Ordered outermost-to-innermost level,
    # each ``{"key": "project"|"sample"|"lot", "label": ..., "value": ...}``.
    # Empty by default so legacy models fall back to the pre-HIER-01
    # Sample/Lot columns sourced from ``ImageSummary.sample_id``/``lot_number``.
    hierarchy: List[Dict[str, str]] = field(default_factory=list)
    # Already-rendered (export_name_template) base filename, without
    # extension, supplied by the UI from the workspace's hierarchy profile.
    # Empty means "use the legacy default" (see ``reports.suggest_filename``).
    export_basename: str = ""
    # INN-27: one ``core.metrics.SampleStatistics.to_dict()`` per lot (the
    # "sample_statistics" section). Empty -> no lot block is rendered, so
    # legacy/single-image reports are unchanged. Rendered as a lot summary
    # block above the Excel Overview table and a "G ± CI" tile on the PPTX
    # summary slide; hide it with a disabled Section of type
    # "sample_statistics" (``is_enabled`` defaults to True).
    sample_statistics: List[Dict[str, Any]] = field(default_factory=list)

    # INN-02: ``data.specs.Verdict.to_dict()`` -- PASS/FAIL/INCONCLUSIVE
    # against a project/sample spec, or ``None`` (the default) when no spec
    # is attached. Spec limits are opt-in: with ``verdict`` at its default
    # ``None``, nothing changes anywhere in either renderer -- no badge, no
    # verdict cell, no conformity statement, no extra columns. A verdict
    # whose ``overall`` is ``"no_spec"`` (or missing/empty) is normalized to
    # ``None`` on load/construction so it can never accidentally render.
    verdict: Optional[Dict[str, Any]] = None

    # FIX-07: ``data.cal_records.report_calibration()`` payload (INN-29
    # scale-verification check) -- ``{"source", "px_per_um", "check",
    # "status", "reason", "warnings", "text"}`` -- or ``None`` (the
    # default) when calibration verification is unused. Historically this
    # only ever lived in ``metadata["calibration"]`` (set by
    # ``ui.pages.report_builder.apply_calibration``); it is still mirrored
    # there for that caller, but this field is the canonical, renderer-facing
    # copy. Entirely optional: absent -> nothing renders, no errors.
    calibration: Optional[Dict[str, Any]] = None

    # UX-16: opacity (0..1) applied to the overlay image in exports —
    # ``1.0`` (the default) is the pre-UX-16 look (the overlay embedded
    # as-is). The Analyze page's overlay opacity slider (UX-05, AppState
    # key ``overlay_opacity``) sets this when a report is (re)built; both
    # renderers alpha-blend the overlay PNG down towards the plain original
    # image by this fraction (``reports.excel_renderer._resized_png``) so a
    # value < 1 fades the grain colouring/outlines without needing the raw
    # label mask.
    overlay_opacity: float = 1.0

    # UX-15: the fully-resolved custom palette (``reports.charts.
    # derive_custom_palette``) when ``theme`` is a custom one — ``None``
    # for a built-in ``theme`` (default). Embedding the resolved colours
    # (not just an id into AppSettings.custom_palettes) keeps a report
    # self-contained: it renders identically later even if the user
    # renames/edits/deletes that saved palette. ``theme`` still carries the
    # saved palette's id (``"custom:<id>"``) so the designer can re-select
    # it in the combo when the report is reopened.
    custom_palette: Optional[Dict[str, str]] = None

    # UX-14: editable chart options — see ``reports.charts.
    # DEFAULT_CHART_OPTIONS`` for the shape and ``resolve_chart_options`` for
    # how a partial/empty dict here resolves. Empty (the default) renders
    # exactly like before UX-14. Editable in the designer's Charts panel;
    # "Save as my default" copies this to ``AppSettings.default_chart_options``
    # (read by ``ui.pages.report_builder.chart_options_arg`` for new reports).
    chart_options: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction from analysis results
    # ------------------------------------------------------------------

    @classmethod
    def from_results(
        cls,
        items: List[ReportImageInput],
        *,
        title: str = "Grain Analysis Report",
        operator: str = "",
        organization: str = "",
        logo_path: Optional[str] = None,
        units: str = "auto",
        bins: Optional[Dict[str, int]] = None,
        theme: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        asset_dir: Optional[str] = None,
        hierarchy: Optional[List[Dict[str, str]]] = None,
        export_basename: str = "",
        sample_statistics: Any = None,
        verdict: Any = None,
        calibration: Any = None,
        overlay_opacity: float = 1.0,
        chart_options: Optional[Dict[str, Any]] = None,
    ) -> "ReportModel":
        """Build a model from freshly-analysed images.

        ``asset_dir``: where to persist ``image_bgr``/``overlay_bgr`` arrays
        as PNGs (needed because the model only stores paths). If omitted and
        an array is supplied without a usable existing file, a temp
        directory is used as a best-effort fallback (callers that want the
        assets to survive should pass a real ``asset_dir``, e.g. alongside
        ``report.json``).
        """
        model = cls(
            title=title, operator=operator, organization=organization,
            logo_path=logo_path, units=units,
            bins=dict(bins) if bins else {"area": 0, "diameter": 0},
            theme=theme, metadata=dict(metadata) if metadata else {},
            hierarchy=[dict(h) for h in hierarchy] if hierarchy else [],
            export_basename=export_basename or "",
            overlay_opacity=float(overlay_opacity) if overlay_opacity is not None else 1.0,
            chart_options=dict(chart_options) if chart_options else {},
        )
        model.calibration = _resolve_calibration(calibration, model.metadata)

        _asset_dir = asset_dir
        images: List[ImageSummary] = []
        for idx, item in enumerate(items, 1):
            img_id = f"img_{idx}"
            res = item.result

            overlay_path = None
            image_path = item.image_path
            if (item.overlay_bgr is None and getattr(res, "overlay_image", None) is None
                    and item.overlay_loader is not None):
                try:
                    lazy = item.overlay_loader()
                except Exception:
                    lazy = None
                if lazy is not None:
                    if _asset_dir is None:
                        _asset_dir = tempfile.mkdtemp(prefix="grain_report_assets_")
                    base = os.path.splitext(os.path.basename(
                        item.image_path or f"image_{idx}"))[0] or f"image_{idx}"
                    overlay_path = _save_bgr(lazy, _asset_dir, f"{idx:03d}_{base}_overlay.png")
                    if item.image_bgr is not None:
                        image_path = _save_bgr(item.image_bgr, _asset_dir,
                                               f"{idx:03d}_{base}_original.png")
                lazy = None
            elif item.image_bgr is not None or item.overlay_bgr is not None:
                if _asset_dir is None:
                    _asset_dir = tempfile.mkdtemp(prefix="grain_report_assets_")
                base = os.path.splitext(os.path.basename(item.image_path or f"image_{idx}"))[0] or f"image_{idx}"
                if item.image_bgr is not None:
                    image_path = _save_bgr(item.image_bgr, _asset_dir, f"{idx:03d}_{base}_original.png")
                overlay_src = item.overlay_bgr if item.overlay_bgr is not None else getattr(res, "overlay_image", None)
                if overlay_src is not None:
                    overlay_path = _save_bgr(overlay_src, _asset_dir, f"{idx:03d}_{base}_overlay.png")
            elif getattr(res, "overlay_image", None) is not None:
                # Caller kept overlay only inside the AnalysisResult.
                if _asset_dir is None:
                    _asset_dir = tempfile.mkdtemp(prefix="grain_report_assets_")
                base = os.path.splitext(os.path.basename(item.image_path or f"image_{idx}"))[0] or f"image_{idx}"
                overlay_path = _save_bgr(res.overlay_image, _asset_dir, f"{idx:03d}_{base}_overlay.png")

            astm_g = estimate_astm_g(res)

            summary = ImageSummary(
                id=img_id,
                image_path=image_path,
                overlay_path=overlay_path,
                caption="",
                notes=item.notes or "",
                order=idx,
                sample_id=item.sample_id or "",
                lot_number=item.lot_number or "",
                display_name=item.display_name or "",
                original_name=os.path.basename(item.image_path) if item.image_path else "",
                levels=dict(item.levels) if item.levels else {},
                px_per_um=float(getattr(res, "px_per_um", 0.0) or 0.0),
                has_calibration=bool(getattr(res, "has_calibration", False)),
                grain_count=int(getattr(res, "grain_count", 0)),
                mean_area_um2=float(getattr(res, "mean_area_um2", 0.0)),
                std_area_um2=float(getattr(res, "std_area_um2", 0.0)),
                median_area_um2=float(getattr(res, "median_area_um2", 0.0)),
                min_area_um2=float(getattr(res, "min_area_um2", 0.0)),
                max_area_um2=float(getattr(res, "max_area_um2", 0.0)),
                mean_diameter_um=float(getattr(res, "mean_diameter_um", 0.0)),
                std_diameter_um=float(getattr(res, "std_diameter_um", 0.0)),
                mean_circularity=float(getattr(res, "mean_circularity", 0.0)),
                mean_aspect_ratio=float(getattr(res, "mean_aspect_ratio", 0.0)),
                grain_coverage_pct=float(getattr(res, "grain_coverage_pct", 0.0)),
                valid_area_um2=float(getattr(res, "valid_area_um2", 0.0)),
                invalid_area_pct=float(getattr(res, "invalid_area_pct", 0.0)),
                astm_g=astm_g,
                grains=[_grain_row(g) for g in (res.grains or [])],
            )
            images.append(summary)

        model.images = images
        model.sections = model._default_sections()
        if isinstance(sample_statistics, str) and sample_statistics == "auto":
            model.sample_statistics = lot_statistics_from_items(items)
        elif sample_statistics:
            model.sample_statistics = [
                s.to_dict() if hasattr(s, "to_dict") else dict(s) for s in sample_statistics]
        model.verdict = normalize_verdict(verdict)
        return model

    def _default_sections(self) -> List[Section]:
        secs = [
            Section(id="cover", type="cover", title=self.title, enabled=True, order=0),
            Section(id="overview_table", type="overview_table", title="Overview", enabled=True, order=1),
            Section(id="combined_distribution", type="combined_distribution",
                    title="Summary Charts", enabled=True, order=2),
        ]
        for i, img in enumerate(self.images, start=3):
            secs.append(Section(
                id=f"image_{img.id}", type="image",
                title=img.display() or img.id,
                enabled=True, order=i, payload={"image_id": img.id},
            ))
        n = len(self.images) + 3
        secs.append(Section(id="parameters", type="parameters", title="Methods", enabled=True, order=n))
        secs.append(Section(id="raw_data", type="raw_data", title="Raw Data", enabled=True, order=n + 1))
        return secs

    # ------------------------------------------------------------------
    # Helpers used by renderers
    # ------------------------------------------------------------------

    def get_section(self, type_: str, image_id: Optional[str] = None) -> Optional[Section]:
        for s in self.sections:
            if s.type != type_:
                continue
            if image_id is not None:
                if s.payload.get("image_id") == image_id:
                    return s
                continue
            return s
        return None

    def is_enabled(self, type_: str, image_id: Optional[str] = None, default: bool = True) -> bool:
        s = self.get_section(type_, image_id)
        return s.enabled if s is not None else default

    def section_title(self, type_: str, image_id: Optional[str] = None, default: str = "") -> str:
        s = self.get_section(type_, image_id)
        return s.title if (s is not None and s.title) else default

    def ordered_images(self, included_only: bool = True) -> List[ImageSummary]:
        imgs = [i for i in self.images if (not included_only or i.include)]
        imgs = [i for i in imgs if self.is_enabled("image", i.id, default=True)]
        return sorted(imgs, key=lambda i: i.order)

    # ------------------------------------------------------------------
    # HIER-01: user-defined folder hierarchy helpers used by the renderers
    # ------------------------------------------------------------------

    def hierarchy_header(self) -> str:
        """'Job #: 24-117 | Part Number: 7718-A | Lot: L-44A' or '' when no
        hierarchy is set (legacy models)."""
        return " | ".join(f"{h.get('label', h.get('key', ''))}: {h.get('value', '')}"
                           for h in self.hierarchy)

    def level_columns(self) -> List[Tuple[str, str]]:
        """[(key, label), ...] for the per-image table columns — the user's
        hierarchy when set, else the legacy fixed (key, label) pairs."""
        if self.hierarchy:
            return [(h.get("key", ""), h.get("label", h.get("key", ""))) for h in self.hierarchy]
        return [("sample", "Sample"), ("lot", "Lot")]

    def row_levels(self, img: ImageSummary) -> List[str]:
        """Values for ``level_columns()`` for one image row, honouring a
        per-image ``levels`` override (multi-lot reports)."""
        if self.hierarchy:
            return [img.level_value(h.get("key", ""), h.get("value", "")) for h in self.hierarchy]
        return [img.sample_id, img.lot_number]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "operator": self.operator,
            "organization": self.organization,
            "logo_path": self.logo_path,
            "date": self.date,
            "units": self.units,
            "bins": dict(self.bins),
            "theme": self.theme,
            "metadata": dict(self.metadata),
            "sections": [s.to_dict() for s in self.sections],
            "images": [i.to_dict() for i in self.images],
            "hierarchy": [dict(h) for h in self.hierarchy],
            "export_basename": self.export_basename,
            "sample_statistics": [dict(s) for s in self.sample_statistics],
            "verdict": normalize_verdict(self.verdict),
            "calibration": dict(self.calibration) if self.calibration else None,
            "overlay_opacity": self.overlay_opacity,
            "custom_palette": dict(self.custom_palette) if self.custom_palette else None,
            "chart_options": dict(self.chart_options) if self.chart_options else {},
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReportModel":
        return cls(
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            title=d.get("title", "Grain Analysis Report"),
            operator=d.get("operator", ""),
            organization=d.get("organization", ""),
            logo_path=d.get("logo_path"),
            date=d.get("date", ""),
            units=d.get("units", "auto"),
            bins=dict(d.get("bins") or {"area": 0, "diameter": 0}),
            theme=d.get("theme", "default"),
            metadata=dict(d.get("metadata") or {}),
            sections=[Section.from_dict(s) for s in d.get("sections", [])],
            images=[ImageSummary.from_dict(i) for i in d.get("images", [])],
            hierarchy=[dict(h) for h in (d.get("hierarchy") or [])],
            export_basename=d.get("export_basename", ""),
            sample_statistics=[dict(s) for s in (d.get("sample_statistics") or [])],
            verdict=normalize_verdict(d.get("verdict")),
            calibration=_resolve_calibration(d.get("calibration"), d.get("metadata") or {}),
            # UX-16: absent in reports saved before this field existed ->
            # 1.0, the exact look those reports already had.
            overlay_opacity=float(d.get("overlay_opacity", 1.0) or 1.0),
            custom_palette=dict(d["custom_palette"]) if d.get("custom_palette") else None,
            chart_options=dict(d["chart_options"]) if d.get("chart_options") else {},
        )

    @classmethod
    def from_json(cls, s: str) -> "ReportModel":
        return cls.from_dict(json.loads(s))

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        return path

    @classmethod
    def load(cls, path: str) -> "ReportModel":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        problems: List[str] = []
        if self.schema_version != SCHEMA_VERSION:
            problems.append(f"Unexpected schema_version {self.schema_version} (expected {SCHEMA_VERSION}).")
        if not self.title or not str(self.title).strip():
            problems.append("title is empty.")
        if self.units not in ("auto", "um", "nm"):
            problems.append(f"units must be auto|um|nm, got {self.units!r}.")
        if not (0.0 <= float(self.overlay_opacity) <= 1.0):
            problems.append(f"overlay_opacity must be between 0 and 1, got {self.overlay_opacity!r}.")
        if str(self.theme).startswith("custom") and not self.custom_palette:
            problems.append("theme is a custom palette but custom_palette is not set.")
        for metric in ("area", "diameter"):
            opt = (self.chart_options or {}).get(metric) or {}
            lo, hi = opt.get("min"), opt.get("max")
            if lo is not None and hi is not None and float(lo) > float(hi):
                problems.append(f"chart_options[{metric!r}]: min ({lo}) is greater than max ({hi}).")

        ids = [i.id for i in self.images]
        if len(ids) != len(set(ids)):
            problems.append("Duplicate image ids.")
        for img in self.images:
            if not img.image_path:
                problems.append(f"Image {img.id} has no image_path.")
            elif not os.path.exists(img.image_path):
                problems.append(f"Image {img.id} path does not exist: {img.image_path}")
            if img.overlay_path and not os.path.exists(img.overlay_path):
                problems.append(f"Image {img.id} overlay path does not exist: {img.overlay_path}")

        sec_ids = [s.id for s in self.sections]
        if len(sec_ids) != len(set(sec_ids)):
            problems.append("Duplicate section ids.")
        for s in self.sections:
            if s.type == "image":
                iid = s.payload.get("image_id")
                if iid not in ids:
                    problems.append(f"Section {s.id} references unknown image_id {iid!r}.")

        return problems
