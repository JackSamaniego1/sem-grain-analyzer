"""
UI-09 — Projects-browser file management helpers (no Qt).

Folder moves (sample → another project, lot → another sample, session →
another lot), and image-level operations inside a session / lot record:
move to another lot, rename, move to the trash and restore. Everything is
containment-checked against the workspace root, never overwrites (name
collisions get the usual `` (2)`` suffix) and never destroys data: the
trash is ``<root>/.trash`` exactly like ``Workspace.trash_*``.

Image files of one image ``<stem><ext>`` inside a session directory:
``images/<stem><ext>``, ``results/<stem>.{labels.npz,overlay.png,
grains.json,summary.json}``, ``thumbs/<stem>.jpg``; plus its entry in
``manifest.json``.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from data.models import (
    SessionMeta, dedupe_name, local_now_display, read_json, sanitize_name, utc_now_iso,
    write_json_atomic,
)
from data.workspace import Workspace

PathLike = Union[str, Path]

IMAGE_TRASH_MARKER = "_trash_images.json"
RESULT_SUFFIXES = ("labels.npz", "overlay.png", "grains.json", "summary.json")
PARENT_KIND = {"sample": "project", "lot": "sample", "session": "lot", "image": "lot"}
_META = {"project": "project.json", "sample": "sample.json", "lot": "lot.json"}


# ======================================================================
# classification / destinations
# ======================================================================

def classify(path: PathLike) -> Optional[str]:
    """``project`` / ``sample`` / ``lot`` / ``session`` from the metadata
    file in the folder (a lot record -- lot.json + manifest.json -- is a
    ``lot``), or ``None``."""
    p = Path(path)
    for kind in ("lot", "sample", "project"):
        if (p / _META[kind]).exists():
            return kind
    if (p / "manifest.json").exists():
        return "session"
    return None


def _all_of_kind(ws: Workspace, kind: str) -> List[Path]:
    out: List[Path] = []
    for pm in ws.list_projects():
        pp = Path(pm.path)
        if kind == "project":
            out.append(pp)
            continue
        for sm in ws.list_samples(pp):
            sp = Path(sm.path)
            if kind == "sample":
                out.append(sp)
                continue
            for lm in ws.list_lots(pp, sp):
                out.append(Path(lm.path))
    return out


def move_destinations(ws: Workspace, kind: str, sources: Sequence[PathLike]) -> List[Path]:
    """Every folder that items of ``kind`` may be moved into -- one level up
    only (sample → projects, lot → samples, session/image → lots) --
    excluding the folder(s) they are already in. For ``image`` the sources
    are the session/lot directories the images currently live in."""
    dest_kind = PARENT_KIND.get(kind)
    if dest_kind is None:
        return []
    if kind == "image":
        current = {Path(s).resolve() for s in sources}
    else:
        current = {Path(s).parent.resolve() for s in sources}
    # never offer a folder inside one of the moved folders
    moved = [Path(s).resolve() for s in sources] if kind != "image" else []
    out = []
    for d in _all_of_kind(ws, dest_kind):
        r = d.resolve()
        if r in current:
            continue
        if any(_is_within(r, m) for m in moved):
            continue
        out.append(d)
    return out


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# ======================================================================
# folder moves
# ======================================================================

def _level_values(lot_dir: Path) -> Dict[str, str]:
    """project / sample_id / lot_number of the lot at ``lot_dir``."""
    def rd(p: Path) -> dict:
        try:
            return read_json(p) if p.exists() else {}
        except (OSError, ValueError):
            return {}
    lm = rd(lot_dir / "lot.json")
    sm = rd(lot_dir.parent / "sample.json")
    pm = rd(lot_dir.parent.parent / "project.json")
    return {"project": pm.get("name") or lot_dir.parent.parent.name,
            "sample_id": sm.get("sample_id") or lot_dir.parent.name,
            "lot_number": lm.get("lot_number") or lot_dir.name}


def _nearest_lot(path: Path, stop: Path) -> Optional[Path]:
    p = path
    while True:
        if (p / "lot.json").exists():
            return p
        if p == stop or p.parent == p:
            return None
        p = p.parent


def _refresh_manifests(ws: Workspace, top: Path) -> List[Path]:
    """Rewrite the project / sample / lot labels stored in every manifest
    at or under ``top`` (they follow the folder they now live in)."""
    dirs = []
    root = ws.root.resolve()
    for mp in sorted(top.rglob("manifest.json")):
        if ".trash" in mp.parts:
            continue
        lot = _nearest_lot(mp.parent, root)
        if lot is None:
            continue
        try:
            d = read_json(mp)
        except (OSError, ValueError):
            continue
        d.update(_level_values(lot))
        write_json_atomic(mp, d)
        dirs.append(mp.parent)
    return dirs


def move_node(ws: Workspace, path: PathLike, dest_parent: PathLike, catalog=None) -> Path:
    """Move a sample / lot / session folder into ``dest_parent`` (which must
    be of the level directly above it). Returns the new path; a name clash
    in the destination gets a `` (2)`` suffix. Manifests under the moved
    folder are relabelled; the catalog (if given) is re-indexed."""
    path = ws._require_within_root(Path(path))
    dest_parent = ws._require_within_root(Path(dest_parent))
    kind = classify(path)
    if kind not in ("sample", "lot", "session"):
        raise ValueError(f"{path.name} cannot be moved (only samples, lots and sessions)")
    want = PARENT_KIND[kind]
    if classify(dest_parent) != want:
        raise ValueError(f"{dest_parent.name} is not a {want} folder")
    if _is_within(dest_parent.resolve(), path.resolve()):
        raise ValueError("Cannot move a folder into itself")
    if path.parent.resolve() == dest_parent.resolve():
        return path
    old_sessions = [mp.parent for mp in path.rglob("manifest.json")] if catalog is not None else []
    target = dest_parent / dedupe_name(dest_parent, path.name)
    shutil.move(str(path), str(target))
    new_sessions = _refresh_manifests(ws, target)
    if catalog is not None:
        for sp in old_sessions:
            catalog.remove(str(sp))
        for sp in new_sessions:
            catalog.index_session(sp)
    return target


# ======================================================================
# images
# ======================================================================

def _manifest(session_dir: Path) -> SessionMeta:
    return SessionMeta.from_dict(read_json(session_dir / "manifest.json"))


def _write_manifest(session_dir: Path, meta: SessionMeta) -> None:
    d = meta.to_dict()
    d.pop("path", None)
    # keep keys this version of the dataclass doesn't know about
    try:
        old = read_json(session_dir / "manifest.json")
    except (OSError, ValueError):
        old = {}
    for k, v in old.items():
        d.setdefault(k, v)
    write_json_atomic(session_dir / "manifest.json", d)


def image_files(session_dir: PathLike, filename: str) -> List[Path]:
    """Existing files that belong to one image (image, results, thumbnail)."""
    sd = Path(session_dir)
    stem = Path(filename).stem
    cands = [sd / "images" / filename, sd / "thumbs" / f"{stem}.jpg"]
    cands += [sd / "results" / f"{stem}.{s}" for s in RESULT_SUFFIXES]
    return [p for p in cands if p.is_file()]


def _renamed(p: Path, old_stem: str, new_stem: str, new_filename: str) -> str:
    if p.parent.name == "images":
        return new_filename
    return new_stem + p.name[len(old_stem):]


def _taken_names(session_dir: Path, meta: Optional[SessionMeta]) -> Set[str]:
    names = {e.filename.lower() for e in (meta.images if meta else [])}
    idir = session_dir / "images"
    if idir.exists():
        names |= {p.name.lower() for p in idir.iterdir()}
    return names


def _taken_stems(session_dir: Path, meta: Optional[SessionMeta]) -> Set[str]:
    return {Path(n).stem.lower() for n in _taken_names(session_dir, meta)}


def _free_filename(session_dir: Path, filename: str, meta: Optional[SessionMeta],
                   extra: Set[str] = frozenset(), exclude: str = "") -> str:
    stem, suffix = Path(filename).stem, Path(filename).suffix
    stems = (_taken_stems(session_dir, meta) | {s.lower() for s in extra}) - {exclude.lower()}
    cand, n = stem, 2
    while cand.lower() in stems:
        cand = f"{stem} ({n})"
        n += 1
    return cand + suffix


def _move_image_files(src: Path, dst: Path, old: str, new: str) -> None:
    old_stem, new_stem = Path(old).stem, Path(new).stem
    for f in image_files(src, old):
        sub = f.parent.name
        (dst / sub).mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dst / sub / _renamed(f, old_stem, new_stem, new)))


def rename_image(ws: Workspace, session_dir: PathLike, filename: str, new_name: str,
                 catalog=None) -> str:
    """Rename one image (its file, results and thumbnail, and the manifest
    entry). The original extension is kept; returns the final filename
    (deduplicated if the name is already used in this folder)."""
    sd = ws._require_within_root(Path(session_dir))
    meta = _manifest(sd)
    entry = next((e for e in meta.images if e.filename == filename), None)
    if entry is None:
        raise ValueError(f"{filename} is not in this folder")
    suffix = Path(filename).suffix
    base = new_name.strip()
    if base.lower().endswith(suffix.lower()):
        base = base[: -len(suffix)]
    base = sanitize_name(base)
    if not base:
        raise ValueError("The new name is empty")
    wanted = base + suffix
    if wanted == filename:
        return filename
    final = _free_filename(sd, wanted, meta, exclude=Path(filename).stem)
    _move_image_files(sd, sd, filename, final)
    entry.filename = final
    _write_manifest(sd, meta)
    if catalog is not None:
        catalog.index_session(sd)
    return final


def _ensure_manifest(dest: Path, like: SessionMeta) -> SessionMeta:
    if (dest / "manifest.json").exists():
        return _manifest(dest)
    lv = _level_values(dest) if (dest / "lot.json").exists() else {}
    meta = SessionMeta(
        session_id=dest.name, created_utc=utc_now_iso(), created_local=local_now_display(),
        operator=like.operator, project=lv.get("project", like.project),
        sample_id=lv.get("sample_id", like.sample_id),
        lot_number=lv.get("lot_number", like.lot_number), instrument=like.instrument,
        magnification=like.magnification, accelerating_voltage_kv=like.accelerating_voltage_kv,
        working_distance_mm=like.working_distance_mm, detector_mode=like.detector_mode,
        px_per_um=like.px_per_um, detection_params=dict(like.detection_params or {}),
        software_version=like.software_version, filters=dict(like.filters or {}), images=[])
    for sub in ("images", "results", "thumbs"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    write_json_atomic(dest / "manifest.json", meta.to_dict())
    if not (dest / "report.json").exists():
        write_json_atomic(dest / "report.json", {})
    return meta


def move_images(ws: Workspace, session_dir: PathLike, filenames: Sequence[str],
                dest_dir: PathLike, catalog=None,
                target_names: Optional[Sequence[str]] = None) -> List[Tuple[str, str]]:
    """Move images (files + results + manifest entries) from one session /
    lot record to another. The session-level calibration and filters the
    images were measured with travel with them (as per-image values) when
    the destination's differ. ``target_names`` (same order as
    ``filenames``) asks for specific names in the destination -- Undo uses
    it to restore the original names; a taken name is still deduplicated.
    Returns ``[(old_filename, new_filename)]``."""
    src = ws._require_within_root(Path(session_dir))
    dst = ws._require_within_root(Path(dest_dir))
    if src.resolve() == dst.resolve():
        return [(f, f) for f in filenames]
    if classify(dst) not in ("lot", "session"):
        raise ValueError(f"{dst.name} cannot hold images")
    smeta = _manifest(src)
    by_name = {e.filename: e for e in smeta.images}
    missing = [f for f in filenames if f not in by_name]
    if missing:
        raise ValueError(f"Not in this folder: {', '.join(missing)}")
    dmeta = _ensure_manifest(dst, smeta)
    moved: List[Tuple[str, str]] = []
    new_stems: Set[str] = set()
    wanted = list(target_names) if target_names is not None else list(filenames)
    for fn, want in zip(filenames, wanted):
        e = by_name[fn]
        new = _free_filename(dst, want or fn, dmeta, extra=new_stems)
        new_stems.add(Path(new).stem)
        _move_image_files(src, dst, fn, new)
        if not e.px_per_um and smeta.px_per_um and smeta.px_per_um != dmeta.px_per_um:
            e.px_per_um = smeta.px_per_um
        if e.filters_override is None and smeta.filters and smeta.filters != dmeta.filters:
            e.filters_override = dict(smeta.filters)
        e.filename = new
        dmeta.images.append(e)
        moved.append((fn, new))
    moved_ids = {id(by_name[fn]) for fn, _ in moved}
    smeta.images = [e for e in smeta.images if id(e) not in moved_ids]
    _write_manifest(dst, dmeta)       # destination first: a crash duplicates, never loses
    _write_manifest(src, smeta)
    if catalog is not None:
        catalog.index_session(src)
        catalog.index_session(dst)
    return moved


def trash_images(ws: Workspace, session_dir: PathLike, filenames: Sequence[str],
                 catalog=None) -> Path:
    """Move images (files, results, thumbnails) into ``<root>/.trash`` and
    drop them from the manifest; the entries are kept in the trash folder so
    ``restore_images`` can put everything back. Returns the trash folder."""
    sd = ws._require_within_root(Path(session_dir))
    meta = _manifest(sd)
    wanted = set(filenames)
    entries = [e for e in meta.images if e.filename in wanted]
    if len(entries) != len(wanted):
        raise ValueError("Some images are not in this folder")
    rel = sd.resolve().relative_to(ws.root.resolve())
    trash_root = ws.root / ".trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    dest = trash_root / dedupe_name(trash_root, f"{stamp}__images__{'__'.join(rel.parts)}")
    dest.mkdir(parents=True)
    write_json_atomic(dest / IMAGE_TRASH_MARKER,
                      {"origin_relpath": str(rel), "trashed_utc": utc_now_iso(),
                       "entries": [e.to_dict() for e in entries]})
    for e in entries:
        _move_image_files(sd, dest, e.filename, e.filename)
    meta.images = [e for e in meta.images if e.filename not in wanted]
    _write_manifest(sd, meta)
    if catalog is not None:
        catalog.index_session(sd)
    return dest


def is_image_trash(path: PathLike) -> bool:
    return (Path(path) / IMAGE_TRASH_MARKER).exists()


def restore_images(ws: Workspace, trash_path: PathLike, catalog=None) -> Path:
    """Undo ``trash_images``: move the files back and re-add the manifest
    entries. Raises ``FileExistsError`` if an image of the same name has
    appeared there since (never overwrites). Returns the session folder."""
    tp = ws._require_within_root(Path(trash_path))
    info = read_json(tp / IMAGE_TRASH_MARKER)
    sd = ws.root / Path(info["origin_relpath"])
    if not (sd / "manifest.json").exists():
        raise FileNotFoundError(f"The folder these images came from no longer exists: {sd}")
    from data.models import ImageManifestEntry
    entries = [ImageManifestEntry.from_dict(d) for d in info.get("entries", [])]
    meta = _manifest(sd)
    taken = _taken_names(sd, meta)
    clash = [e.filename for e in entries if e.filename.lower() in taken]
    if clash:
        raise FileExistsError(f"Already exists in the original folder: {', '.join(clash)}")
    for e in entries:
        _move_image_files(tp, sd, e.filename, e.filename)
    meta.images.extend(entries)
    _write_manifest(sd, meta)
    shutil.rmtree(tp, ignore_errors=True)
    if catalog is not None:
        catalog.index_session(sd)
    return sd


def restore_any(ws: Workspace, trash_path: PathLike, catalog=None) -> Path:
    """Restore a trashed folder or trashed images (whichever it is)."""
    if is_image_trash(trash_path):
        return restore_images(ws, trash_path, catalog=catalog)
    return ws.restore_from_trash(trash_path, catalog=catalog)


__all__ = [
    "classify", "move_destinations", "move_node", "image_files", "rename_image",
    "move_images", "trash_images", "restore_images", "restore_any", "is_image_trash",
    "IMAGE_TRASH_MARKER", "PARENT_KIND",
]
