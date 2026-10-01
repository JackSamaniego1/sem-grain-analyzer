"""
The guided tour's sample job (batch 4, D-39) -- Qt-free, local files only.

``ensure_tutorial_job(root)`` creates (or reuses) job "Tutorial" › part
"Sample part" › lot "Lot 1" in the workspace and copies the three bundled
sample images (``assets/tutorial``) into it.  Idempotent: a second call
finds the same record.  With ``reset=True`` a record that was already set up
or analysed (scan area, scale, detection mode or results saved) is emptied
and re-created from the bundled images, so every tour step is actionable
again.  Only the tutorial's own record is ever touched; the job is deleted
like any other when the user no longer wants it.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, Optional, Union

from core.resources import REINSTALL_MESSAGE, tutorial_images
from data.models import read_json

PROJECT = "Tutorial"
SAMPLE = "Sample part"
LOT = "Lot 1"
SESSION_LABEL = "Tutorial"
_WIPE = ("images", "results", "thumbs", "manifest.json", "report.json")

__all__ = ["ensure_tutorial_job", "find_tutorial_record", "is_pristine", "PROJECT", "SAMPLE",
           "LOT", "REINSTALL_MESSAGE"]


def _ws(root):
    from data.workspace import Workspace
    return Workspace(root)


def _resolve(fn, *a) -> Optional[Path]:
    try:
        return fn(*a)
    except FileNotFoundError:
        return None


def _lot_mode(ws) -> bool:
    return ws.profile.images_location == "lot"


def _session_dirs(lot: Path):
    for d in sorted(lot.iterdir()) if lot.is_dir() else ():
        mp = d / "manifest.json"
        if d.is_dir() and d.name != ".trash" and mp.is_file():
            try:
                if read_json(mp).get("label") == SESSION_LABEL:
                    yield d
            except (OSError, ValueError):
                continue


def find_tutorial_record(root: Union[str, Path]) -> Optional[Path]:
    """The tutorial's record folder (the lot in lot mode, else its
    "Tutorial" session), or None when it does not exist yet."""
    ws = _ws(root)
    lot = _resolve(ws.resolve_lot, PROJECT, SAMPLE, LOT)
    if lot is None:
        return None
    if _lot_mode(ws):
        return lot if (lot / "manifest.json").is_file() else None
    return next(_session_dirs(lot), None)


def is_pristine(record: Path, n_images: int) -> bool:
    """Nothing set up yet: the bundled images only, no scan area, scale,
    chosen detection mode or result."""
    try:
        m = read_json(Path(record) / "manifest.json")
    except (OSError, ValueError):
        return False
    imgs = m.get("images") or []
    if len(imgs) != n_images:
        return False
    if float(m.get("px_per_um") or 0) > 0 or m.get("scan_rect"):
        return False
    dp = m.get("detection_params") or {}
    if dp and not isinstance(dp.get("wizard"), dict):
        return False                    # parameters saved without the wizard: chosen
    if (dp.get("wizard") or {}).get("mode_chosen"):
        return False
    for im in imgs:
        if im.get("has_result") or float(im.get("px_per_um") or 0) > 0 or \
                im.get("has_calibration") or im.get("scan_rect") or im.get("grain_edits") \
                or im.get("manual_excluded") or im.get("resolution_profile"):
            return False
    return True


def _wipe(record: Path, catalog, whole: bool) -> None:
    try:
        catalog.remove(record)
    except Exception:
        pass
    if whole:
        shutil.rmtree(record, ignore_errors=True)
        return
    for name in _WIPE:
        p = record / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try:
                p.unlink()
            except OSError:
                pass


def ensure_tutorial_job(root: Union[str, Path], operator: str = "",
                        reset: bool = True) -> Dict[str, object]:
    """Create or reuse the tutorial job; see the module docstring.

    Returns ``{"record", "lot", "project", "created", "reset"}``.  Raises
    ``FileNotFoundError`` with the reinstall message when the bundled images
    are missing (never offers a download)."""
    from data.catalog import Catalog
    from data.hierarchy import context_for_session
    from data.models import ImageEntry
    from data.session_io import save_session

    images = tutorial_images()                  # FileNotFoundError -> reinstall
    root = Path(root)
    ws = _ws(root)
    cat = Catalog(root)
    pp = _resolve(ws.resolve_project, PROJECT) or ws.create_project(
        PROJECT, description="Guided tour sample job (delete it any time)")
    sp = _resolve(ws.resolve_sample, pp, SAMPLE) or ws.create_sample(
        pp, SAMPLE, description="Synthetic SEM images for the guided tour")
    lp = _resolve(ws.resolve_lot, pp, sp, LOT) or ws.create_lot(pp, sp, LOT)
    lot_mode = _lot_mode(ws)
    record = find_tutorial_record(root)
    did_reset = False
    if record is not None and not is_pristine(record, len(images)):
        if not reset:
            return {"record": record, "lot": lp, "project": pp, "created": False,
                    "reset": False}
        _wipe(record, cat, whole=not lot_mode)
        record, did_reset = None, True
    if record is not None:
        return {"record": record, "lot": lp, "project": pp, "created": False, "reset": False}
    from version import __version__
    meta = {"operator": operator, "software_version": __version__,
            "notes": "Guided tour sample images (synthetic)"}
    entries = [ImageEntry(source_path=str(p)) for p in images]
    prof = ws.profile
    if lot_mode:
        ref = save_session(lp, meta, entries, in_place=True, catalog=cat,
                           image_name_template=prof.image_name_template,
                           name_context=context_for_session(lp, prof))
    else:
        ref = save_session(lp, dict(meta, label=SESSION_LABEL), entries, label=SESSION_LABEL,
                           catalog=cat, image_name_template=prof.image_name_template,
                           name_context={"project": PROJECT, "sample": SAMPLE, "lot": LOT,
                                         "label": SESSION_LABEL})
    return {"record": Path(ref.path), "lot": lp, "project": pp, "created": not did_reset,
            "reset": did_reset}
