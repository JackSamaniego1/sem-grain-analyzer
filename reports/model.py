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
from typing import Any, Dict, List, Optional

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
        )

        _asset_dir = asset_dir
        images: List[ImageSummary] = []
        for idx, item in enumerate(items, 1):
            img_id = f"img_{idx}"
            res = item.result

            overlay_path = None
            image_path = item.image_path
            if item.image_bgr is not None or item.overlay_bgr is not None:
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

            astm_g = estimate_astm_g(getattr(res, "mean_diameter_um", 0.0))

            summary = ImageSummary(
                id=img_id,
                image_path=image_path,
                overlay_path=overlay_path,
                caption="",
                notes=item.notes or "",
                order=idx,
                sample_id=item.sample_id or "",
                lot_number=item.lot_number or "",
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
                title=os.path.basename(img.image_path) or img.id,
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
