"""
HIER-01 — the workspace's hierarchy profile as the UI sees it (no widgets).

Internal slots stay ``project`` / ``sample`` / ``lot`` (+ ``session``); every
user-facing word comes from the profile: level labels ("Job #", "Part
Number", "Lot"), the identifying value's label ("Job number"), the level's
metadata fields, and whether a Session level exists at all
(``images_location == "lot"`` → the lot folder *is* the record).

Legacy workspaces (a profile whose levels carry no fields) keep the fixed
v3.0 fields (customer / material / grade / supplier …) so nothing changes
for them.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from data.hierarchy import (
    FieldDef, HierarchyProfile, PRESETS, context_for_session, render_template,
)
from data.models import read_json, write_json_atomic

LEVELS = ("project", "sample", "lot")
ID_KEYS = {"project": "name", "sample": "sample_id", "lot": "lot_number"}
META_FILES = {"project": "project.json", "sample": "sample.json", "lot": "lot.json",
              "session": "manifest.json"}
KIND_ICON = {"workspace": "database", "project": "projects", "sample": "sample",
             "lot": "tag", "session": "images", "image": "image"}

# v3.0 fixed fields, used when the profile defines none (legacy preset)
LEGACY_FIELDS: Dict[str, List[FieldDef]] = {
    "project": [FieldDef("customer", "Customer / programme"),
                FieldDef("description", "Description", kind="multiline")],
    "sample": [FieldDef("material", "Material"), FieldDef("alloy_grade", "Grade"),
               FieldDef("heat_treatment", "Heat treatment"),
               FieldDef("description", "Description", kind="multiline")],
    "lot": [FieldDef("supplier", "Supplier"), FieldDef("received_date", "Received date",
                                                       kind="date"),
            FieldDef("notes", "Notes", kind="multiline")],
}
SESSION_FIELDS: List[FieldDef] = [
    FieldDef("label", "Session label"), FieldDef("operator", "Operator"),
    FieldDef("instrument", "Instrument"), FieldDef("magnification", "Magnification"),
    FieldDef("notes", "Notes", kind="multiline"),
]
FIELD_KINDS = ("text", "number", "date", "choice")
KIND_NAMES = {"text": "Text", "number": "Number", "date": "Date", "choice": "Choice"}

# acquisition fields read from SEM metadata (INN-05) → manifest keys
SEM_META_KEYS = ("instrument", "accelerating_voltage_kv", "working_distance_mm", "magnification")


def legacy_profile() -> HierarchyProfile:
    return PRESETS["project_sample_lot_session"]


def is_legacy_fields(profile: HierarchyProfile) -> bool:
    return not any(lv.fields for lv in profile.levels)


def lot_mode(profile: Optional[HierarchyProfile]) -> bool:
    return profile is not None and profile.images_location == "lot"


def kind_label(profile: Optional[HierarchyProfile], kind: str) -> str:
    if kind == "workspace":
        return "Workspace"
    if kind == "session":
        return "Session"
    lv = profile.level(kind) if profile is not None else None
    if lv is not None and lv.label.strip():
        return lv.label.strip()
    return {"project": "Project", "sample": "Sample", "lot": "Lot"}.get(kind, kind.title())


def id_label(profile: Optional[HierarchyProfile], kind: str) -> str:
    lv = profile.level(kind) if profile is not None else None
    if lv is not None and lv.id_label.strip():
        return lv.id_label.strip()
    return {"project": "Project name", "sample": "Sample ID", "lot": "Lot number",
            "session": "Session label"}.get(kind, "Name")


def record_word(profile: Optional[HierarchyProfile]) -> str:
    """What one analysis record is called in running text: the lot label
    when images live in the lot ("lot", "Work Order"), else "session"."""
    if not lot_mode(profile):
        return "session"
    lab = kind_label(profile, "lot")
    return lab.lower() if (" " not in lab and lab[:1].isupper() and lab[1:].islower()) else lab


def cap_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def level_chain(profile: Optional[HierarchyProfile]) -> str:
    """"Job # › Part Number › Lot" (+ " › Session" in session mode)."""
    parts = [kind_label(profile, k) for k in LEVELS]
    if not lot_mode(profile):
        parts.append("Session")
    return " › ".join(parts)


def plural(label: str) -> str:
    s = label.strip()
    if s.endswith(" #"):
        s = s[:-2]
    if not s:
        return s
    if s.endswith("s"):
        return s
    if s.endswith("y") and len(s) > 1 and s[-2].lower() not in "aeiou":
        return s[:-1] + "ies"
    return s + "s"


def count_text(n: int, label: str) -> str:
    return f"{n} {(label.rstrip(' #') or label) if n == 1 else plural(label)}"


def child_kind(profile: Optional[HierarchyProfile], kind: str) -> Optional[str]:
    nxt = {"workspace": "project", "project": "sample", "sample": "lot", "lot": "session"}
    c = nxt.get(kind)
    if c == "session" and lot_mode(profile):
        return None
    return c


def level_fields(profile: Optional[HierarchyProfile], kind: str) -> List[FieldDef]:
    if kind == "session":
        return list(SESSION_FIELDS)
    if profile is None or is_legacy_fields(profile):
        return list(LEGACY_FIELDS.get(kind, []))
    lv = profile.level(kind)
    return list(lv.fields) if lv is not None else []


def id_value(kind: str, meta: dict, path: Optional[Path] = None) -> str:
    key = ID_KEYS.get(kind)
    v = (meta or {}).get(key, "") if key else ""
    return str(v or (path.name if path is not None else ""))


def session_title(created_local: str, fallback: str) -> str:
    try:
        dt = datetime.strptime(created_local, "%Y-%m-%d %H:%M:%S")
        return "Session " + dt.strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return fallback


def node_caption(profile: Optional[HierarchyProfile], kind: str, meta: dict,
                 path: Path) -> str:
    """Tree / card title: ids for the upper levels, "<Lot label> <id>" for
    lots (as v3.0 showed "Lot 2026-0917-B")."""
    if kind == "workspace":
        return "Workspace"
    if kind == "session":
        return (meta or {}).get("label") or session_title((meta or {}).get("created_local", ""),
                                                          path.name)
    v = id_value(kind, meta, path)
    if kind == "lot":
        return f"{kind_label(profile, 'lot')} {v}"
    return v


def crumb_caption(profile: Optional[HierarchyProfile], kind: str, meta: dict,
                  path: Path) -> str:
    """Breadcrumb segment: "<level label> <id>" for every level."""
    if kind in LEVELS:
        return f"{kind_label(profile, kind)} {id_value(kind, meta, path)}"
    return node_caption(profile, kind, meta, path)


def read_meta(kind: str, path: Path) -> dict:
    mf = META_FILES.get(kind)
    try:
        return read_json(Path(path) / mf) if mf and (Path(path) / mf).exists() else {}
    except Exception:
        return {}


def is_lot_record(path: Path) -> bool:
    """True when ``path`` is a lot folder used as the session (lot mode)."""
    return (Path(path) / "lot.json").exists()


# ----------------------------------------------------------------------
# metadata writes that keep profile field values
# ----------------------------------------------------------------------

def update_level_meta(ws, kind: str, path: Path, values: dict) -> None:
    """Write metadata for a project/sample/lot folder.

    ``Workspace.update_*_meta`` only keeps the fixed dataclass fields and
    drops profile field values (heat number, PO number …) from the file, so
    the extra keys are merged back here (atomic write)."""
    path = Path(path)
    mf = path / META_FILES[kind]
    before = read_meta(kind, path)
    fixed = {k: v for k, v in values.items() if k != ID_KEYS[kind]}
    if kind == "project":
        ws.update_project_meta(path, **fixed)
    elif kind == "sample":
        ws.update_sample_meta(path, **fixed)
    else:
        ws.update_lot_meta(path, **fixed)
    after = read_meta(kind, path)
    for k, v in before.items():
        after.setdefault(k, v)
    for k, v in values.items():
        if k != ID_KEYS[kind]:
            after[k] = v
    write_json_atomic(mf, after)


def rename_level(ws, kind: str, path: Path, new_id: str) -> Path:
    """Rename a level (folder + id) keeping every profile field value."""
    path = Path(path)
    before = read_meta(kind, path)
    if kind == "project":
        newp = ws.rename_project(path, new_id)
    elif kind == "sample":
        newp = ws.rename_sample(path.parent, path, new_id)
    else:
        newp = ws.rename_lot(path.parent.parent, path.parent, path, new_id)
    after = read_meta(kind, newp)
    changed = False
    for k, v in before.items():
        if k not in after:
            after[k] = v
            changed = True
    if changed:
        write_json_atomic(Path(newp) / META_FILES[kind], after)
    return Path(newp)


# ----------------------------------------------------------------------
# template contexts
# ----------------------------------------------------------------------

def sample_context(profile: HierarchyProfile) -> dict:
    """Plausible values for template previews when nothing is selected."""
    ctx = {"project": "24-117", "sample": "7718-A", "lot": "L-44A", "operator": "J. Samaniego",
           "label": "Transverse", "instrument": "Zeiss Sigma 300", "date": datetime.now(),
           "index": 1, "original": "SEM_0001"}
    samples = {"customer": "Acme Aerospace", "po_number": "PO-5521",
               "part_description": "Turbine disk forging", "material_alloy": "Alloy 718",
               "drawing_revision": "C", "heat_number": "HT-90211", "supplier": "Special Metals",
               "received_date": "2026-09-17", "quantity": "12"}
    for lv in profile.levels:
        ctx[f"{lv.key}_label"] = lv.label
        for f in lv.fields:
            ctx[f"{lv.key}_{f.key}"] = samples.get(f.key, f.label)
    return ctx


def context_for_path(path: Optional[Path], profile: HierarchyProfile) -> dict:
    """Template context for a lot / session / sample / project folder
    (falls back to :func:`sample_context` values for missing levels)."""
    if path is None:
        return sample_context(profile)
    path = Path(path)
    try:
        if (path / "lot.json").exists() or (path / "manifest.json").exists():
            return context_for_session(path, profile)
    except Exception:
        pass
    ctx = sample_context(profile)
    for kind in ("sample", "project"):
        if (path / META_FILES[kind]).exists():
            chain = [path] if kind == "project" else [path.parent, path]
            for k, p in zip(("project", "sample")[:len(chain)], chain):
                m = read_meta(k, p)
                ctx[k] = id_value(k, m, p)
                lv = profile.level(k)
                for f in (lv.fields if lv else []):
                    ctx[f"{k}_{f.key}"] = m.get(f.key, "")
            ctx["lot"] = ""
            break
    return ctx


def hierarchy_rows(profile: HierarchyProfile, ctx: dict) -> List[dict]:
    """``ReportModel.hierarchy``: ordered [{key, label, value}]."""
    return [{"key": lv.key, "label": lv.label, "value": str(ctx.get(lv.key, "") or "")}
            for lv in profile.levels]


def export_basename(profile: HierarchyProfile, ctx: dict) -> str:
    return render_template(profile.export_name_template, ctx, for_filename=True)


def report_title(profile: HierarchyProfile, ctx: dict) -> str:
    return render_template(profile.report_title_template, ctx).strip()


def token_list(profile: HierarchyProfile, kind: str = "general") -> List[tuple]:
    """(token, human label) chips for a template editor.

    ``kind="folder:<level>"`` gives the folder-name tokens of one level
    (``{id}`` + that level's field keys); otherwise the report / export /
    image tokens."""
    if kind.startswith("folder:"):
        lv = profile.level(kind.split(":", 1)[1])
        out = [("{id}", lv.id_label if lv else "Identifier")]
        for f in (lv.fields if lv else []):
            out.append((f"{{{f.key}}}", f.label))
        return out
    out = []
    for lv in profile.levels:
        out.append((f"{{{lv.key}}}", lv.label))
    for lv in profile.levels:
        out.append((f"{{{lv.key}_label}}", f"“{lv.label}” (label)"))
    out += [("{date:%Y%m%d}", "Date 20260924"), ("{date:%Y-%m-%d}", "Date 2026-09-24"),
            ("{operator}", "Operator"), ("{instrument}", "Instrument")]
    if kind == "image":
        out += [("{original}", "Original file name"), ("{index:02}", "Image number 01")]
    for lv in profile.levels:
        for f in lv.fields:
            out.append((f"{{{lv.key}_{f.key}}}", f"{lv.label} · {f.label}"))
    return out


def folder_keys(profile: HierarchyProfile, level_key: str) -> List[str]:
    lv = profile.level(level_key)
    return ["id"] + ([f.key for f in lv.fields] if lv else [])


def snake(text: str) -> str:
    import re
    s = re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")
    if not s:
        s = "field"
    if s[0].isdigit():
        s = "f_" + s
    return s


__all__ = [
    "record_word", "cap_first", "level_chain",
    "LEVELS", "ID_KEYS", "META_FILES", "KIND_ICON", "LEGACY_FIELDS", "SESSION_FIELDS",
    "FIELD_KINDS", "KIND_NAMES", "SEM_META_KEYS", "legacy_profile", "is_legacy_fields",
    "lot_mode", "kind_label", "id_label", "plural", "count_text", "child_kind",
    "level_fields", "id_value", "session_title", "node_caption", "crumb_caption", "read_meta",
    "is_lot_record", "update_level_meta", "rename_level", "sample_context", "context_for_path",
    "hierarchy_rows", "export_basename", "report_title", "token_list", "folder_keys", "snake",
]
