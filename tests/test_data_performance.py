"""Listing and catalog rebuild must stay fast with many sessions (manifest
files only — no images/results IO), since the browser lists 1000+ sessions
by reading manifest.json alone."""
import time

from data.catalog import Catalog
from data.models import SessionMeta, write_json_atomic
from data.workspace import Workspace

N_SESSIONS = 300


def _make_fake_sessions(tmp_path, n=N_SESSIONS):
    ws = Workspace(tmp_path)
    proj = ws.create_project("PerfProject")
    sample = ws.create_sample(proj, "PerfSample")
    lot = ws.create_lot(proj, sample, "PerfLot")
    for i in range(n):
        session_dir = lot / f"2026-01-01_{i:06d}"
        session_dir.mkdir(parents=True)
        meta = SessionMeta(
            session_id=session_dir.name, project="PerfProject",
            sample_id="PerfSample", lot_number="PerfLot",
            operator="jack", created_utc=f"2026-01-01T00:{i % 60:02d}:00Z",
            notes=f"session {i}",
        )
        write_json_atomic(session_dir / "manifest.json", meta.to_dict())
    return ws, lot


def test_list_sessions_performance(tmp_path):
    ws, lot = _make_fake_sessions(tmp_path)
    start = time.perf_counter()
    sessions = ws.list_sessions("PerfProject", "PerfSample", "PerfLot")
    elapsed = time.perf_counter() - start
    assert len(sessions) == N_SESSIONS
    assert elapsed < 3.0, f"list_sessions took {elapsed:.2f}s for {N_SESSIONS} sessions"


def test_catalog_rebuild_performance(tmp_path):
    ws, lot = _make_fake_sessions(tmp_path)
    cat = Catalog(tmp_path)
    start = time.perf_counter()
    count = cat.rebuild()
    elapsed = time.perf_counter() - start
    assert count == N_SESSIONS
    assert elapsed < 3.0, f"catalog rebuild took {elapsed:.2f}s for {N_SESSIONS} sessions"
    assert len(cat.search("")) == N_SESSIONS
