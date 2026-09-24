"""
SQLite search index over the workspace (D-04): rebuildable cache only,
never the source of truth. Files on disk (manifest.json) always win; if the
database is locked, missing, or corrupt, every read here falls back to a
manifest scan so the browser keeps working on a flaky/network drive.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from core.metrics import FieldResult
from data.hierarchy import load_profile
from data.models import read_json

_DB_FILENAME = "catalog.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    path TEXT PRIMARY KEY,
    project TEXT,
    sample_id TEXT,
    lot_number TEXT,
    session_id TEXT,
    label TEXT,
    operator TEXT,
    created_utc TEXT,
    notes TEXT,
    tags TEXT,
    image_count INTEGER,
    grain_count INTEGER,
    mean_area_um2 REAL,
    mean_diameter_um REAL,
    profile_json TEXT
);
"""

# INN-02: one row per lot directory -- a rebuildable cache of the
# conformity verdict (data.specs.evaluate) for tree/browser badges. A lot
# with no spec attached is simply never written here (see
# ``compute_lot_verdict``/``index_lot_verdict``), so an unspecced workspace
# never gains an "overall" column value anywhere.
_LOT_VERDICT_SCHEMA = """
CREATE TABLE IF NOT EXISTS lot_verdicts (
    path TEXT PRIMARY KEY,
    project TEXT,
    sample_id TEXT,
    lot_number TEXT,
    overall TEXT,
    verdict_json TEXT
);
"""

# HIER-01: columns added after the original schema. SQLite has no "ADD
# COLUMN IF NOT EXISTS"; adding is attempted every connection and the
# "duplicate column" failure (already there, from an earlier run) is
# swallowed.
_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN profile_json TEXT",
]

# Keys that live in project.json/sample.json/lot.json for bookkeeping (id,
# schema version, ...) rather than as a profile field value -- never
# surfaced as a searchable "<levelkey>_<fieldkey>" entry.
_LEVEL_META_SKIP = {
    "project": {"schema_version", "path", "name", "created_utc"},
    "sample": {"schema_version", "path", "sample_id", "created_utc"},
    "lot": {"schema_version", "path", "lot_number", "created_utc", "spec_limits"},
}
_LEVEL_META_FILES = {"project": "project.json", "sample": "sample.json", "lot": "lot.json"}


def _manifest_paths(root: Path):
    if not root.exists():
        return
    for manifest_path in root.rglob("manifest.json"):
        if ".trash" in manifest_path.parts:
            continue
        yield manifest_path.parent


def _level_meta_dirs(session_dir: Path) -> Dict[str, Path]:
    """A session's ancestor level directories -- generic, no profile
    lookup needed: a lot-as-session dir carries ``lot.json`` itself
    (HIER-01, ``images_location == "lot"``); a legacy timestamped session
    dir does not, so its parent is the lot."""
    lot_dir = session_dir if (session_dir / "lot.json").exists() else session_dir.parent
    sample_dir = lot_dir.parent
    project_dir = sample_dir.parent
    return {"project": project_dir, "sample": sample_dir, "lot": lot_dir}


def _extra_profile_fields(root: Path, session_dir: Path) -> dict:
    """Level field values (heat number, part description, customer, ...)
    and level labels, so ``Catalog.search`` can match on them even though
    they aren't fixed columns (HIER-01)."""
    out: dict = {}
    dirs = _level_meta_dirs(session_dir)
    for level_key, dirpath in dirs.items():
        mp = dirpath / _LEVEL_META_FILES[level_key]
        d = {}
        try:
            if mp.exists():
                d = read_json(mp)
        except (OSError, ValueError):
            d = {}
        skip = _LEVEL_META_SKIP[level_key]
        for k, v in d.items():
            if k in skip or not isinstance(v, (str, int, float)):
                continue
            out[f"{level_key}_{k}"] = v
    try:
        profile = load_profile(root)
        for lv in profile.levels:
            out[f"{lv.key}_label"] = lv.label
    except Exception:
        pass
    return out


def _row_from_manifest(session_dir: Path, root: Optional[Path] = None) -> Optional[dict]:
    manifest_path = session_dir / "manifest.json"
    try:
        m = read_json(manifest_path)
    except (OSError, ValueError):
        return None
    images = m.get("images", []) or []
    grain_counts = [img.get("grain_count", 0) for img in images if img.get("has_result")]
    summary_means = []
    diam_means = []
    for img in images:
        if not img.get("has_result"):
            continue
        stem = Path(img.get("filename", "")).stem
        summary_path = session_dir / "results" / f"{stem}.summary.json"
        if summary_path.exists():
            try:
                s = read_json(summary_path)
                summary_means.append(s.get("mean_area_um2", 0.0))
                diam_means.append(s.get("mean_diameter_um", 0.0))
            except (OSError, ValueError):
                pass
    extra = _extra_profile_fields(root if root is not None else session_dir.parent.parent.parent,
                                   session_dir)
    return {
        "path": str(session_dir),
        "project": m.get("project", ""),
        "sample_id": m.get("sample_id", ""),
        "lot_number": m.get("lot_number", ""),
        "session_id": m.get("session_id", ""),
        "label": m.get("label", ""),
        "operator": m.get("operator", ""),
        "created_utc": m.get("created_utc", ""),
        "notes": m.get("notes", ""),
        "tags": json.dumps(m.get("tags", []) or []),
        "image_count": len(images),
        "grain_count": int(sum(grain_counts)),
        "mean_area_um2": float(sum(summary_means) / len(summary_means)) if summary_means else 0.0,
        "mean_diameter_um": float(sum(diam_means) / len(diam_means)) if diam_means else 0.0,
        "profile_json": json.dumps(extra),
    }


# ----------------------------------------------------------------------
# INN-27: per-lot field collection (always read from the manifests on
# disk -- the source of truth -- never from the SQLite cache).
# ----------------------------------------------------------------------

def _session_dirs_of_lot(lot_path: Path) -> List[Path]:
    """Session dirs of a lot: the lot dir itself when it carries a
    manifest (HIER-01 images_location == "lot"), plus every direct child
    run folder with a manifest."""
    out: List[Path] = []
    if (lot_path / "manifest.json").exists():
        out.append(lot_path)
    try:
        children = sorted(p for p in lot_path.iterdir() if p.is_dir())
    except OSError:
        children = []
    out.extend(p for p in children if (p / "manifest.json").exists())
    return out


# A refilter hook: ``(session_dir, manifest_dict, image_dict) -> AnalysisResult``
# (or None).  Supplied by the UI (``ui.pages.lot_results.refilter_saved_field``)
# so the data layer never imports the UI / image-processing adapters.
Refilter = Callable[[Path, dict, dict], Any]

_NUMERIC_FILTERS = ("min_area_px", "max_area_px", "max_aspect_ratio", "min_circularity")


def _filter_state(manifest: dict, img: dict):
    """Effective post-filter options + manual grain removals of one image,
    resolved like the app does (per-image override > session filters >
    pre-DATA-09 ``detection_params["post_filters"]`` > default: border
    exclusion when a scan area is set)."""
    pf = (manifest.get("detection_params") or {}).get("post_filters") or {}
    legacy = (pf.get("images") or {}).get(img.get("filename", "")) or {}
    opts = (img.get("filters_override") or legacy.get("options")
            or manifest.get("filters") or pf.get("options"))
    if not isinstance(opts, dict):
        opts = {"exclude_border": bool(manifest.get("scan_rect"))}
    manual = img.get("manual_excluded") or legacy.get("manual") or []
    return opts, {int(i) for i in manual}


def edits_not_in_saved_result(summary: dict, grains: List[dict], opts: dict,
                              manual) -> bool:
    """True when an image's saved summary/grains predate its grain filters
    or manual grain removals.  The app normally re-saves the filtered
    result, but legacy sessions, manifest-only edits and interrupted saves
    can leave it raw.  Cheap: only reads what is already loaded."""
    kept = {int(g.get("grain_id", -1)) for g in grains}
    if set(manual) & kept:
        return True
    lim = {k: float(opts.get(k) or 0) for k in _NUMERIC_FILTERS}
    for g in grains:
        area = float(g.get("area_px", 0) or 0)
        if (lim["min_area_px"] and area < lim["min_area_px"]) or \
           (lim["max_area_px"] and area > lim["max_area_px"]) or \
           (lim["max_aspect_ratio"]
                and float(g.get("aspect_ratio", 0) or 0) > lim["max_aspect_ratio"]) or \
           (lim["min_circularity"]
                and float(g.get("circularity", 1) or 0) < lim["min_circularity"]):
            return True
    active = bool(manual) or any(bool(opts.get(k)) for k in
                                 ("exclude_border", "exclude_touching_invalid",
                                  "exclude_low_contrast")) or any(lim.values())
    if not active:
        return False
    astm = summary.get("astm") if isinstance(summary.get("astm"), dict) else {}
    # core.postfilter stamps every filtered ASTM evaluation with this note
    return not any("Post-filter" in str(n) for n in (astm.get("notes") or []))


def _field_from_saved(session_dir: Path, session_id: str, img: dict,
                      manifest: Optional[dict] = None,
                      refilter: Optional[Refilter] = None) -> FieldResult:
    stem = Path(img.get("filename", "")).stem
    summary: dict = {}
    try:
        summary = read_json(session_dir / "results" / f"{stem}.summary.json")
    except (OSError, ValueError):
        pass
    grains: List[dict] = []
    try:
        grains = list(read_json(session_dir / "results" / f"{stem}.grains.json")
                      .get("grains", []) or [])
    except (OSError, ValueError, AttributeError):
        grains = []
    astm = summary.get("astm") if isinstance(summary.get("astm"), dict) else {}
    g = summary.get("astm_g")
    if g is None:
        g = astm.get("G_primary")
    calibrated = bool(summary.get("has_calibration")) and float(summary.get("px_per_um") or 0) > 0
    ecds: List[float] = []
    if calibrated:
        try:
            ecds = [float(x.get("equivalent_diameter_um", 0.0)) for x in grains]
        except (AttributeError, TypeError, ValueError):
            ecds = []
    mean_d = float(summary.get("mean_diameter_um") or 0.0)
    thumb = session_dir / "thumbs" / f"{stem}.jpg"
    field = FieldResult(
        field_id=f"{session_id}/{img.get('filename', '')}",
        G=float(g) if g is not None else None,
        method=str(astm.get("primary_method", "") or ""),
        ecd_mean_um=mean_d if calibrated and mean_d > 0 else None,
        grain_count=int(summary.get("grain_count", img.get("grain_count", 0)) or 0),
        valid_area_pct=100.0 - float(summary.get("invalid_area_pct") or 0.0),
        included=bool(img.get("included", True)),
        exclusion_reason=img.get("exclusion_reason"),
        session_id=session_id,
        session_path=str(session_dir),
        image_name=img.get("filename", ""),
        thumb_path=str(thumb) if thumb.exists() else "",
        ecds_um=ecds,
    )
    if refilter is not None and manifest is not None:
        opts, manual = _filter_state(manifest, img)
        if edits_not_in_saved_result(summary, grains, opts, manual):
            try:
                res = refilter(session_dir, manifest, img)
            except Exception:
                res = None
            if res is not None:
                edited = FieldResult.from_analysis(res, field_id=field.field_id)
                for k in ("G", "method", "ecd_mean_um", "grain_count", "valid_area_pct",
                          "ecds_um"):
                    setattr(field, k, getattr(edited, k))
    return field


def fields_for_lot(lot_path: Union[str, Path],
                   refilter: Optional[Refilter] = None) -> List[FieldResult]:
    """One ``FieldResult`` per analysed image (latest saved result) in
    every session of the lot at ``lot_path``, excluded fields included
    (flagged ``included=False``) so the UI can list them.  Feed the list
    to ``core.metrics.sample_statistics``.

    Grain filters / manual grain removals: the app saves the *filtered*
    result, so the saved G normally already honours them.  When a saved
    result predates the image's edits (``edits_not_in_saved_result``) and
    ``refilter`` is given, that image's G / ECD come from it instead --
    the same numbers the Review page and the exports use."""
    out: List[FieldResult] = []
    for sdir in _session_dirs_of_lot(Path(lot_path)):
        try:
            m = read_json(sdir / "manifest.json")
        except (OSError, ValueError):
            continue
        sid = m.get("session_id") or sdir.name
        for img in m.get("images", []) or []:
            if isinstance(img, dict) and img.get("has_result"):
                out.append(_field_from_saved(sdir, sid, img, m, refilter))
    return out


# ----------------------------------------------------------------------
# INN-02: per-lot conformity verdict (spec limits). Always re-derivable
# from project.json + the manifests on disk -- the ``lot_verdicts`` table
# (below) is only a rebuildable cache for tree/browser badges, same as
# ``sessions`` is for search (D-04). A lot whose project defines no spec
# is never written here, so an unspecced workspace has no verdict rows at
# all -- no badge, nothing to fall back on but "no_spec".
# ----------------------------------------------------------------------

def _project_meta_for_lot(lot_path: Path) -> dict:
    p = lot_path.parent.parent / "project.json"
    try:
        return read_json(p) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _sample_id_for_lot(lot_path: Path) -> str:
    p = lot_path.parent / "sample.json"
    try:
        d = read_json(p) if p.exists() else {}
    except (OSError, ValueError):
        d = {}
    return str(d.get("sample_id") or lot_path.parent.name)


def _lot_number_for(lot_path: Path) -> str:
    p = lot_path / "lot.json"
    try:
        d = read_json(p) if p.exists() else {}
    except (OSError, ValueError):
        d = {}
    return str(d.get("lot_number") or lot_path.name)


def compute_lot_verdict(lot_path: Union[str, Path]) -> dict:
    """Live (uncached) INN-02 conformity verdict for the lot at
    ``lot_path``: resolves the applicable spec out of ``project.json``
    (sample-level override wins -- ``data.specs.select_spec``), computes
    lot statistics (``fields_for_lot`` + ``core.metrics.sample_statistics``)
    only if a spec applies, and evaluates. Always returns a dict --
    ``data.specs.Verdict(overall="no_spec").to_dict()`` when the project/
    sample has no spec attached, never an error, never a badge."""
    from core.metrics import sample_statistics as _sample_statistics
    from data.specs import evaluate, select_spec, specs_from_project_dict

    lot_path = Path(lot_path)
    specs = specs_from_project_dict(_project_meta_for_lot(lot_path))
    spec = select_spec(specs, _sample_id_for_lot(lot_path)) if specs else None
    stats = _sample_statistics(fields_for_lot(lot_path)) if spec is not None else None
    return evaluate(spec, stats).to_dict()


def _lot_dirs(root: Path) -> List[Path]:
    """Every distinct lot directory under ``root``, derived from every
    session's manifest (HIER-01-aware via ``_level_meta_dirs``)."""
    out: List[Path] = []
    seen = set()
    for session_dir in _manifest_paths(root):
        lot_dir = _level_meta_dirs(session_dir)["lot"]
        if lot_dir not in seen:
            seen.add(lot_dir)
            out.append(lot_dir)
    return out


_COLUMNS = ["path", "project", "sample_id", "lot_number", "session_id", "label",
            "operator", "created_utc", "notes", "tags", "image_count",
            "grain_count", "mean_area_um2", "mean_diameter_um", "profile_json"]

_TEXT_COLUMNS = ["project", "sample_id", "lot_number", "session_id", "label",
                  "operator", "notes", "tags", "profile_json"]


class Catalog:
    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)
        self.db_path = self.root / _DB_FILENAME

    @contextlib.contextmanager
    def _connection(self):
        """A connection that is always closed (not just committed) on
        exit, so Windows never holds the db file open after a call
        returns — important since rebuild() may need to delete it."""
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(_SCHEMA)
            conn.execute(_LOT_VERDICT_SCHEMA)
            for migration in _MIGRATIONS:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column already present from an earlier run
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------

    def index_session(self, session_path: Union[str, Path]) -> bool:
        row = _row_from_manifest(Path(session_path), self.root)
        if row is None:
            return False
        try:
            with self._connection() as conn:
                placeholders = ", ".join("?" for _ in _COLUMNS)
                cols = ", ".join(_COLUMNS)
                conn.execute(
                    f"INSERT INTO sessions ({cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT(path) DO UPDATE SET " +
                    ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "path"),
                    [row[c] for c in _COLUMNS],
                )
            return True
        except sqlite3.Error:
            return False

    def remove(self, session_path: Union[str, Path]) -> bool:
        try:
            with self._connection() as conn:
                conn.execute("DELETE FROM sessions WHERE path = ?", (str(session_path),))
            return True
        except sqlite3.Error:
            return False

    def search(self, text: str = "", filters: Optional[Dict[str, Any]] = None) -> List[dict]:
        filters = filters or {}
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                clauses = []
                params: List[Any] = []
                if text:
                    like = f"%{text}%"
                    clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in _TEXT_COLUMNS) + ")")
                    params.extend([like] * len(_TEXT_COLUMNS))
                for key, value in filters.items():
                    if key not in _COLUMNS:
                        continue
                    clauses.append(f"{key} = ?")
                    params.append(value)
                where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM sessions {where} ORDER BY created_utc DESC", params
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error:
            return self._manifest_scan_search(text, filters)

    def _manifest_scan_search(self, text: str, filters: Dict[str, Any]) -> List[dict]:
        text_l = text.lower()
        out = []
        for session_dir in _manifest_paths(self.root):
            row = _row_from_manifest(session_dir, self.root)
            if row is None:
                continue
            if filters and any(row.get(k) != v for k, v in filters.items() if k in _COLUMNS):
                continue
            if text and not any(text_l in str(row.get(c, "")).lower() for c in _TEXT_COLUMNS):
                continue
            out.append(row)
        out.sort(key=lambda r: r.get("created_utc", ""), reverse=True)
        return out

    def fields_for_lot(self, lot_id: Union[str, Path],
                       refilter: Optional[Refilter] = None) -> List[FieldResult]:
        """Latest per-image results of a lot (INN-27). ``lot_id`` is the lot
        folder (absolute, or relative to the workspace root) or a lot
        number, resolved through the index (manifest scan fallback)."""
        p = Path(lot_id)
        if not p.is_absolute():
            p = self.root / p
        if p.is_dir():
            return fields_for_lot(p, refilter)
        lots: List[Path] = []
        for row in self.search("", {"lot_number": str(lot_id)}):
            sdir = Path(row["path"])
            lot = sdir if (sdir / "lot.json").exists() else sdir.parent
            if lot not in lots:
                lots.append(lot)
        return [f for lot in lots for f in fields_for_lot(lot, refilter)]

    def index_lot_verdict(self, lot_path: Union[str, Path]) -> dict:
        """Compute (``compute_lot_verdict``) and cache the INN-02 verdict
        for one lot. Returns the verdict dict regardless of whether the
        cache write itself succeeds. A lot with no spec attached
        (``overall == "no_spec"``) is never written -- any stale cached row
        (spec since removed) is dropped instead -- so a workspace with no
        specs defined never gains a single ``lot_verdicts`` row."""
        lot_path = Path(lot_path)
        verdict = compute_lot_verdict(lot_path)
        overall = str(verdict.get("overall", "no_spec"))
        try:
            with self._connection() as conn:
                if overall == "no_spec":
                    conn.execute("DELETE FROM lot_verdicts WHERE path = ?", (str(lot_path),))
                else:
                    project_meta = _project_meta_for_lot(lot_path)
                    row = (str(lot_path), str(project_meta.get("name", "")),
                           _sample_id_for_lot(lot_path), _lot_number_for(lot_path),
                           overall, json.dumps(verdict))
                    conn.execute(
                        "INSERT INTO lot_verdicts (path, project, sample_id, lot_number, overall, verdict_json) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
                        "project=excluded.project, sample_id=excluded.sample_id, "
                        "lot_number=excluded.lot_number, overall=excluded.overall, "
                        "verdict_json=excluded.verdict_json",
                        row,
                    )
        except sqlite3.Error:
            pass
        return verdict

    def get_lot_verdict(self, lot_path: Union[str, Path]) -> dict:
        """The cached INN-02 verdict for ``lot_path`` if indexed, else a
        live evaluation (DB missing/locked/never indexed -- D-04: the cache
        is never the source of truth)."""
        lot_path = Path(lot_path)
        try:
            with self._connection() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT verdict_json FROM lot_verdicts WHERE path = ?", (str(lot_path),)
                ).fetchone()
                if row is not None:
                    return json.loads(row["verdict_json"])
        except (sqlite3.Error, ValueError):
            pass
        return compute_lot_verdict(lot_path)

    def rebuild(self) -> int:
        """Rescan every manifest.json under the workspace root and rebuild
        the index from scratch. Returns the number of sessions indexed.
        Degrades gracefully: if the DB itself can't be written (locked,
        corrupt beyond repair), still returns the count found on disk."""
        rows = [r for r in (_row_from_manifest(d, self.root) for d in _manifest_paths(self.root)) if r]
        try:
            if self.db_path.exists():
                try:
                    with self._connection() as conn:
                        conn.execute("DELETE FROM sessions")
                        conn.execute("DELETE FROM lot_verdicts")
                except sqlite3.Error:
                    # Corrupt DB file: drop it and start fresh.
                    for suffix in ("", "-wal", "-shm"):
                        p = Path(str(self.db_path) + suffix)
                        if p.exists():
                            p.unlink()
            with self._connection() as conn:
                cols = ", ".join(_COLUMNS)
                placeholders = ", ".join("?" for _ in _COLUMNS)
                conn.executemany(
                    f"INSERT OR REPLACE INTO sessions ({cols}) VALUES ({placeholders})",
                    [[row[c] for c in _COLUMNS] for row in rows],
                )
        except sqlite3.Error:
            pass
        # INN-02: recompute every lot's verdict cache so a rebuild restores
        # the same badges a live evaluation would show right now.
        for lot_dir in _lot_dirs(self.root):
            self.index_lot_verdict(lot_dir)
        return len(rows)
