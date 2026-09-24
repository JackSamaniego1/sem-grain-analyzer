"""
Reports page logic — no widgets (REP-05/06/07).

* :func:`collect_inputs` snapshots the open session's FILTERED results on the
  GUI thread; :func:`build_model` turns them into a ``reports.ReportModel``
  off the GUI thread (overlays are written to ``<session>/report_assets``).
* :func:`results_fingerprint` identifies the numbers a report was built from,
  so the page can tell when re-analysis / filter edits made it stale.
* :func:`merge_refresh` swaps fresh numbers into an edited report while
  keeping every user edit (title, captions, notes, order, sections, units,
  bins, grain annotations, export history).
* :func:`render_outputs` renders Excel and/or PowerPoint from a plain dict
  copy of the model (safe to run on a pool thread).

Grain exclusion is NOT stored in the report: it goes through the app's
manual-exclusion mechanism (``AppState.delete_grains``) so Review, report and
exports always agree.  Per-grain annotations live in the report's grain rows
(``grain["note"]``) and are appended to the image notes on export.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from reports.model import ImageSummary, ReportImageInput, ReportModel, Section

REPORT_ASSETS = "report_assets"
FIXED_ORDER = ("cover", "overview_table", "combined_distribution", "images", "parameters",
               "raw_data")
MAX_EXPORT_HISTORY = 25

# Colour of each Excel tab / outline chip.  Mirrors reports.charts.TAB_COLORS
# (the renderer's palette) so the outline reads like the workbook's tabs.
from reports.charts import TAB_COLORS  # noqa: E402

SECTION_COLORS = {
    "cover": TAB_COLORS["overview"], "overview_table": TAB_COLORS["overview"],
    "combined_distribution": TAB_COLORS["charts"], "image": TAB_COLORS["image"],
    "images": TAB_COLORS["image"], "parameters": TAB_COLORS["methods"],
    "raw_data": TAB_COLORS["raw"], "custom_text": "#6A4C93",
}

SECTION_LABELS = {
    "cover": "Cover", "overview_table": "Overview table",
    "combined_distribution": "Summary charts", "images": "Images",
    "parameters": "Methods", "raw_data": "Raw data", "custom_text": "Text",
}

# Where each section ends up in the two outputs (shown in the preview header).
SECTION_TARGETS = {
    "cover": ("Overview sheet header", "Title slide"),
    "overview_table": ("Overview sheet", "Executive summary slide"),
    "combined_distribution": ("Summary Charts sheet", "2 distribution slides"),
    "image": ("One sheet per image", "One slide per image"),
    "parameters": ("Methods sheet", "Methods slide"),
    "raw_data": ("Raw sheets — always last", "Appendix slide"),
    "custom_text": ("Notes sheet (purple tab)", "Text slide"),
}


# ======================================================================
# Snapshot + build
# ======================================================================

def _session_ids(state) -> Tuple[str, str]:
    s = state.session
    if s is None:
        return "", ""
    sample = s.meta.sample_id or (s.sample_meta or {}).get("sample_id", "") or ""
    lot = s.meta.lot_number or (s.lot_meta or {}).get("lot_number", "") or ""
    return str(sample), str(lot)


def results_fingerprint(state) -> str:
    """Hash of the numbers a report would be built from (kept grain ids,
    their total area and the calibration per analysed image)."""
    h = hashlib.sha1()
    for im in state.images():
        r = im.result
        if r is None:
            continue
        ids = sorted(int(g.grain_id) for g in r.grains)
        area = round(float(sum(g.area_px for g in r.grains)), 3)
        h.update(json.dumps([im.filename, ids, area, round(float(r.px_per_um or 0.0), 6)])
                 .encode("utf-8"))
    excl = field_exclusions(state)
    if excl:                      # INN-27: excluding a field changes the lot block
        h.update(json.dumps(sorted(excl.items())).encode("utf-8"))
    return h.hexdigest()[:16]


# ======================================================================
# INN-27: lot statistics block (opt-in "auto")
# ======================================================================

def field_exclusions(state) -> Dict[str, str]:
    """``{filename: reason}`` of the open session's images excluded from the
    lot statistics (Projects ▸ Lot result), read from its manifest on disk."""
    s = state.session
    if s is None:
        return {}
    try:
        m = json.loads((Path(s.path) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(i.get("filename", "")): str(i.get("exclusion_reason") or "")
            for i in (m.get("images") or []) if isinstance(i, dict)
            and i.get("included", True) is False}


def sample_statistics_arg(state, inputs: Sequence[ReportImageInput]):
    """What the report export passes as ``from_results(sample_statistics=)``:
    ``"auto"`` (one lot block per lot, every image a field) -- or, when the
    user excluded fields or changed the required fields / target %RA in
    Settings, the same blocks computed with those choices.  ``None`` for a
    single image (a lot result needs >= 2 fields; the report is unchanged)."""
    if len(inputs) < 2:
        return None
    from core.metrics import FieldResult, sample_statistics
    settings = getattr(state, "settings", None)
    cfg = {"required_fields": int(getattr(settings, "required_fields", 5) or 5),
           "target_RA_pct": float(getattr(settings, "target_RA_pct", 10.0) or 10.0)}
    excl = field_exclusions(state)
    if not excl and cfg == {"required_fields": 5, "target_RA_pct": 10.0}:
        return "auto"
    groups: Dict[str, list] = {}
    for idx, it in enumerate(inputs, 1):
        name = os.path.basename(it.image_path or "") or f"image_{idx}"
        f = FieldResult.from_analysis(it.result, field_id=name)
        if name in excl:
            f.included, f.exclusion_reason = False, excl[name] or None
        groups.setdefault(it.lot_number or "", []).append(f)
    out = []
    for lot, fields in groups.items():
        st = sample_statistics(fields, cfg)
        st.label = lot or "All images"
        out.append(st.to_dict())
    return out


def analysed_count(state) -> int:
    return sum(1 for im in state.images() if im.result is not None)


def _filters_text(opts) -> str:
    on = []
    if opts.exclude_border:
        on.append("border grains")
    if opts.exclude_touching_invalid:
        on.append("grains touching excluded regions")
    if opts.exclude_low_contrast:
        on.append("low-contrast / dark false grains")
    if opts.min_area_px:
        on.append(f"area < {opts.min_area_px} px")
    if opts.max_area_px:
        on.append(f"area > {opts.max_area_px} px")
    if opts.max_aspect_ratio:
        on.append(f"aspect ratio > {opts.max_aspect_ratio:g}")
    if opts.min_circularity:
        on.append(f"circularity < {opts.min_circularity:g}")
    return ("Excluded: " + ", ".join(on)) if on else "None"


def session_metadata(state) -> dict:
    s = state.session
    if s is None:
        return {}
    params = {k: v for k, v in (s.params or {}).items() if not isinstance(v, (dict, list))}
    mode = params.pop("detection_mode", "") or s.meta.detector_mode or ""
    params["grain filters"] = _filters_text(s.filters)
    manual = sum(len(im.manual) for im in s.images)
    if manual:
        params["grains removed by hand"] = manual
    return {
        "detection_mode": mode,
        "detection_params": params,
        "instrument": s.meta.instrument or "",
        "magnification": s.meta.magnification or "",
        "session": s.title,
        "project": s.meta.project or (s.project_meta or {}).get("name", ""),
    }


def collect_inputs(state) -> List[ReportImageInput]:
    """GUI-thread snapshot of every analysed image (filtered results).

    HIER-01: each image carries its display name (the stem of the file name
    the profile's image-name template produced on import)."""
    from ui.workers import snapshot_result
    sample, lot = _session_ids(state)
    out: List[ReportImageInput] = []
    for im in state.images():
        if im.result is None:
            continue
        res = snapshot_result(im.result)
        path = str(im.path) if im.path else im.filename
        out.append(ReportImageInput(image_path=path, result=res, overlay_bgr=res.overlay_image,
                                    sample_id=sample, lot_number=lot,
                                    display_name=im.display_name))
    return out


# ======================================================================
# HIER-01: hierarchy labels / names from the workspace profile
# ======================================================================

def report_context(state) -> dict:
    """Template context of the open session / lot record."""
    from ui import hierarchy_ui as hui
    s = state.session
    prof = state.profile
    ctx = hui.context_for_path(s.path if s is not None else None, prof)
    if s is not None:
        sample, lot = _session_ids(state)
        proj = s.meta.project or (s.project_meta or {}).get("name", "")
        for k, v in (("project", proj), ("sample", sample), ("lot", lot)):
            if v and not ctx.get(k):
                ctx[k] = v
        if not ctx.get("operator"):
            ctx["operator"] = s.meta.operator or state.operator()
    return ctx


def hierarchy_defaults(state) -> dict:
    """``hierarchy`` rows, ``export_basename`` and default ``title`` for a
    report of the open session, rendered from the workspace profile."""
    from ui import hierarchy_ui as hui
    prof = state.profile
    ctx = report_context(state)
    s = state.session
    title = hui.report_title(prof, ctx) or (f"Grain Analysis Report — {s.title}" if s
                                            else "Grain Analysis Report")
    return {"hierarchy": hui.hierarchy_rows(prof, ctx),
            "export_basename": hui.export_basename(prof, ctx),
            "title": title}


def apply_profile(model: ReportModel, defaults: dict) -> bool:
    """Profile edited (levels renamed, templates changed): relabel the
    report.  The title and export name follow the profile only while the
    user has not typed their own (the last automatic value is remembered in
    ``metadata``).  Returns True when anything changed."""
    changed = False
    new_h = [dict(h) for h in defaults.get("hierarchy") or []]
    if new_h and new_h != model.hierarchy:
        model.hierarchy = new_h
        changed = True
    meta = model.metadata
    for fld, key in (("export_basename", "auto_export_basename"), ("title", "auto_title")):
        new = defaults.get(fld, "")
        if not new:
            continue
        cur = getattr(model, fld) or ""
        auto = meta.get(key)
        follows = auto is None or cur == auto or not cur
        if not follows:
            continue
        if cur != new:
            setattr(model, fld, new)
            if fld == "title":
                sec = model.get_section("cover")
                if sec is not None:
                    sec.title = new
            changed = True
        meta[key] = new
    return changed


def build_model(inputs: Sequence[ReportImageInput], *, title: str, operator: str,
                organization: str = "", logo_path: Optional[str] = None,
                metadata: Optional[dict] = None, asset_dir: Optional[str] = None,
                fingerprint: str = "", hierarchy: Optional[list] = None,
                export_basename: str = "", sample_statistics=None) -> ReportModel:
    """Pool-thread: ReportModel from snapshots (writes overlay PNGs).
    ``sample_statistics``: see :func:`sample_statistics_arg` (INN-27)."""
    if asset_dir:
        os.makedirs(asset_dir, exist_ok=True)
    meta = dict(metadata or {})
    meta["results_fingerprint"] = fingerprint
    meta.setdefault("exports", [])
    meta["auto_title"] = title
    if export_basename:
        meta["auto_export_basename"] = export_basename
    model = ReportModel.from_results(list(inputs), title=title, operator=operator,
                                     organization=organization, logo_path=logo_path,
                                     metadata=meta, asset_dir=asset_dir,
                                     hierarchy=hierarchy, export_basename=export_basename,
                                     sample_statistics=sample_statistics)
    normalize(model)
    return model


# ======================================================================
# Ordering
# ======================================================================

def image_sections(model: ReportModel) -> List[Section]:
    return [s for s in model.sections if s.type == "image"]


def outline_order(model: ReportModel) -> List[str]:
    """Top-level outline entries as section ids, with the pseudo id
    ``"images"`` standing for the per-image block."""
    secs = sorted(model.sections, key=lambda s: s.order)
    out: List[str] = []
    for s in secs:
        if s.type == "image":
            if "images" not in out:
                out.append("images")
            continue
        out.append(s.id)
    if "images" not in out:
        i = out.index("combined_distribution") + 1 if "combined_distribution" in out else len(out)
        out.insert(i, "images")
    return out


def apply_order(model: ReportModel, top: Sequence[str], image_ids: Sequence[str]) -> None:
    """Write an outline order back into ``Section.order`` / ``ImageSummary.order``.

    Structural rules the renderers rely on are enforced here: Cover first,
    Raw data last (the lab manager's "raw data pages at the end")."""
    top = [t for t in top if t not in ("cover", "raw_data")]
    top = ["cover"] + top + ["raw_data"]
    by_img = {i.id: i for i in model.images}
    ordered_imgs = [by_img[i] for i in image_ids if i in by_img]
    ordered_imgs += [i for i in model.images if i not in ordered_imgs]
    for n, img in enumerate(ordered_imgs, 1):
        img.order = n
    sec_by_id = {s.id: s for s in model.sections}
    img_secs = {s.payload.get("image_id"): s for s in image_sections(model)}
    k = 0
    for t in top:
        if t == "images":
            for img in ordered_imgs:
                s = img_secs.get(img.id)
                if s is not None:
                    s.order = k
                    k += 1
            continue
        s = sec_by_id.get(t)
        if s is not None:
            s.order = k
            k += 1
    model.sections.sort(key=lambda s: s.order)
    model.images.sort(key=lambda i: i.order)


def normalize(model: ReportModel) -> None:
    """Make sure every image has a section and orders are consistent."""
    have = {s.payload.get("image_id") for s in image_sections(model)}
    known = {i.id for i in model.images}
    model.sections = [s for s in model.sections
                      if s.type != "image" or s.payload.get("image_id") in known]
    for img in model.images:
        if img.id not in have:
            model.sections.append(Section(id=f"image_{img.id}", type="image",
                                          title=img.display() or img.id,
                                          order=10_000 + img.order,
                                          payload={"image_id": img.id}))
    for fixed, title in (("parameters", "Methods"), ("raw_data", "Raw Data")):
        if model.get_section(fixed) is None:
            model.sections.append(Section(id=fixed, type=fixed, title=title, order=20_000))
    images_sorted = [i.id for i in sorted(model.images, key=lambda i: i.order)]
    apply_order(model, outline_order(model), images_sorted)


def add_custom_text(model: ReportModel, after: Optional[str] = None,
                    title: str = "Notes", body: str = "") -> Section:
    n = 1
    ids = {s.id for s in model.sections}
    while f"text_{n}" in ids:
        n += 1
    sec = Section(id=f"text_{n}", type="custom_text", title=title, enabled=True,
                  order=0, payload={"body": body})
    model.sections.append(sec)
    top = [t for t in outline_order(model) if t != sec.id]
    anchor = after if after in top else ("parameters" if "parameters" in top else top[-1])
    idx = top.index(anchor) + 1 if anchor != "raw_data" else len(top) - 1
    top.insert(idx, sec.id)
    apply_order(model, top, [i.id for i in sorted(model.images, key=lambda i: i.order)])
    return sec


def remove_section(model: ReportModel, section_id: str) -> None:
    model.sections = [s for s in model.sections if not (s.id == section_id and s.type == "custom_text")]


# ======================================================================
# Refresh-numbers merge
# ======================================================================

def _key(img: ImageSummary) -> str:
    return os.path.basename(img.image_path or img.id).lower()


def merge_refresh(old: ReportModel, new: ReportModel) -> ReportModel:
    """``new`` numbers + every edit made to ``old``.  Image ids of matched
    images are preserved so the page's selection survives a refresh."""
    out = new
    defaults = {"hierarchy": list(new.hierarchy), "export_basename": new.export_basename,
                "title": new.metadata.get("auto_title", "")}
    for f in ("title", "operator", "organization", "logo_path", "date", "units", "theme",
              "export_basename"):
        setattr(out, f, getattr(old, f))
    out.hierarchy = [dict(h) for h in old.hierarchy]
    out.bins = dict(old.bins)
    meta = dict(old.metadata)
    for k in ("detection_mode", "detection_params", "instrument", "magnification", "session",
              "project", "results_fingerprint"):
        if k in new.metadata:
            meta[k] = new.metadata[k]
    out.metadata = meta
    apply_profile(out, defaults)

    old_by_key = {_key(i): i for i in old.images}
    used_ids = {i.id for i in old.images}
    next_order = max([i.order for i in old.images] or [0]) + 1
    seq = len(used_ids) + 1
    for img in out.images:
        prev = old_by_key.get(_key(img))
        if prev is not None:
            img.id = prev.id
            img.include, img.caption, img.notes, img.order = (prev.include, prev.caption,
                                                              prev.notes, prev.order)
            notes = {int(g["id"]): g["note"] for g in prev.grains if g.get("note")}
            for g in img.grains:
                if int(g["id"]) in notes:
                    g["note"] = notes[int(g["id"])]
        else:
            while f"img_{seq}" in used_ids:
                seq += 1
            img.id = f"img_{seq}"
            used_ids.add(img.id)
            img.order = next_order
            next_order += 1

    old_secs = {s.id: s for s in old.sections if s.type != "image"}
    old_img_secs = {s.payload.get("image_id"): s for s in image_sections(old)}
    secs: List[Section] = []
    for s in out.sections:
        if s.type == "image":
            continue
        prev = old_secs.get(s.id)
        if prev is not None:
            s.enabled, s.title, s.order = prev.enabled, prev.title, prev.order
        secs.append(s)
    for s in old.sections:
        if s.type == "custom_text":
            secs.append(copy.deepcopy(s))
    for img in out.images:
        prev = old_img_secs.get(img.id)
        secs.append(Section(id=f"image_{img.id}", type="image",
                            title=prev.title if prev else img.display(),
                            enabled=prev.enabled if prev else True,
                            order=prev.order if prev else 10_000 + img.order,
                            payload={"image_id": img.id}))
    out.sections = secs
    normalize(out)
    return out


# ======================================================================
# Rendering
# ======================================================================

def prepare_render_model(model: ReportModel) -> ReportModel:
    """Copy tuned for the renderers: included images renumbered 1..k in the
    designer's order, grain annotations appended to the image notes."""
    rm = ReportModel.from_dict(copy.deepcopy(model.to_dict()))
    inc = [i for i in sorted(rm.images, key=lambda i: i.order) if i.include]
    exc = [i for i in sorted(rm.images, key=lambda i: i.order) if not i.include]
    for n, img in enumerate(inc + exc, 1):
        img.order = n
    for img in rm.images:
        ann = [f"#{g['id']}: {g['note']}" for g in img.grains if str(g.get("note", "")).strip()]
        if ann:
            img.notes = (img.notes + "\n" if img.notes else "") + "Grain notes — " + "; ".join(ann)
    return rm


def render_outputs(model_dict: dict, jobs: Sequence[Tuple[str, str]]) -> List[str]:
    """Pool-thread: render ``[(kind, path), ...]`` (kind = xlsx | pptx).

    ``reports.excel_renderer``/``reports.pptx_renderer`` render everything
    the designer can edit natively — including ``custom_text`` sections and
    section order/enabled/title — so no UI-side post-processing is needed
    (REP-08 removed the old ``add_custom_text_slides`` hack that used to
    reach into ``reports.pptx_renderer``'s private helpers from here)."""
    from reports.excel_renderer import render_excel
    from reports.pptx_renderer import render_pptx
    rm = prepare_render_model(ReportModel.from_dict(model_dict))
    done: List[str] = []
    for kind, path in jobs:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if kind == "xlsx":
            render_excel(rm, path)
        else:
            render_pptx(rm, path)
        done.append(path)
    return done


def only_image_model(model: ReportModel, image_path: str) -> ReportModel:
    """Copy of ``model`` restricted to one image (Export current image)."""
    m = ReportModel.from_dict(copy.deepcopy(model.to_dict()))
    key = os.path.basename(image_path).lower()
    for img in m.images:
        img.include = _key(img) == key
    return m


# ======================================================================
# Files
# ======================================================================

def slug(text: str, fallback: str = "report") -> str:
    s = re.sub(r"[^\w\-]+", "_", text or "", flags=re.UNICODE).strip("_")
    return (s or fallback)[:60]


def default_export_path(session_path: Path, title: str, kind: str,
                        stamp: Optional[str] = None, basename: str = "") -> Path:
    """``<session>/exports/<name>.<kind>``, never overwriting.  HIER-01: a
    profile-rendered ``basename`` ("24-117_7718-A_L-44A_Grain_Report_20260924")
    is used as-is; otherwise ``<title>_<stamp>``."""
    d = Path(session_path) / "exports"
    if basename.strip():
        from data.models import sanitize_name
        base = sanitize_name(basename.strip())
    else:
        stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M")
        base = f"{slug(title)}_{stamp}"
    p = d / f"{base}.{kind}"
    n = 2
    while p.exists():
        p = d / f"{base}_{n}.{kind}"
        n += 1
    return p


def copy_logo(src: str, session_path: Path) -> str:
    d = Path(session_path) / REPORT_ASSETS
    d.mkdir(parents=True, exist_ok=True)
    dst = d / ("logo" + Path(src).suffix.lower())
    if Path(src).resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    return str(dst)


def record_export(model: ReportModel, paths: Sequence[str], operator: str) -> None:
    hist = list(model.metadata.get("exports") or [])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for p in paths:
        hist.append({"file": os.path.basename(p), "path": str(p), "time": now,
                     "operator": operator,
                     "kind": "PowerPoint" if str(p).lower().endswith(".pptx") else "Excel"})
    model.metadata["exports"] = hist[-MAX_EXPORT_HISTORY:]


def save_report(session_path: Path, model_dict: dict) -> str:
    """Pool-thread: write report.json through the data layer."""
    from data.session_io import update_session
    update_session(session_path, report=model_dict)
    return datetime.now().strftime("%H:%M")


def load_report(session_path: Path) -> Optional[dict]:
    """Pool-thread: report.json as a dict (None when no report was built)."""
    from data.models import read_json
    p = Path(session_path) / "report.json"
    try:
        d = read_json(p) if p.exists() else {}
    except Exception:
        return None
    return d if d.get("sections") is not None and d.get("images") is not None else None


# ======================================================================
# Preview data (same helpers the Excel renderer uses)
# ======================================================================

def overview_table(model: ReportModel):
    """(header, rows, total_row) exactly as the Overview sheet prints them
    (strings, already formatted with the sheet's number formats)."""
    import numpy as np
    from reports.charts import resolve_units
    from reports.excel_renderer import _combined_unit, _row_size_stats

    images = model.ordered_images(included_only=True)
    au, du = _combined_unit(model, images)
    hier = bool(model.hierarchy)
    lead = (["#", "Image", "File"] + [lab for _, lab in model.level_columns()]) if hier \
        else ["#", "Image", "Sample", "Lot"]
    header = lead + ["Grains", f"Mean Area ({au})*",
              f"Median Area ({au})*", f"Std Area ({au})*", f"Mean Diameter ({du})*",
              f"Std Diameter ({du})*", "Units", "Coverage %", "Invalid %", "Mean Circularity",
              "Mean Aspect Ratio", "ASTM G"]
    rows = []
    for n, img in enumerate(sorted(images, key=lambda i: i.order), 1):
        au_i, du_i, mean_a, med_a, std_a, mean_d, std_d = _row_size_stats(model, img)
        lead_v = ([str(n), img.display(), os.path.basename(img.image_path)]
                  + [v or "" for v in model.row_levels(img)]) if hier \
            else [str(n), img.display(), img.sample_id, img.lot_number]
        rows.append(lead_v + [str(img.grain_count), f"{mean_a:.2f}", f"{med_a:.2f}", f"{std_a:.2f}",
                     f"{mean_d:.2f}", f"{std_d:.2f}", f"{au_i} / {du_i}",
                     f"{img.grain_coverage_pct:.1f}%", f"{img.invalid_area_pct:.1f}%",
                     f"{img.mean_circularity:.4f}", f"{img.mean_aspect_ratio:.4f}",
                     f"{img.astm_g:.2f}" if img.astm_g is not None else "—"])
    total = None
    grains = [g for img in images for g in img.grains]
    if grains:
        cal = all(i.has_calibration for i in images)
        if cal:
            _, am, _, dm = resolve_units(images[0].px_per_um, model.units)
            areas = np.array([g["area_um2"] for g in grains]) * am
            diams = np.array([g["diameter_um"] for g in grains]) * dm
        else:
            areas = np.array([g["area_px"] for g in grains], dtype=float)
            diams = np.array([g["diameter_px"] for g in grains], dtype=float)
        total = ["", "Combined (all images)"] + [""] * (len(lead) - 2) + [str(len(grains)),
                 f"{np.mean(areas):.2f}", f"{np.median(areas):.2f}", f"{np.std(areas):.2f}",
                 f"{np.mean(diams):.2f}", f"{np.std(diams):.2f}",
                 f"{au} / {du}" if cal else "px² / px",
                 f"{np.mean([i.grain_coverage_pct for i in images]):.2f}",
                 f"{np.mean([i.invalid_area_pct for i in images]):.2f}",
                 f"{np.mean([g['circularity'] for g in grains]):.4f}",
                 f"{np.mean([g['aspect_ratio'] for g in grains]):.4f}", "—"]
    return header, rows, total


def combined_values(model: ReportModel, kind: str):
    """(values, unit) of all included grains, in report units (as the
    Summary Charts sheet computes them)."""
    from reports.charts import resolve_units
    images = model.ordered_images(included_only=True)
    grains = [g for img in images for g in img.grains]
    if images and all(i.has_calibration for i in images):
        au, am, du, dm = resolve_units(images[0].px_per_um, model.units)
        if kind == "area":
            return [g["area_um2"] * am for g in grains], au
        return [g["diameter_um"] * dm for g in grains], du
    if kind == "area":
        return [g["area_px"] for g in grains], "px²"
    return [g["diameter_px"] for g in grains], "px"


# ======================================================================
# Validation hints
# ======================================================================

def problem_hints(model: ReportModel) -> List[Tuple[str, str, str]]:
    """(severity, problem, fix hint) for ``model.validate()`` findings."""
    out = []
    names = {i.id: os.path.basename(i.image_path) for i in model.images}
    for p in model.validate():
        m = re.match(r"Image (\w+) path does not exist", p)
        if m:
            out.append(("danger", f"{names.get(m.group(1), m.group(1))}: image file is missing",
                        "The file was moved or deleted. Put it back in the session's images "
                        "folder, or untick the image in the outline to leave it out."))
            continue
        m = re.match(r"Image (\w+) overlay path does not exist", p)
        if m:
            out.append(("warning", f"{names.get(m.group(1), m.group(1))}: grain overlay is missing",
                        "Click “Refresh numbers” to regenerate the overlay images."))
            continue
        if p.startswith("title is empty"):
            out.append(("warning", "The report has no title", "Enter a title in Document ▸ Title."))
            continue
        out.append(("warning", p, "Rebuild the report from the session to repair it."))
    if model.logo_path and not os.path.exists(model.logo_path):
        out.append(("warning", "The logo file is missing",
                    "Choose the logo again in Document ▸ Logo."))
    if not model.ordered_images(included_only=True):
        out.append(("warning", "No images are included",
                    "Tick at least one image under “Images” in the outline."))
    return out


__all__ = [
    "REPORT_ASSETS", "SECTION_COLORS", "SECTION_LABELS", "SECTION_TARGETS",
    "results_fingerprint", "analysed_count", "session_metadata", "collect_inputs",
    "build_model", "outline_order", "apply_order", "normalize", "add_custom_text",
    "remove_section", "merge_refresh", "prepare_render_model",
    "render_outputs", "only_image_model", "slug",
    "default_export_path", "copy_logo", "record_export", "save_report", "load_report",
    "problem_hints", "image_sections",
]
