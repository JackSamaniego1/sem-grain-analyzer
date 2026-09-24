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
from typing import Any, Dict, List, Optional, Union

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
    mean_diameter_um REAL
);
"""


def _manifest_paths(root: Path):
    if not root.exists():
        return
    for manifest_path in root.rglob("manifest.json"):
        if ".trash" in manifest_path.parts:
            continue
        yield manifest_path.parent


def _row_from_manifest(session_dir: Path) -> Optional[dict]:
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
    }


_COLUMNS = ["path", "project", "sample_id", "lot_number", "session_id", "label",
            "operator", "created_utc", "notes", "tags", "image_count",
            "grain_count", "mean_area_um2", "mean_diameter_um"]

_TEXT_COLUMNS = ["project", "sample_id", "lot_number", "session_id", "label",
                  "operator", "notes", "tags"]


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
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------

    def index_session(self, session_path: Union[str, Path]) -> bool:
        row = _row_from_manifest(Path(session_path))
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
            row = _row_from_manifest(session_dir)
            if row is None:
                continue
            if filters and any(row.get(k) != v for k, v in filters.items() if k in _COLUMNS):
                continue
            if text and not any(text_l in str(row.get(c, "")).lower() for c in _TEXT_COLUMNS):
                continue
            out.append(row)
        out.sort(key=lambda r: r.get("created_utc", ""), reverse=True)
        return out

    def rebuild(self) -> int:
        """Rescan every manifest.json under the workspace root and rebuild
        the index from scratch. Returns the number of sessions indexed.
        Degrades gracefully: if the DB itself can't be written (locked,
        corrupt beyond repair), still returns the count found on disk."""
        rows = [r for r in (_row_from_manifest(d) for d in _manifest_paths(self.root)) if r]
        try:
            if self.db_path.exists():
                try:
                    with self._connection() as conn:
                        conn.execute("DELETE FROM sessions")
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
        return len(rows)
