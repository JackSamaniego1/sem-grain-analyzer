"""UPDATE 4 item 2 (UI half): drag image files from Explorer onto a lot's image
area, and the Add-images button, share one path ending in add_images_to_lot."""
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402

from data.hierarchy import PRESETS  # noqa: E402
from data.models import AppSettings, SessionMeta, read_json  # noqa: E402
from data.settings import save_settings  # noqa: E402


def _img(path, seed):
    arr = np.random.default_rng(seed).integers(0, 255, (24, 24, 3)).astype("uint8")
    ok, buf = cv2.imencode(Path(path).suffix, arr)
    assert ok
    Path(path).write_bytes(buf.tobytes())
    return Path(path)


@pytest.fixture
def env(tmp_path, monkeypatch, qapp, qtbot):
    from ui.design.theme import apply_theme, set_reduced_motion
    from ui.app_shell import AppShell
    from ui.app_state import AppState, NodeRef
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    save_settings(AppSettings(workspace_root=str(tmp_path / "ws"), operator="T", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    state = AppState()
    state.set_profile(PRESETS["job_part_lot"])
    w = AppShell(state, probe_device=False)
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    ws = state.workspace
    job = ws.create_project("24-117")
    part = ws.create_sample(job, "7718-A")
    lot = ws.create_lot(job, part, "L-44A")
    pg = w.projects
    pg.reload()
    state.set_node(NodeRef("lot", lot))
    qtbot.waitUntil(lambda: not pg.is_loading(), timeout=5000)
    toasts = []
    monkeypatch.setattr(pg, "_toast", lambda *a, **k: toasts.append(a))
    src = tmp_path / "loose"
    src.mkdir()
    yield pg, lot, src, toasts, state
    w.close()
    set_reduced_motion(False)


def _mime(paths):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return md


def _drop(pg, paths):
    md = _mime(paths)      # keep alive: QDropEvent does not own it
    ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    pg.drop_frame.dropEvent(ev)
    return ev


def _wait(qtbot, pg):
    qtbot.waitUntil(lambda: not getattr(pg, "_adding", False), timeout=8000)
    qtbot.waitUntil(lambda: not pg.is_loading(), timeout=8000)


def _names(lot):
    return sorted(i.filename for i in SessionMeta.from_dict(read_json(lot / "manifest.json")).images)


def test_drop_onto_empty_lot_adds_and_copies(env, qtbot):
    pg, lot, src, toasts, _ = env
    a, b = _img(src / "a.png", 1), _img(src / "b.tif", 2)
    assert "Drop image files here" in pg.empty.body_label.text()
    _drop(pg, [a, b])
    _wait(qtbot, pg)
    assert _names(lot) == ["a.png", "b.tif"]
    assert a.exists() and b.exists()                       # copied, not moved
    assert (lot / "images" / "a.png").is_file()
    assert "2 images added" in toasts[-1][0]


def test_drop_onto_non_empty_lot(env, qtbot):
    pg, lot, src, toasts, _ = env
    _drop(pg, [_img(src / "a.png", 1)])
    _wait(qtbot, pg)
    _drop(pg, [_img(src / "c.png", 3)])
    _wait(qtbot, pg)
    assert _names(lot) == ["a.png", "c.png"]


def test_unsupported_reported_not_added(env, qtbot):
    pg, lot, src, toasts, _ = env
    doc = src / "notes.docx"
    doc.write_text("x")
    _drop(pg, [_img(src / "a.png", 1), doc])
    _wait(qtbot, pg)
    assert _names(lot) == ["a.png"]
    title, body, sev = toasts[-1][:3]
    assert "1 image added" in title
    assert "notes.docx is not a supported image type" in body and sev == "warning"


def test_duplicate_reported_as_already_in_lot(env, qtbot):
    pg, lot, src, toasts, _ = env
    a = _img(src / "a.png", 1)
    _drop(pg, [a])
    _wait(qtbot, pg)
    _drop(pg, [a])
    _wait(qtbot, pg)
    assert _names(lot) == ["a.png"]
    assert "1 already in this lot" in toasts[-1][1]


def test_drop_while_busy_is_ignored_with_message(env, qtbot):
    pg, lot, src, toasts, _ = env
    pg._adding = True
    _drop(pg, [_img(src / "a.png", 1)])
    assert "Still adding" in toasts[-1][0]
    assert not (lot / "manifest.json").exists() or _names(lot) == []
    pg._adding = False


def test_drag_enter_accepts_files_only(env):
    pg, lot, src, toasts, _ = env
    f = pg.drop_frame
    md1 = _mime([_img(src / "a.png", 1)])
    ev = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.CopyAction, md1,
                         Qt.LeftButton, Qt.NoModifier)
    f.dragEnterEvent(ev)
    assert ev.isAccepted() and not f.overlay.isHidden()
    f.dragLeaveEvent(None)
    assert f.overlay.isHidden()
    md = QMimeData()
    md.setText("in-app drag")
    ev2 = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    ev2.setAccepted(False)
    f.dragEnterEvent(ev2)
    assert not ev2.isAccepted()


def test_add_images_button_path_uses_add_images_to_lot(env, qtbot, monkeypatch):
    pg, lot, src, toasts, _ = env
    import data.session_io as sio
    calls = []
    real = sio.add_images_to_lot

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(sio, "add_images_to_lot", spy)
    a = _img(src / "a.png", 1)
    monkeypatch.setattr("ui.pages.projects_page.QFileDialog.getOpenFileNames",
                        staticmethod(lambda *_a, **_k: ([str(a)], "")))
    pg.import_files()
    _wait(qtbot, pg)
    assert calls and _names(lot) == ["a.png"]


def test_drop_on_open_lot_updates_open_session(env, qtbot):
    pg, lot, src, toasts, state = env
    _drop(pg, [_img(src / "a.png", 1)])
    _wait(qtbot, pg)
    state.open_session(lot)
    qtbot.waitUntil(lambda: state.session is not None and len(state.session.images) == 1,
                    timeout=8000)
    _drop(pg, [_img(src / "d.png", 4)])
    qtbot.waitUntil(lambda: len(state.session.images) == 2, timeout=8000)
    _wait(qtbot, pg)
    assert _names(lot) == ["a.png", "d.png"]


def test_session_switched_mid_add_does_not_stick_busy(env, qtbot):
    pg, lot, src, toasts, state = env
    _drop(pg, [_img(src / "a.png", 1)])
    _wait(qtbot, pg)
    state.open_session(lot)
    qtbot.waitUntil(lambda: state.session is not None and len(state.session.images) == 1,
                    timeout=8000)
    _drop(pg, [_img(src / "b.png", 2)])          # open-lot path
    assert pg._adding
    state.session = None                          # "another lot opened" before the copy returns
    _wait(qtbot, pg)                              # must clear, not stay busy forever
    assert not pg._adding
    _drop(pg, [_img(src / "c.png", 3)])
    _wait(qtbot, pg)
    assert _names(lot) == ["a.png", "b.png", "c.png"]
    assert not any("Still adding" in t[0] for t in toasts)


def test_drop_with_no_lot_selected_is_ignored(env):
    from ui.app_state import NodeRef
    pg, lot, src, toasts, state = env
    pg._node = NodeRef("workspace", state.root)
    _drop(pg, [_img(src / "a.png", 1)])
    assert not toasts and not pg._adding
    assert not (lot / "manifest.json").exists() or _names(lot) == []


def test_drop_under_session_profile_not_accepted(env):
    from data.hierarchy import PRESETS as P
    pg, lot, src, toasts, state = env
    state.set_profile(P["project_sample_lot_session"])
    assert not pg.accepts_image_drops()
    md = _mime([_img(src / "a.png", 1)])
    ev = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    ev.setAccepted(False)
    pg.drop_frame.dragEnterEvent(ev)
    assert not ev.isAccepted() and pg.drop_frame.overlay.isHidden()


def test_highlight_clears_on_drop(env, qtbot):
    pg, lot, src, toasts, _ = env
    f = pg.drop_frame
    md = _mime([_img(src / "a.png", 1)])
    ev = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    f.dragEnterEvent(ev)
    assert not f.overlay.isHidden()
    _drop(pg, [src / "a.png"])
    assert f.overlay.isHidden()
    _wait(qtbot, pg)


def test_dropped_folder_reason_passed_through(env, qtbot):
    pg, lot, src, toasts, _ = env
    d = src / "sub"
    d.mkdir()
    _drop(pg, [d])
    _wait(qtbot, pg)
    title, body, sev = toasts[-1][:3]
    assert "Folders aren't supported. Drop image files." in body and sev == "warning"
