"""
Session save/load: images + detection results + report, on disk under a
lot directory as ``<Session yyyy-mm-dd_HHMMSS[ label]>/``.

All paths that hold user data live only under a ``Workspace.root`` passed
in by the caller (D-14: nothing is written to the OS temp dir here except
via atomic-write temp files in the destination directory, cleaned up
immediately).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import cv2
import numpy as np

logger = logging.getLogger(__name__)

from core.grain_detector import AnalysisResult
from data.models import (
    ImageEntry, ImageManifestEntry, SessionMeta, SessionRef,
    analysis_summary_from_dict, analysis_summary_to_dict, dedupe_name,
    default_operator, grains_from_list, grains_to_list, local_now_display,
    read_json, sanitize_name, session_timestamp_id, sha256_bytes,
    sha256_file, utc_now_iso, write_json_atomic,
)
from data.workspace import Workspace

_THUMB_MAX_DIM = 256
_SESSION_SUBDIRS = ("images", "results", "exports", "thumbs")


# ======================================================================
# internal helpers
# ======================================================================

def _ensure_session_dirs(session_dir: Path) -> None:
    for sub in _SESSION_SUBDIRS:
        (session_dir / sub).mkdir(parents=True, exist_ok=True)


def _read_bytes(path: Union[str, Path]) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _encode_png(image_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image_bgr)
    if not ok:
        raise ValueError("Failed to encode image as PNG")
    return buf.tobytes()


def _decode_image(data: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _make_thumbnail_bytes(image_bgr: np.ndarray, max_dim: int = _THUMB_MAX_DIM) -> bytes:
    h, w = image_bgr.shape[:2]
    scale = min(1.0, max_dim / max(h, w)) if max(h, w) > 0 else 1.0
    if scale < 1.0:
        image_bgr = cv2.resize(image_bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                                interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("Failed to encode thumbnail")
    return buf.tobytes()


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


_SUFFIX_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]")


def _sanitize_suffix(suffix: str) -> str:
    """Keep only ``.`` + alphanumerics (Windows chokes on e.g. ``?`` or
    ``<`` in a file extension); an extension-less name stays extension-less,
    but a suffix that sanitizes to nothing (``".!!!"``) falls back to
    ``.img`` rather than silently disappearing."""
    if not suffix:
        return ""
    cleaned = _SUFFIX_SANITIZE_RE.sub("", suffix[1:])
    return f".{cleaned}" if cleaned else ".img"


def _save_one_image(images_dir: Path, entry: ImageEntry, index: int):
    """Copy/encode one image into ``images_dir``, deduping identical content
    by sha256. Returns (dest_filename, sha256_hex, width, height, image_bgr)."""
    if entry.source_path and Path(entry.source_path).is_file():
        raw = _read_bytes(entry.source_path)
        base_name = entry.filename or Path(entry.source_path).name
    elif entry.image_bgr is not None:
        raw = _encode_png(entry.image_bgr)
        base_name = entry.filename or f"image_{index + 1:03d}.png"
    else:
        raise ValueError("ImageEntry needs source_path or image_bgr")

    base_name = sanitize_name(Path(base_name).stem) + _sanitize_suffix(Path(base_name).suffix)
    digest = sha256_bytes(raw)

    candidate = base_name
    n = 2
    while True:
        dest = images_dir / candidate
        if not dest.exists():
            _write_bytes_atomic(dest, raw)
            break
        try:
            existing_digest = sha256_file(dest)
        except OSError:
            existing_digest = None
        if existing_digest == digest:
            break  # identical content already present, reuse it
        stem, suffix = Path(base_name).stem, Path(base_name).suffix
        candidate = f"{stem} ({n}){suffix}"
        n += 1

    if entry.image_bgr is not None:
        image_bgr = entry.image_bgr
    else:
        image_bgr = _decode_image(raw)

    h, w = (image_bgr.shape[:2] if image_bgr is not None else (0, 0))
    return dest.name, digest, w, h, image_bgr


def _save_result_files(results_dir: Path, thumbs_dir: Path, stem: str,
                        result: AnalysisResult, fallback_image: Optional[np.ndarray]) -> None:
    npz_path = results_dir / f"{stem}.labels.npz"
    tmp = npz_path.with_name(f".{npz_path.name}.tmp{os.getpid()}")
    # np.savez_compressed appends ".npz" to a bare filename string that
    # lacks it, which would silently write to the wrong path; pass an open
    # file object instead so the temp name is used exactly as given.
    with open(tmp, "wb") as fh:
        np.savez_compressed(
            fh,
            label_image=result.label_image if result.label_image is not None
            else np.zeros((0, 0), dtype=np.int32),
            valid_mask=result.valid_mask if result.valid_mask is not None
            else np.zeros((0, 0), dtype=bool),
            binary_image=result.binary_image if result.binary_image is not None
            else np.zeros((0, 0), dtype=np.uint8),
        )
    os.replace(tmp, npz_path)

    overlay = result.overlay_image if result.overlay_image is not None else fallback_image
    if overlay is not None:
        _write_bytes_atomic(results_dir / f"{stem}.overlay.png", _encode_png(overlay))
        _write_bytes_atomic(thumbs_dir / f"{stem}.jpg", _make_thumbnail_bytes(overlay))
    elif fallback_image is not None:
        _write_bytes_atomic(thumbs_dir / f"{stem}.jpg", _make_thumbnail_bytes(fallback_image))

    write_json_atomic(results_dir / f"{stem}.grains.json",
                       {"schema_version": 1, "grains": grains_to_list(result.grains)})
    write_json_atomic(results_dir / f"{stem}.summary.json",
                       {"schema_version": 1, **analysis_summary_to_dict(result)})


def _clean_array(npz, key: str):
    if key not in npz:
        return None
    arr = npz[key]
    if arr is not None and arr.size == 0 and arr.shape == (0, 0):
        return None
    return arr


def _load_result_for_stem(session_dir: Path, stem: str) -> Optional[AnalysisResult]:
    """Reconstruct one image's ``AnalysisResult`` from its results/ files.

    Never raises: a corrupted/truncated summary.json, grains.json, or
    labels.npz is logged and that piece degrades to a default/empty value
    instead of crashing the caller (typically the project browser listing
    many sessions at once). ``None`` is only returned when there is no
    summary.json at all (i.e. this image genuinely has no saved result)."""
    npz_path = session_dir / "results" / f"{stem}.labels.npz"
    summary_path = session_dir / "results" / f"{stem}.summary.json"
    grains_path = session_dir / "results" / f"{stem}.grains.json"
    overlay_path = session_dir / "results" / f"{stem}.overlay.png"
    if not summary_path.exists():
        return None

    try:
        kwargs = analysis_summary_from_dict(read_json(summary_path))
    except (OSError, ValueError) as exc:
        logger.warning("Corrupt summary.json (%s, stem=%s): %s — using defaults",
                        session_dir, stem, exc)
        kwargs = analysis_summary_from_dict(None)

    grains = []
    if grains_path.exists():
        try:
            grains = grains_from_list(read_json(grains_path).get("grains", []))
        except (OSError, ValueError) as exc:
            logger.warning("Corrupt grains.json (%s, stem=%s): %s — grain list unavailable",
                            session_dir, stem, exc)

    label_image = valid_mask = binary_image = None
    if npz_path.exists():
        try:
            with np.load(npz_path) as npz:
                label_image = _clean_array(npz, "label_image")
                valid_mask = _clean_array(npz, "valid_mask")
                binary_image = _clean_array(npz, "binary_image")
        except Exception as exc:
            # A truncated/non-npz file can raise anything from zipfile
            # (BadZipFile), OSError, EOFError, or ValueError depending on
            # exactly how it's broken — all of them mean "no arrays".
            logger.warning("Corrupt labels.npz (%s, stem=%s): %s — arrays unavailable",
                            session_dir, stem, exc)
            label_image = valid_mask = binary_image = None

    overlay_image = None
    if overlay_path.exists():
        try:
            overlay_image = cv2.imread(str(overlay_path), cv2.IMREAD_COLOR)
        except Exception as exc:
            logger.warning("Corrupt overlay.png (%s, stem=%s): %s — overlay unavailable",
                            session_dir, stem, exc)

    result = AnalysisResult(**kwargs)
    result.grains = grains
    result.label_image = label_image
    result.valid_mask = valid_mask
    result.binary_image = binary_image
    result.overlay_image = overlay_image
    return result


# ======================================================================
# public: SessionImage / LoadedSession (read side, lazy result loading)
# ======================================================================

_UNSET = object()


class SessionImage:
    """One image entry of a loaded session. ``.result`` is loaded from
    ``results/*.npz`` / ``*.json`` / ``*.overlay.png`` lazily, on first
    access, so listing/browsing many sessions stays cheap."""

    def __init__(self, session_dir: Path, entry: ImageManifestEntry):
        self.entry = entry
        self.filename = entry.filename
        self.stem = Path(entry.filename).stem
        self.path = session_dir / "images" / entry.filename
        self.thumb_path = session_dir / "thumbs" / f"{self.stem}.jpg"
        self._session_dir = session_dir
        self._result = _UNSET

    @property
    def result(self) -> Optional[AnalysisResult]:
        if self._result is _UNSET:
            self._result = (_load_result_for_stem(self._session_dir, self.stem)
                             if self.entry.has_result else None)
        return self._result

    def __repr__(self):
        return f"SessionImage({self.filename!r}, has_result={self.entry.has_result})"


@dataclass
class LoadedSession:
    path: Path
    manifest: SessionMeta
    images: List[SessionImage] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    project_meta: Optional[dict] = None
    sample_meta: Optional[dict] = None
    lot_meta: Optional[dict] = None

    def image_by_filename(self, filename: str) -> Optional[SessionImage]:
        for img in self.images:
            if img.filename == filename:
                return img
        return None


# ======================================================================
# public: save / update / load
# ======================================================================

def _build_manifest_images(images_dir: Path, results_dir: Path, thumbs_dir: Path,
                            entries: Sequence[ImageEntry]) -> List[ImageManifestEntry]:
    """Save every image; one bad entry (unreadable source file, an image
    that can't be encoded, ...) is skipped with a warning recorded on its
    manifest entry rather than aborting the whole session save and leaving
    the earlier images half-written with no manifest at all."""
    manifest_images: List[ImageManifestEntry] = []
    for i, entry in enumerate(entries):
        try:
            filename, digest, w, h, image_bgr = _save_one_image(images_dir, entry, i)
        except Exception as exc:
            attempted = (entry.filename or
                         (Path(entry.source_path).name if entry.source_path else
                          f"image_{i + 1:03d}"))
            logger.warning("Skipping image %d (%s): %s", i, attempted, exc)
            manifest_images.append(ImageManifestEntry(
                filename=attempted,
                notes=f"FAILED to import: {exc}",
                source_path=str(entry.source_path) if entry.source_path else "",
            ))
            continue

        stem = Path(filename).stem
        has_result = entry.result is not None
        try:
            if has_result:
                _save_result_files(results_dir, thumbs_dir, stem, entry.result, image_bgr)
            elif image_bgr is not None:
                _write_bytes_atomic(thumbs_dir / f"{stem}.jpg", _make_thumbnail_bytes(image_bgr))
        except Exception as exc:
            logger.warning("Result save failed for %s: %s — image kept, results dropped",
                            filename, exc)
            has_result = False

        manifest_images.append(ImageManifestEntry(
            filename=filename,
            sha256=digest,
            width=w, height=h,
            grain_count=len(entry.result.grains) if has_result else 0,
            has_result=has_result,
            px_per_um=entry.px_per_um or (entry.result.px_per_um if has_result else 0.0),
            has_calibration=bool(entry.result.has_calibration) if has_result else False,
            scan_rect=list(entry.scan_rect) if entry.scan_rect else None,
            notes=entry.notes,
            source_path=str(entry.source_path) if entry.source_path else "",
        ))
    return manifest_images


def save_session(lot_path: Union[str, Path], session_meta: Optional[dict],
                  images: Sequence[ImageEntry], *, label: Optional[str] = None,
                  catalog=None) -> SessionRef:
    """Create a new session directory under ``lot_path`` and save
    ``images`` (each optionally carrying an ``AnalysisResult``) into it."""
    lot_path = Path(lot_path)
    session_meta = dict(session_meta or {})
    label = label if label is not None else session_meta.get("label", "")

    base_id = session_timestamp_id()
    dirname = f"{base_id} {sanitize_name(label)}" if label else base_id
    dirname = dedupe_name(lot_path, dirname)
    session_dir = lot_path / dirname
    session_dir.mkdir(parents=True)
    _ensure_session_dirs(session_dir)

    manifest_images = _build_manifest_images(
        session_dir / "images", session_dir / "results", session_dir / "thumbs", images)

    lot_meta = read_json(lot_path / "lot.json") if (lot_path / "lot.json").exists() else {}
    sample_meta = (read_json(lot_path.parent / "sample.json")
                   if (lot_path.parent / "sample.json").exists() else {})
    project_meta = (read_json(lot_path.parent.parent / "project.json")
                    if (lot_path.parent.parent / "project.json").exists() else {})

    meta = SessionMeta(
        session_id=dirname,
        label=label,
        created_utc=utc_now_iso(),
        created_local=local_now_display(),
        operator=session_meta.get("operator") or default_operator(),
        project=session_meta.get("project", project_meta.get("name", "")),
        sample_id=session_meta.get("sample_id", sample_meta.get("sample_id", "")),
        lot_number=session_meta.get("lot_number", lot_meta.get("lot_number", "")),
        instrument=session_meta.get("instrument", ""),
        magnification=session_meta.get("magnification", ""),
        accelerating_voltage_kv=session_meta.get("accelerating_voltage_kv", 0.0),
        working_distance_mm=session_meta.get("working_distance_mm", 0.0),
        detector_mode=session_meta.get("detector_mode", ""),
        px_per_um=session_meta.get("px_per_um", 0.0),
        scan_rect=session_meta.get("scan_rect"),
        detection_params=session_meta.get("detection_params", {}) or {},
        software_version=session_meta.get("software_version", ""),
        notes=session_meta.get("notes", ""),
        tags=list(session_meta.get("tags", []) or []),
        images=manifest_images,
    )
    write_json_atomic(session_dir / "manifest.json", meta.to_dict())

    report_path = session_dir / "report.json"
    if not report_path.exists():
        write_json_atomic(report_path, {})

    ref = SessionRef(path=session_dir, project=meta.project, sample_id=meta.sample_id,
                      lot_number=meta.lot_number, session_id=meta.session_id)
    if catalog is not None:
        catalog.index_session(session_dir)
    return ref


def update_session(session_path: Union[str, Path], *,
                    images: Optional[Sequence[ImageEntry]] = None,
                    meta_updates: Optional[dict] = None,
                    report: Optional[dict] = None,
                    catalog=None) -> SessionRef:
    """Re-analysis / grain-edit autosave (DATA-08): overwrite results for
    images matched by filename (or add new ones), merge metadata fields,
    and/or replace report.json."""
    session_dir = Path(session_path)
    manifest_path = session_dir / "manifest.json"
    meta = SessionMeta.from_dict(read_json(manifest_path))

    if images:
        by_filename = {img.filename: img for img in meta.images}
        for entry in images:
            wanted_name = entry.filename or (Path(entry.source_path).name if entry.source_path else None)
            existing = by_filename.get(sanitize_name(Path(wanted_name).stem) + Path(wanted_name).suffix) \
                if wanted_name else None
            if existing is not None:
                stem = Path(existing.filename).stem
                if entry.result is not None:
                    image_bgr = entry.image_bgr
                    if image_bgr is None and (session_dir / "images" / existing.filename).exists():
                        image_bgr = cv2.imread(str(session_dir / "images" / existing.filename),
                                                cv2.IMREAD_COLOR)
                    _save_result_files(session_dir / "results", session_dir / "thumbs",
                                        stem, entry.result, image_bgr)
                    existing.has_result = True
                    existing.grain_count = len(entry.result.grains)
                    existing.px_per_um = entry.px_per_um or entry.result.px_per_um
                    existing.has_calibration = bool(entry.result.has_calibration)
                if entry.scan_rect:
                    existing.scan_rect = list(entry.scan_rect)
                if entry.notes:
                    existing.notes = entry.notes
            else:
                new_entries = _build_manifest_images(
                    session_dir / "images", session_dir / "results",
                    session_dir / "thumbs", [entry])
                meta.images.extend(new_entries)

    if meta_updates:
        for k, v in meta_updates.items():
            if hasattr(meta, k):
                setattr(meta, k, v)

    write_json_atomic(manifest_path, meta.to_dict())

    if report is not None:
        write_json_atomic(session_dir / "report.json", report)

    ref = SessionRef(path=session_dir, project=meta.project, sample_id=meta.sample_id,
                      lot_number=meta.lot_number, session_id=meta.session_id)
    if catalog is not None:
        catalog.index_session(session_dir)
    return ref


def load_session(path: Union[str, Path]) -> LoadedSession:
    """Reconstruct a full session: manifest, per-image lazy results, and
    the report dict. Cheap except for whatever result data the caller
    actually touches via ``SessionImage.result``."""
    session_dir = Path(path)
    manifest = SessionMeta.from_dict(read_json(session_dir / "manifest.json"))
    images = [SessionImage(session_dir, entry) for entry in manifest.images]

    report_path = session_dir / "report.json"
    report = read_json(report_path) if report_path.exists() else {}

    def _try_read(p: Path) -> Optional[dict]:
        try:
            return read_json(p) if p.exists() else None
        except (OSError, ValueError):
            return None

    lot_path = session_dir.parent
    sample_path = lot_path.parent
    project_path = sample_path.parent

    return LoadedSession(
        path=session_dir,
        manifest=manifest,
        images=images,
        report=report,
        project_meta=_try_read(project_path / "project.json"),
        sample_meta=_try_read(sample_path / "sample.json"),
        lot_meta=_try_read(lot_path / "lot.json"),
    )


def import_loose_images(workspace: Workspace, paths: Sequence[Union[str, Path]],
                         project: str, sample: str, lot: str,
                         session_label: Optional[str] = None, *, catalog=None) -> SessionRef:
    """Pull existing unorganized image files into the workspace as a
    session with no analysis results yet."""
    try:
        project_path = workspace.resolve_project(project)
    except FileNotFoundError:
        project_path = workspace.create_project(project)
    try:
        sample_path = workspace.resolve_sample(project_path, sample)
    except FileNotFoundError:
        sample_path = workspace.create_sample(project_path, sample)
    try:
        lot_path = workspace.resolve_lot(project_path, sample_path, lot)
    except FileNotFoundError:
        lot_path = workspace.create_lot(project_path, sample_path, lot)

    entries = [ImageEntry(source_path=str(p)) for p in paths]
    return save_session(
        lot_path,
        {"project": project, "sample_id": sample, "lot_number": lot,
         "label": session_label or "", "notes": "Imported loose images"},
        entries, label=session_label, catalog=catalog)
