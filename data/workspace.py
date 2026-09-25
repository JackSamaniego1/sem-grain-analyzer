"""
Workspace hierarchy: <root>/<Project>/<Sample>/<Lot>/<Session>/

Files on disk are the source of truth (D-04); ``data/catalog.py`` is just a
rebuildable cache. Every write in this module is atomic; folder names are
sanitized and deduplicated (D-04 / role file rules).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from data.hierarchy import (
    HierarchyProfile, RESERVED_LOT_SUBDIRS, load_profile, render_template, save_profile,
)
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
        self._profile: Optional[HierarchyProfile] = None

    # ------------------------------------------------------------------
    # hierarchy profile (HIER-01)
    # ------------------------------------------------------------------

    @property
    def profile(self) -> HierarchyProfile:
        """The workspace's :class:`~data.hierarchy.HierarchyProfile`,
        loaded (and, on a workspace with no ``workspace.json`` yet,
        decided-and-persisted) lazily on first use -- so a freshly
        constructed ``Workspace`` that never touches the profile never
        writes ``workspace.json`` as a side effect."""
        if self._profile is None:
            self._profile = load_profile(self.root)
        return self._profile

    def reload_profile(self) -> HierarchyProfile:
        """Force a re-read of ``workspace.json`` (e.g. after Settings
        edited it from another part of the app)."""
        self._profile = load_profile(self.root)
        return self._profile

    def set_profile(self, profile: HierarchyProfile) -> None:
        save_profile(self.root, profile)
        self._profile = profile

    def _level_context(self, level_key: str, id_value: str, fields: dict) -> dict:
        ctx = {"id": id_value}
        level = self.profile.level(level_key)
        if level:
            for f in level.fields:
                ctx[f.key] = fields.get(f.key, "")
        return ctx

    def _folder_name_for(self, level_key: str, id_value: str, fields: dict) -> str:
        level = self.profile.level(level_key)
        template = level.folder_template if level else "{id}"
        ctx = self._level_context(level_key, id_value, fields)
        rendered = render_template(template, ctx, for_filename=True)
        return rendered or sanitize_name(id_value)

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

    def _profile_field_extras(self, level_key: str, meta: dict) -> dict:
        """Profile field values (from ``**meta``) for ``level_key`` that
        aren't already covered by the level's fixed dataclass fields --
        written as extra top-level keys in the level's meta JSON so the
        hierarchy layer (``context_for_session``/catalog) can read them
        back, alongside the id under the pre-existing key."""
        level = self.profile.level(level_key)
        if not level:
            return {}
        return {f.key: meta[f.key] for f in level.fields if f.key in meta}

    def create_project(self, name: str, **meta) -> Path:
        dirname = dedupe_name(self.root, self._folder_name_for("project", name, meta))
        path = self.root / dirname
        path.mkdir(parents=True)
        pm = ProjectMeta(
            name=meta.pop("name", None) or name,
            description=meta.get("description", ""),
            customer=meta.get("customer", ""),
            created_utc=meta.get("created_utc") or utc_now_iso(),
        )
        d = pm.to_dict()
        d.update(self._profile_field_extras("project", meta))
        write_json_atomic(path / "project.json", d)
        return path

    def create_sample(self, project: ProjectLike, sample_id: str, **meta) -> Path:
        project_path = self.resolve_project(project)
        dirname = dedupe_name(project_path, self._folder_name_for("sample", sample_id, meta))
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
        d = sm.to_dict()
        d.update(self._profile_field_extras("sample", meta))
        write_json_atomic(path / "sample.json", d)
        return path

    def create_lot(self, project: ProjectLike, sample_id: ProjectLike,
                    lot_number: str, **meta) -> Path:
        sample_path = self.resolve_sample(project, sample_id)
        dirname = dedupe_name(sample_path, self._folder_name_for("lot", lot_number, meta))
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
        d = lm.to_dict()
        d.update(self._profile_field_extras("lot", meta))
        write_json_atomic(path / "lot.json", d)
        if self.profile.images_location == "lot":
            for sub in ("images", "results", "thumbs", "exports"):
                (path / sub).mkdir(exist_ok=True)
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
        """Fast: reads only manifest.json per session, no image/result IO.

        When the workspace's profile has ``images_location == "lot"`` the
        lot folder *is* the (one, continuous) session: if it has its own
        ``manifest.json`` that's returned as the sole entry (path == the
        lot path), never mistaking its own ``images/``/``results/``/etc.
        subfolders for sessions. Any legacy timestamped run subfolders
        left over from before the workspace switched profiles are still
        listed alongside it."""
        lot_path = self.resolve_lot(project, sample_id, lot_number)
        if self.profile.images_location == "lot":
            out: List[SessionMeta] = []
            manifest_path = lot_path / "manifest.json"
            if manifest_path.exists():
                try:
                    obj = SessionMeta.from_dict(read_json(manifest_path))
                    obj.path = str(lot_path)
                    out.append(obj)
                except (OSError, ValueError):
                    pass
            if lot_path.exists():
                for child in sorted(lot_path.iterdir()):
                    if not child.is_dir() or child.name in RESERVED_LOT_SUBDIRS:
                        continue
                    mp = child / "manifest.json"
                    if not mp.exists():
                        continue
                    try:
                        obj = SessionMeta.from_dict(read_json(mp))
                    except (OSError, ValueError):
                        continue
                    obj.path = str(child)
                    out.append(obj)
            return out
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

    # ------------------------------------------------------------------
    # INN-43: baseline lot per material
    # ------------------------------------------------------------------

    @staticmethod
    def baseline_group(lot_path: Union[str, Path]) -> str:
        """Key of the group a lot's baseline flag is unique within: the
        sample's material (case-insensitive), or -- when the material is
        blank -- the sample folder itself, so a blank material never links
        unrelated samples."""
        sample = Path(lot_path).parent
        try:
            material = str(read_json(sample / "sample.json").get("material") or "").strip()
        except (OSError, ValueError):
            material = ""
        return ("material:" + material.lower()) if material else ("sample:" + str(sample))

    def all_lot_paths(self) -> List[Path]:
        """Every lot folder (with a lot.json) of every project/sample."""
        out: List[Path] = []
        for pm in self.list_projects():
            for sm in self._list_children_with_meta(Path(pm.path), "sample.json", SampleMeta):
                out += [Path(lm.path) for lm in
                        self._list_children_with_meta(Path(sm.path), "lot.json", LotMeta)]
        return out

    def set_baseline_lot(self, lot_path: Union[str, Path], on: bool = True) -> List[Path]:
        """Mark (``on``) or un-mark a lot as the baseline for its material.
        Marking un-marks every other lot of the same material (see
        :meth:`baseline_group`).  Returns the lot folders whose flag changed."""
        lot = self._require_within_root(Path(lot_path))
        changed: List[Path] = []
        if on:
            group = self.baseline_group(lot)
            for lp in self.all_lot_paths():
                if lp.resolve() == lot.resolve():
                    continue
                try:
                    flagged = bool(read_json(lp / "lot.json").get("is_baseline"))
                except (OSError, ValueError):
                    continue
                if flagged and self.baseline_group(lp) == group:
                    self._update_meta_file(lp / "lot.json", LotMeta, is_baseline=False)
                    changed.append(lp)
        cur = LotMeta.from_dict(read_json(lot / "lot.json")) if (lot / "lot.json").exists() \
            else LotMeta()
        if bool(cur.is_baseline) != bool(on):
            self._update_meta_file(lot / "lot.json", LotMeta, is_baseline=bool(on))
            changed.append(lot)
        return changed

    def baseline_lot_for(self, lot_path: Union[str, Path]) -> Optional[Path]:
        """The baseline lot of ``lot_path``'s material (may be itself), or None."""
        group = self.baseline_group(lot_path)
        for lp in self.all_lot_paths():
            try:
                flagged = bool(read_json(lp / "lot.json").get("is_baseline"))
            except (OSError, ValueError):
                continue
            if flagged and self.baseline_group(lp) == group:
                return lp
        return None

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


# ==========================================================================
# HIER-01: "Rename existing folders to match" (Settings action)
# ==========================================================================

_RENAME_ORIGIN_FILENAME = "_rename_origin.json"

_LEVEL_META = {
    "project": ("project.json", "name"),
    "sample": ("sample.json", "sample_id"),
    "lot": ("lot.json", "lot_number"),
}


def _classify_level(path: Path) -> Optional[str]:
    if (path / "lot.json").exists():
        return "lot"
    if (path / "sample.json").exists():
        return "sample"
    if (path / "project.json").exists():
        return "project"
    return None


def _walk_hierarchy(path: Path):
    """Yield ``(level_key, path)`` for every project/sample/lot directory
    at or under ``path``, never descending into a lot's own reserved
    subfolders (``images/``, ``results/``, ...) or ``.trash``."""
    if not path.is_dir() or path.name in RESERVED_LOT_SUBDIRS:
        return
    level = _classify_level(path)
    if level:
        yield level, path
    if level == "lot":
        return
    try:
        children = sorted(path.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir() or child.name in RESERVED_LOT_SUBDIRS:
            continue
        yield from _walk_hierarchy(child)


def rename_to_template(workspace: "Workspace", node_path: Union[str, Path],
                        profile: Optional[HierarchyProfile] = None
                        ) -> List[Tuple[Path, Path]]:
    """Preview what "Rename existing folders to match" would do: for every
    project/sample/lot directory at or under ``node_path``, the folder
    name its level's ``folder_template`` (evaluated against that level's
    saved id + profile field values) would produce, if different from its
    current name. Read-only -- does not touch disk. Returns
    ``[(old_path, new_path), ...]`` for entries that would actually
    change, in top-down (parent-before-child) order; ``apply_renames``
    reorders for safe execution."""
    node_path = workspace._require_within_root(Path(node_path))
    prof = profile or workspace.profile
    plan: List[Tuple[Path, Path]] = []
    for level_key, path in _walk_hierarchy(node_path):
        meta_filename, id_key = _LEVEL_META[level_key]
        try:
            meta = read_json(path / meta_filename)
        except (OSError, ValueError):
            continue
        id_value = meta.get(id_key, "")
        level = prof.level(level_key)
        template = level.folder_template if level else "{id}"
        fields = {f.key: meta.get(f.key, "") for f in (level.fields if level else [])}
        ctx = {"id": id_value, **fields}
        try:
            desired = render_template(template, ctx, for_filename=True) or sanitize_name(str(id_value))
        except ValueError:
            continue
        if desired and desired != path.name:
            plan.append((path, path.parent / desired))
    return plan


def apply_renames(workspace: "Workspace",
                   plan: Sequence[Tuple[Union[str, Path], Union[str, Path]]],
                   *, catalog=None) -> List[Tuple[Path, Path]]:
    """Apply a rename plan (from ``rename_to_template``, or its reverse --
    ``[(new, old) for old, new in applied]`` -- to undo a previous apply).
    Every path is checked to stay within the workspace root; renames are
    executed deepest-first so a parent rename never invalidates an
    already-queued child path; a collision with an existing sibling (or
    another entry in this same plan) is resolved with the usual
    ``" (2)"`` dedupe suffix rather than overwriting anything. Each
    renamed folder gets a ``_rename_origin.json`` marker recording where
    it came from. If ``catalog`` is given, every session under a renamed
    folder is re-indexed at its new path. Returns the renames actually
    applied (post-dedupe), in the same ``(old, new)`` shape as the input,
    so the caller can build the reverse plan for Undo."""
    items = [(workspace._require_within_root(Path(o)), Path(n)) for o, n in plan]
    items.sort(key=lambda pair: len(pair[0].parts), reverse=True)
    applied: List[Tuple[Path, Path]] = []
    for old, new in items:
        if not old.exists():
            continue  # already moved as part of an ancestor's rename, or stale
        new = workspace._require_within_root(new)
        if new == old:
            continue
        old_manifest_dirs = list(old.rglob("manifest.json")) if catalog is not None else []
        parent = new.parent
        final_name = dedupe_name(parent, new.name, exclude=old)
        final = parent / final_name
        origin_rel = str(old.resolve().relative_to(workspace.root.resolve()))
        old.rename(final)
        write_json_atomic(final / _RENAME_ORIGIN_FILENAME,
                           {"origin_relpath": origin_rel, "renamed_utc": utc_now_iso()})
        applied.append((old, final))
        if catalog is not None:
            for old_manifest in old_manifest_dirs:
                old_dir = old_manifest.parent
                try:
                    rel = old_dir.relative_to(old)
                except ValueError:
                    continue
                catalog.remove(str(old_dir))
                catalog.index_session(final / rel)
    return applied
