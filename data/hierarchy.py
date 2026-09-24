"""
HIER-01: user-defined folder hierarchy and naming, one profile per
workspace, stored in ``<root>/workspace.json``.

No Qt imports. Internal slot names (``project``/``sample``/``lot``) never
change -- the rest of the data layer (``data/workspace.py``,
``data/session_io.py``, ``data/catalog.py``) keeps working against those
slots. Only labels, extra metadata fields, and naming templates are
user-facing, and they all live in ``HierarchyProfile``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from data.models import read_json, sanitize_name, write_json_atomic

SCHEMA_VERSION = 1

WORKSPACE_FILENAME = "workspace.json"

# Folder names that belong to a lot's own data (or app-level bookkeeping)
# and must never be mistaken for a child project/sample/lot/session when
# scanning a directory -- used both by ``data/workspace.py`` (list_sessions,
# rename_to_template) and ``data/catalog.py`` (rebuild via rglob).
RESERVED_LOT_SUBDIRS = {"images", "results", "thumbs", "exports",
                         "report_assets", "_history", ".trash"}


# ======================================================================
# models
# ======================================================================

@dataclass
class FieldDef:
    key: str
    label: str
    kind: str = "text"          # text | date | number | choice
    choices: List[str] = field(default_factory=list)
    required: bool = False

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "kind": self.kind,
                "choices": list(self.choices), "required": bool(self.required)}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "FieldDef":
        d = d or {}
        return cls(
            key=str(d.get("key", "")),
            label=str(d.get("label", d.get("key", ""))),
            kind=str(d.get("kind", "text")) or "text",
            choices=[str(c) for c in (d.get("choices") or [])],
            required=bool(d.get("required", False)),
        )


@dataclass
class LevelDef:
    key: str                    # "project" | "sample" | "lot"
    label: str                  # user-facing name, e.g. "Job #"
    id_label: str                # label of the identifying value
    fields: List[FieldDef] = field(default_factory=list)
    folder_template: str = "{id}"

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "id_label": self.id_label,
                "fields": [f.to_dict() for f in self.fields],
                "folder_template": self.folder_template}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "LevelDef":
        d = d or {}
        return cls(
            key=str(d.get("key", "")),
            label=str(d.get("label", d.get("key", ""))),
            id_label=str(d.get("id_label", d.get("label", ""))),
            fields=[FieldDef.from_dict(f) for f in (d.get("fields") or [])],
            folder_template=str(d.get("folder_template", "{id}")) or "{id}",
        )

    def field_keys(self) -> List[str]:
        return [f.key for f in self.fields]


@dataclass
class HierarchyProfile:
    name: str
    levels: List[LevelDef] = field(default_factory=list)
    images_location: str = "session"   # "lot" | "session"
    schema_version: int = SCHEMA_VERSION
    session_folder_template: str = "{date:%Y-%m-%d_%H%M%S}{label: - }"
    image_name_template: str = "{original}"
    export_name_template: str = "{project}_{sample}_{lot}_Grain_Report_{date:%Y%m%d}"
    report_title_template: str = "Grain Size Report — {sample_label} {sample}, {lot_label} {lot}"

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "levels": [lv.to_dict() for lv in self.levels],
            "images_location": self.images_location,
            "session_folder_template": self.session_folder_template,
            "image_name_template": self.image_name_template,
            "export_name_template": self.export_name_template,
            "report_title_template": self.report_title_template,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "HierarchyProfile":
        d = d or {}
        levels = [LevelDef.from_dict(lv) for lv in (d.get("levels") or [])]
        if not levels:
            levels = _default_levels()
        return cls(
            schema_version=int(d.get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
            name=str(d.get("name", "") or ""),
            levels=levels,
            images_location=str(d.get("images_location", "session") or "session"),
            session_folder_template=str(d.get("session_folder_template")
                                          or "{date:%Y-%m-%d_%H%M%S}{label: - }"),
            image_name_template=str(d.get("image_name_template") or "{original}"),
            export_name_template=str(d.get("export_name_template")
                                       or "{project}_{sample}_{lot}_Grain_Report_{date:%Y%m%d}"),
            report_title_template=str(d.get("report_title_template")
                                        or "Grain Size Report — {sample_label} {sample}, {lot_label} {lot}"),
        )

    def level(self, key: str) -> Optional[LevelDef]:
        for lv in self.levels:
            if lv.key == key:
                return lv
        return None

    def available_keys(self) -> List[str]:
        """Every key ``render_template``/``context_for_session`` can fill in
        for this profile -- used by ``validate_template``."""
        keys = ["project", "sample", "lot", "project_label", "sample_label",
                "lot_label", "operator", "date", "label", "instrument",
                "index", "original"]
        for lv in self.levels:
            for f in lv.fields:
                keys.append(f"{lv.key}_{f.key}")
        return keys


def _lv(key, label, id_label, fields, folder_template="{id}"):
    return LevelDef(key=key, label=label, id_label=id_label, fields=fields,
                     folder_template=folder_template)


def _default_levels():
    # legacy shape helper only used if a stored profile has no levels at all
    return [
        _lv("project", "Project", "Project name", []),
        _lv("sample", "Sample", "Sample ID", []),
        _lv("lot", "Lot", "Lot number", []),
    ]



# ======================================================================
# presets
# ======================================================================

def _job_part_lot_preset() -> HierarchyProfile:
    return HierarchyProfile(
        name="Job › Part › Lot",
        images_location="lot",
        levels=[
            _lv("project", "Job #", "Job number", [
                FieldDef("customer", "Customer"),
                FieldDef("po_number", "PO Number"),
            ]),
            _lv("sample", "Part Number", "Part number", [
                FieldDef("part_description", "Part Description"),
                FieldDef("material_alloy", "Material / Alloy"),
                FieldDef("drawing_revision", "Drawing Revision"),
            ]),
            _lv("lot", "Lot", "Lot number", [
                FieldDef("heat_number", "Heat Number"),
                FieldDef("supplier", "Supplier"),
                FieldDef("received_date", "Received Date", kind="date"),
                FieldDef("quantity", "Quantity", kind="number"),
            ]),
        ],
        session_folder_template="{date:%Y-%m-%d_%H%M%S}{label: - }",
        image_name_template="{original}",
        export_name_template="{project}_{sample}_{lot}_Grain_Report_{date:%Y%m%d}",
        report_title_template="Grain Size Report — Part {sample}, Lot {lot} (Job {project})",
    )


def _project_sample_lot_session_preset() -> HierarchyProfile:
    return HierarchyProfile(
        name="Project › Sample › Lot › Session",
        images_location="session",
        levels=[
            _lv("project", "Project", "Project name", []),
            _lv("sample", "Sample", "Sample ID", []),
            _lv("lot", "Lot", "Lot number", []),
        ],
        session_folder_template="{date:%Y-%m-%d_%H%M%S}{label: - }",
        image_name_template="{original}",
        export_name_template="{project}_{sample}_{lot}_Grain_Report_{date:%Y%m%d}",
        report_title_template="Grain Size Report — {sample_label} {sample}, {lot_label} {lot}",
    )


def _preset(name: str) -> HierarchyProfile:
    if name == "job_part_lot":
        return _job_part_lot_preset()
    if name == "project_sample_lot_session":
        return _project_sample_lot_session_preset()
    raise KeyError(f"Unknown preset: {name!r}")


class _Presets:
    """``PRESETS["job_part_lot"]`` returns a *fresh* profile instance every
    access (dataclasses hold mutable lists -- callers must not be able to
    corrupt the preset definition by mutating what they got back)."""

    _NAMES = ("job_part_lot", "project_sample_lot_session")

    def __getitem__(self, name: str) -> HierarchyProfile:
        return _preset(name)

    def __contains__(self, name: object) -> bool:
        return name in self._NAMES

    def __iter__(self):
        return iter(self._NAMES)

    def keys(self):
        return list(self._NAMES)

    def items(self):
        return [(n, _preset(n)) for n in self._NAMES]


PRESETS = _Presets()


# ======================================================================
# load / save
# ======================================================================

def _has_existing_project_folders(root: Path) -> bool:
    if not root.exists():
        return False
    for child in root.iterdir():
        if not child.is_dir() or child.name in (".trash",):
            continue
        if (child / "project.json").exists():
            return True
    return False


def load_profile(root: Union[str, Path]) -> HierarchyProfile:
    """Load ``<root>/workspace.json``. If it doesn't exist yet, decide and
    persist a default so behaviour stays stable on every future call:
    a brand-new workspace (no project folders, no workspace.json) gets the
    lab's actual workflow (``job_part_lot``); an existing workspace with
    project folders but no workspace.json gets the legacy preset
    (``project_sample_lot_session``) so nothing about it changes."""
    root = Path(root)
    ws_path = root / WORKSPACE_FILENAME
    if ws_path.exists():
        try:
            return HierarchyProfile.from_dict(read_json(ws_path))
        except (OSError, ValueError):
            pass  # fall through to default-and-write

    preset_name = "project_sample_lot_session" if _has_existing_project_folders(root) else "job_part_lot"
    profile = _preset(preset_name)
    try:
        save_profile(root, profile)
    except OSError:
        pass  # read-only/locked location: still usable in-memory this run
    return profile


def save_profile(root: Union[str, Path], profile: HierarchyProfile) -> None:
    write_json_atomic(Path(root) / WORKSPACE_FILENAME, profile.to_dict())


# ======================================================================
# template rendering
# ======================================================================

_FIELD_RE = re.compile(r"\{([^{}]*)\}")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PAD_RE = re.compile(r"^0\d*$")


def _parse_field(spec: str) -> Tuple[str, Optional[str]]:
    if ":" in spec:
        key, fmt = spec.split(":", 1)
        return key, fmt
    return spec, None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _format_value(value: Any, fmt: Optional[str]) -> str:
    if fmt is None:
        return _stringify(value)
    if fmt.startswith("%"):
        if isinstance(value, datetime):
            try:
                return value.strftime(fmt)
            except (ValueError, OSError):
                return ""
        return _stringify(value)
    if _PAD_RE.match(fmt):
        width = int(fmt)
        try:
            return str(int(value)).zfill(width)
        except (TypeError, ValueError):
            return _stringify(value)
    # "prefix" modifier, e.g. {label: - } -> " - value" only if non-empty
    sval = _stringify(value)
    return f"{fmt}{sval}" if sval else ""


def render_template(template: str, context: Optional[Dict[str, Any]] = None, *,
                     for_filename: bool = False) -> str:
    """A small, safe ``str.format``-like language: ``{key}``,
    ``{key:%Y%m%d}`` (date, only applied when the value is a ``datetime``),
    ``{key:02}`` (zero-padded int), ``{key: - }`` (literal prefix, emitted
    only when the value is non-empty). Missing/empty keys render as ``""``.
    Never raises, and never resolves anything but a plain identifier -- no
    attribute (``{a.b}``) or subscript (``{a[0]}``) access, so a malicious
    or malformed template can't reach outside ``context``."""
    template = template or ""
    context = context or {}

    def repl(m: "re.Match[str]") -> str:
        spec = m.group(1)
        try:
            key, fmt = _parse_field(spec)
        except Exception:
            return ""
        key = key.strip()
        if not _KEY_RE.match(key):
            return ""
        if key not in context:
            return ""
        value = context.get(key)
        if value is None or value == "":
            return ""
        try:
            return _format_value(value, fmt)
        except Exception:
            return ""

    try:
        rendered = _FIELD_RE.sub(repl, template)
    except Exception:
        rendered = ""

    if for_filename:
        # collapse only *runs of the same* separator char (e.g. "__" -> "_",
        # "  " -> " ") left behind by missing/empty template fields -- a
        # deliberate mixed separator the template author wrote, like the
        # " - " in "{id} - Renamed", is left alone.
        rendered = re.sub(r"([ _\-])\1+", r"\1", rendered).strip()
        rendered = rendered.rstrip("_- .")
        if not rendered:
            return ""
        try:
            rendered = sanitize_name(rendered)
        except ValueError:
            return ""
    return rendered


def validate_template(template: str, available_keys: Sequence[str]) -> List[str]:
    """List of human-readable problems with ``template`` -- unknown/invalid
    tokens, bad date formats, unbalanced braces. Empty list means clean.
    Never raises."""
    problems: List[str] = []
    template = template or ""
    if not template:
        return problems
    available = set(available_keys or [])
    try:
        for m in _FIELD_RE.finditer(template):
            spec = m.group(1)
            key, fmt = _parse_field(spec)
            key = key.strip()
            if not _KEY_RE.match(key):
                problems.append(f"Invalid token: {{{spec}}}")
                continue
            if key not in available:
                problems.append(f"Unknown key: {key}")
            if fmt and fmt.startswith("%"):
                try:
                    datetime(2000, 1, 2, 3, 4, 5).strftime(fmt)
                except (ValueError, OSError):
                    problems.append(f"Invalid date format: {fmt}")
        stripped = _FIELD_RE.sub("", template)
        if "{" in stripped or "}" in stripped:
            problems.append("Unbalanced braces")
    except Exception as exc:  # pragma: no cover - defensive only
        problems.append(f"Could not validate template: {exc}")
    return problems


# ======================================================================
# context building
# ======================================================================

def _read_json_safe(path: Path) -> dict:
    try:
        return read_json(path) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def context_for_session(loaded_session_or_path: Union[str, Path, Any],
                         profile: Optional[HierarchyProfile] = None,
                         *, image: Optional[Any] = None,
                         index: Optional[int] = None) -> Dict[str, Any]:
    """Build the template context for a session (or, with
    ``images_location == "lot"``, a lot-as-session): ``project``,
    ``sample``, ``lot`` (id values), ``project_label``/``sample_label``/
    ``lot_label`` (level labels), every level field as
    ``"<levelkey>_<fieldkey>"``, plus ``operator``, ``date`` (a
    ``datetime``), ``label``, ``instrument``, and (when ``image`` is given)
    ``index``/``original`` for image naming.

    ``loaded_session_or_path`` may be a path to the session/lot directory,
    or any object with a ``.path`` attribute (e.g.
    ``data.session_io.LoadedSession``) and optionally a ``.manifest`` with
    ``.to_dict()``.
    """
    path = getattr(loaded_session_or_path, "path", loaded_session_or_path)
    path = Path(path)

    manifest = {}
    manifest_path = path / "manifest.json"
    if manifest_path.exists():
        manifest = _read_json_safe(manifest_path)
    else:
        m = getattr(loaded_session_or_path, "manifest", None)
        if m is not None:
            manifest = m.to_dict() if hasattr(m, "to_dict") else dict(m or {})

    if (path / "lot.json").exists():
        lot_dir = path
    else:
        lot_dir = path.parent
    sample_dir = lot_dir.parent
    project_dir = sample_dir.parent

    project_meta = _read_json_safe(project_dir / "project.json")
    sample_meta = _read_json_safe(sample_dir / "sample.json")
    lot_meta = _read_json_safe(lot_dir / "lot.json")

    if profile is None:
        try:
            profile = load_profile(project_dir.parent)
        except Exception:
            profile = _project_sample_lot_session_preset()

    ctx: Dict[str, Any] = {
        "project": manifest.get("project", project_meta.get("name", "")),
        "sample": manifest.get("sample_id", sample_meta.get("sample_id", "")),
        "lot": manifest.get("lot_number", lot_meta.get("lot_number", "")),
        "operator": manifest.get("operator", ""),
        "label": manifest.get("label", ""),
        "instrument": manifest.get("instrument", ""),
    }

    level_meta = {"project": project_meta, "sample": sample_meta, "lot": lot_meta}
    for lv in profile.levels:
        ctx[f"{lv.key}_label"] = lv.label
        meta = level_meta.get(lv.key, {})
        for f in lv.fields:
            ctx[f"{lv.key}_{f.key}"] = meta.get(f.key, "")

    created_utc = manifest.get("created_utc")
    if created_utc:
        try:
            ctx["date"] = datetime.strptime(created_utc, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            ctx["date"] = datetime.now()
    else:
        ctx["date"] = datetime.now()

    if image is not None:
        original = getattr(image, "original_name", None) or getattr(image, "filename", None) or ""
        ctx["original"] = Path(str(original)).stem
    if index is not None:
        ctx["index"] = index

    return ctx


__all__ = [
    "FieldDef", "LevelDef", "HierarchyProfile", "PRESETS",
    "load_profile", "save_profile", "render_template", "validate_template",
    "context_for_session", "WORKSPACE_FILENAME", "RESERVED_LOT_SUBDIRS",
]
