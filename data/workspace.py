"""
Workspace hierarchy: <root>/<Project>/<Sample>/<Lot>/<Session>/

Files on disk are the source of truth (D-04); ``data/catalog.py`` is just a
rebuildable cache. Every write in this module is atomic; folder names are
sanitized and deduplicated (D-04 / role file rules).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union

from data.models import (
    LotMeta, ProjectMeta, SampleMeta, SessionMeta,
    dedupe_name, read_json, sanitize_name, utc_now_iso, write_json_atomic,
)

_TRASH_ORIGIN_FILENAME = "_trash_origin.json"

ProjectLike = Union[str, Path]


class Workspace:
    """All hierarchy CRUD for a single workspace root."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # containment
    # ------------------------------------------------------------------

    def _require_within_root(self, path: Path) -> Path:
        """Guard against a caller-supplied Path that points outside this
        workspace (e.g. a stale reference, a symlink, or a bug elsewhere) —
        no rename/delete/resolve call may ever touch data outside root."""
        path = Path(path)
        try:
            resolved = path.resolve()
            root_resolved = self.root.resolve()
        except OSError as exc:
            raise ValueError(f"Cannot resolve path {path}: {exc}") from exc
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise ValueError(
                f"Path {path} is outside the workspace root {self.root}") from None
        return path

    # ------------------------------------------------------------------
    # resolution helpers (accept either a display name or an existing Path)
    # ------------------------------------------------------------------

    @staticmethod
    def _find_child_by_meta(parent: Path, meta_filename: str, key: str,
                             value: str) -> Optional[Path]:
        if not parent.exists():
            return None
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name == ".trash":
                continue
            mp = child / meta_filename
            if not mp.exists():
                continue
            try:
                d = read_json(mp)
            except (OSError, ValueError):
                continue
            if d.get(key) == value:
                return child
        return None

    def resolve_project(self, project: ProjectLike) -> Path:
        if isinstance(project, Path):
            if project.exists():
                self._require_within_root(project)
                return project
            project = project.name
        found = self._find_child_by_meta(self.root, "project.json", "name", str(project))
        if found:
            return found
        cand = self.root / sanitize_name(str(project))
        if cand.exists():
            return cand
        raise FileNotFoundError(f"Project not found: {project!r}")

    def resolve_sample(self, project: ProjectLike, sample_id: ProjectLike) -> Path:
        project_path = self.resolve_project(project)
        if isinstance(sample_id, Path):
            if sample_id.exists():
                self._require_within_root(sample_id)
                return sample_id
            sample_id = sample_id.name
        found = self._find_child_by_meta(project_path, "sample.json", "sample_id", str(sample_id))
        if found:
            return found
        cand = project_path / sanitize_name(str(sample_id))
        if cand.exists():
            return cand
        raise FileNotFoundError(f"Sample not found: {sample_id!r} in project {project!r}")

    def resolve_lot(self, project: ProjectLike, sample_id: ProjectLike,
                     lot_number: ProjectLike) -> Path:
        sample_path = self.resolve_sample(project, sample_id)
        if isinstance(lot_number, Path):
            if lot_number.exists():
                self._require_within_root(lot_number)
                return lot_number
            lot_number = lot_number.name
        found = self._find_child_by_meta(sample_path, "lot.json", "lot_number", str(lot_number))
        if found:
            return found
        cand = sample_path / sanitize_name(str(lot_number))
        if cand.exists():
            return cand
        raise FileNotFoundError(
            f"Lot not found: {lot_number!r} in {project!r}/{sample_id!r}")

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    def create_project(self, name: str, **meta) -> Path:
        safe = sanitize_name(name)
        dirname = dedupe_name(self.root, safe)
        path = self.root / dirname
        path.mkdir(parents=True)
        pm = ProjectMeta(
            name=meta.pop("name", None) or name,
            description=meta.get("description", ""),
            customer=meta.get("customer", ""),
            created_utc=meta.get("created_utc") or utc_now_iso(),
        )
        write_json_atomic(path / "project.json", pm.to_dict())
        return path

    def create_sample(self, project: ProjectLike, sample_id: str, **meta) -> Path:
        project_path = self.resolve_project(project)
        safe = sanitize_name(sample_id)
        dirname = dedupe_name(project_path, safe)
        path = project_path / dirname
        path.mkdir(parents=True)
        sm = SampleMeta(
            sample_id=sample_id,
            material=meta.get("material", ""),
            alloy_grade=meta.get("alloy_grade", meta.get("grade", "")),
            heat_treatment=meta.get("heat_treatment", ""),
            description=meta.get("description", ""),
            created_utc=meta.get("created_utc") or utc_now_iso(),
        )
        write_json_atomic(path / "sample.json", sm.to_dict())
        return path

    def create_lot(self, project: ProjectLike, sample_id: ProjectLike,
                    lot_number: str, **meta) -> Path:
        sample_path = self.resolve_sample(project, sample_id)
        safe = sanitize_name(lot_number)
        dirname = dedupe_name(sample_path, safe)
        path = sample_path / dirname
        path.mkdir(parents=True)
        lm = LotMeta(
            lot_number=lot_number,
            supplier=meta.get("supplier", ""),
            received_date=meta.get("received_date", ""),
            notes=meta.get("notes", ""),
            spec_limits=meta.get("spec_limits", {}) or {},
            created_utc=meta.get("created_utc") or utc_now_iso(),
        )
        write_json_atomic(path / "lot.json", lm.to_dict())
        return path

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def _list_children_with_meta(self, parent: Path, meta_filename: str, cls):
        out = []
        if not parent.exists():
            return out
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name == ".trash":
                continue
            mp = child / meta_filename
            if not mp.exists():
                continue
            try:
                obj = cls.from_dict(read_json(mp))
            except (OSError, ValueError):
                continue
            obj.path = str(child)
            out.append(obj)
        return out

    def list_projects(self) -> List[ProjectMeta]:
        return self._list_children_with_meta(self.root, "project.json", ProjectMeta)

    def list_samples(self, project: ProjectLike) -> List[SampleMeta]:
        project_path = self.resolve_project(project)
        return self._list_children_with_meta(project_path, "sample.json", SampleMeta)

    def list_lots(self, project: ProjectLike, sample_id: ProjectLike) -> List[LotMeta]:
        sample_path = self.resolve_sample(project, sample_id)
        return self._list_children_with_meta(sample_path, "lot.json", LotMeta)

    def list_sessions(self, project: ProjectLike, sample_id: ProjectLike,
                       lot_number: ProjectLike) -> List[SessionMeta]:
        """Fast: reads only manifest.json per session, no image/result IO."""
        lot_path = self.resolve_lot(project, sample_id, lot_number)
        return self._list_children_with_meta(lot_path, "manifest.json", SessionMeta)

    # ------------------------------------------------------------------
    # rename / update metadata
    # ------------------------------------------------------------------

    def _rename_dir(self, path: Path, new_name: str) -> Path:
        self._require_within_root(path)
        safe = sanitize_name(new_name)
        parent = path.parent
        if safe == path.name:
            return path
        dirname = dedupe_name(parent, safe, exclude=path)
        target = parent / dirname
        path.rename(target)
        return target

    def rename_project(self, project: ProjectLike, new_name: str) -> Path:
        path = self.resolve_project(project)
        new_path = self._rename_dir(path, new_name)
        self.update_project_meta(new_path, name=new_name)
        return new_path

    def rename_sample(self, project: ProjectLike, sample_id: ProjectLike,
                       new_sample_id: str) -> Path:
        path = self.resolve_sample(project, sample_id)
        new_path = self._rename_dir(path, new_sample_id)
        self.update_sample_meta(new_path, sample_id=new_sample_id)
        return new_path

    def rename_lot(self, project: ProjectLike, sample_id: ProjectLike,
                    lot_number: ProjectLike, new_lot_number: str) -> Path:
        path = self.resolve_lot(project, sample_id, lot_number)
        new_path = self._rename_dir(path, new_lot_number)
        self.update_lot_meta(new_path, lot_number=new_lot_number)
        return new_path

    def rename_session(self, session_path: Path, new_label: str) -> Path:
        """Sessions keep their timestamp-derived folder name for identity
        (catalog keys, sha256 dedupe paths); "renaming" a session updates
        its display label in manifest.json instead of the folder."""
        session_path = self._require_within_root(Path(session_path))
        manifest_path = session_path / "manifest.json"
        meta = SessionMeta.from_dict(read_json(manifest_path))
        meta.label = new_label
        write_json_atomic(manifest_path, meta.to_dict())
        return session_path

    def _update_meta_file(self, meta_path: Path, cls, **fields):
        obj = cls.from_dict(read_json(meta_path)) if meta_path.exists() else cls()
        for k, v in fields.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        write_json_atomic(meta_path, obj.to_dict())
        return obj

    def update_project_meta(self, project: ProjectLike, **fields) -> ProjectMeta:
        path = self.resolve_project(project)
        return self._update_meta_file(path / "project.json", ProjectMeta, **fields)

    def update_sample_meta(self, project: ProjectLike, sample_id: Optional[ProjectLike] = None,
                            **fields) -> SampleMeta:
        # allow update_sample_meta(sample_path, **fields) too
        if sample_id is not None:
            path = self.resolve_sample(project, sample_id)
        else:
            path = self._require_within_root(Path(project))
        return self._update_meta_file(path / "sample.json", SampleMeta, **fields)

    def update_lot_meta(self, project: ProjectLike, sample_id: Optional[ProjectLike] = None,
                         lot_number: Optional[ProjectLike] = None, **fields) -> LotMeta:
        if sample_id is not None and lot_number is not None:
            path = self.resolve_lot(project, sample_id, lot_number)
        else:
            path = self._require_within_root(Path(project))
        return self._update_meta_file(path / "lot.json", LotMeta, **fields)

    def update_session_meta(self, session_path: Path, **fields) -> SessionMeta:
        session_path = self._require_within_root(Path(session_path))
        return self._update_meta_file(session_path / "manifest.json", SessionMeta, **fields)

    # ------------------------------------------------------------------
    # delete (trash, never destroy user data)
    # ------------------------------------------------------------------

    def delete_session(self, session_path: Path) -> Path:
        """Move a session directory to ``<root>/.trash/`` instead of
        deleting it. Returns the new (trashed) path."""
        session_path = self._require_within_root(Path(session_path))
        trash_root = self.root / ".trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        stamp = utc_now_iso().replace(":", "").replace("-", "")
        dest_name = dedupe_name(trash_root, f"{stamp}__{session_path.name}")
        dest = trash_root / dest_name
        origin_rel = str(session_path.resolve().relative_to(self.root.resolve()))
        shutil.move(str(session_path), str(dest))
        # Record the origin so the session can be restored (Undo / Trash view).
        write_json_atomic(dest / _TRASH_ORIGIN_FILENAME, {"origin_relpath": origin_rel})
        return dest

    def _trash_dir(self, path: Path, catalog=None) -> Path:
        """Move a project/sample/lot directory (and everything under it)
        into ``<root>/.trash/<UTC timestamp>__<relative path, '/' -> '__'>``,
        recording its original location so it can be restored later, and
        (if a ``Catalog`` is given) dropping every session that was under it
        from the index."""
        path = self._require_within_root(Path(path))
        root_resolved = self.root.resolve()
        if path.resolve() == root_resolved:
            raise ValueError("Refusing to trash the workspace root")

        orig_session_dirs = ([mp.parent for mp in path.rglob("manifest.json")]
                              if catalog is not None else [])

        rel = path.resolve().relative_to(root_resolved)
        flat = "__".join(rel.parts)
        trash_root = self.root / ".trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        stamp = utc_now_iso().replace(":", "").replace("-", "")
        dest = trash_root / dedupe_name(trash_root, f"{stamp}__{flat}")

        origin_rel = str(rel)
        shutil.move(str(path), str(dest))
        write_json_atomic(dest / _TRASH_ORIGIN_FILENAME, {"origin_relpath": origin_rel})

        if catalog is not None:
            for sp in orig_session_dirs:
                catalog.remove(str(sp))
        return dest

    def trash_project(self, name_or_path: ProjectLike, catalog=None) -> Path:
        """Move a whole project into the trash. If ``catalog`` is given,
        every session that was under it is removed from the index."""
        path = self.resolve_project(name_or_path)
        return self._trash_dir(path, catalog=catalog)

    def trash_sample(self, project: ProjectLike, sample: ProjectLike, catalog=None) -> Path:
        """Move a whole sample (all its lots/sessions) into the trash."""
        path = self.resolve_sample(project, sample)
        return self._trash_dir(path, catalog=catalog)

    def trash_lot(self, project: ProjectLike, sample: ProjectLike,
                  lot: ProjectLike, catalog=None) -> Path:
        """Move a whole lot (all its sessions) into the trash."""
        path = self.resolve_lot(project, sample, lot)
        return self._trash_dir(path, catalog=catalog)

    def list_trash(self) -> List[Dict[str, object]]:
        """List everything currently in ``<root>/.trash``. Each entry has
        ``path`` (the trashed directory), ``name``, and ``origin_relpath``
        (the path it was trashed from, relative to the workspace root, or
        ``None`` if it predates the ``_trash_origin.json`` marker / has none
        recorded)."""
        trash_root = self.root / ".trash"
        if not trash_root.exists():
            return []
        out: List[Dict[str, object]] = []
        for child in sorted(trash_root.iterdir()):
            if not child.is_dir():
                continue
            origin_relpath = None
            origin_file = child / _TRASH_ORIGIN_FILENAME
            if origin_file.exists():
                try:
                    origin_relpath = read_json(origin_file).get("origin_relpath")
                except (OSError, ValueError):
                    origin_relpath = None
            out.append({"path": child, "name": child.name, "origin_relpath": origin_relpath})
        return out

    def restore_from_trash(self, trash_path: Union[str, Path], catalog=None) -> Path:
        """Move a directory previously trashed by ``trash_project`` /
        ``trash_sample`` / ``trash_lot`` back to its recorded original
        location.

        Raises ``ValueError`` if ``trash_path`` isn't a direct child of
        ``<root>/.trash`` or has no recorded origin (e.g. something the user
        dropped into ``.trash`` manually, or a session trashed via
        ``delete_session``, which doesn't record one). Raises
        ``FileExistsError`` (with ``.suggested_alternative`` set to a free
        path from ``dedupe_name``) if the original location is occupied
        again -- restoring never silently overwrites existing data."""
        trash_path = self._require_within_root(Path(trash_path))
        trash_root = (self.root / ".trash").resolve()
        if trash_path.resolve().parent != trash_root:
            raise ValueError(f"{trash_path} is not a direct child of the trash folder")

        origin_file = trash_path / _TRASH_ORIGIN_FILENAME
        if not origin_file.exists():
            raise ValueError(f"No origin recorded for {trash_path}; cannot auto-restore")
        origin_rel = read_json(origin_file).get("origin_relpath")
        if not origin_rel:
            raise ValueError(f"No origin recorded for {trash_path}; cannot auto-restore")

        target = self.root / Path(origin_rel)
        if target.exists():
            suggestion = target.parent / dedupe_name(target.parent, target.name)
            exc = FileExistsError(
                f"Original location {target} already exists; "
                f"suggested alternative: {suggestion}")
            exc.suggested_alternative = suggestion  # type: ignore[attr-defined]
            raise exc

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trash_path), str(target))
        marker = target / _TRASH_ORIGIN_FILENAME
        if marker.exists():
            marker.unlink()

        if catalog is not None:
            for manifest_path in target.rglob("manifest.json"):
                catalog.index_session(manifest_path.parent)
        return target
