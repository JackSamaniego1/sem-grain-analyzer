"""UPDATE 4 item 8 (UI): "Add grain" tool on the Review page (and the
Analyze canvas) - same drawing feel as Cut, undoable, persisted."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from data.models import read_json  # noqa: E402
from tests.test_ui_grain_edit import TIMEOUT, _analyse, _drag, _open, env  # noqa: E402,F401
from tests.ui_shell_helpers import make_session  # noqa: E402


class Toasts:
    def __init__(self):
        self.shown = []

    def show_toast(self, title, body="", severity="info", *a, **k):
        self.shown.append((title, body, severity))


def _sparse_canvas(qtbot):
    """Two grains on the left; the right half is free."""
    from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector
    from ui.canvas import GrainCanvas
    lab = np.zeros((100, 100), dtype=np.int32)
    lab[5:35, 5:35] = 1
    lab[50:80, 5:35] = 2
    res = AnalysisResult(label_image=lab, valid_mask=np.ones(lab.shape, bool))
    res.grains = GrainDetector()._measure_grains(
        lab, DetectionParams(min_grain_size_px=0, max_grain_size_px=0), 0.0)
    c = GrainCanvas()
    qtbot.addWidget(c)
    c.resize(420, 420)
    c.show()
    c.set_image(np.full((100, 100, 3), 90, np.uint8), res, raw=res, excluded={})
    return c


def _free_block(st, im, y0=40, x0=40, n=36):
    """Clear a square of the label image so there is room for a grain;
    returns the outline around it in canvas (image) coordinates."""
    _im, off = st._edit_target(im.uid)
    lab = im.raw.label_image
    lab[y0:y0 + n, x0:x0 + n] = 0
    ox, oy = off
    x0i, y0i = x0 + ox + 2, y0 + oy + 2
    m = n - 5
    return [(x0i, y0i), (x0i + m, y0i), (x0i + m, y0i + m), (x0i, y0i + m)]  # open stroke


# ---------------------------------------------------------------- canvas
def test_add_tool_gesture_key_and_closing_line(qtbot):
    c = _sparse_canvas(qtbot)
    c.zoom_by(1.5)
    got = []
    c.add_requested.connect(got.append)
    QTest.keyClick(c, Qt.Key_A)
    assert c.tool() == "add" and c.cursor().shape() == Qt.CrossCursor
    _drag(c, [(55, 20), (90, 20), (90, 60), (55, 60)])        # open stroke
    assert got and len(got[0]) >= 3
    xs, ys = [p[0] for p in got[0]], [p[1] for p in got[0]]
    assert min(xs) < 57 and max(xs) > 88 and min(ys) < 22 and max(ys) > 58
    assert c.tool() == "add"                                   # stays active, like Cut
    # while drawing the loop is painted closed back to the start (no crash)
    c.fit()
    from PySide6.QtWidgets import QApplication
    qtbot.wait(QApplication.doubleClickInterval() + 50)        # not a double-click

    def w(pt):
        return QPoint(int(c._offset.x() + pt[0] * c._scale), int(c._offset.y() + pt[1] * c._scale))
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, w((60, 70)))
    QTest.mouseMove(c, w((80, 72)))
    QTest.mouseMove(c, w((80, 90)))
    assert len(c.stroke()) >= 2
    assert not c.grab().isNull()
    QTest.keyClick(c, Qt.Key_Escape)                           # Esc cancels the stroke
    assert c.stroke() == [] and c.tool() == "add"
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, w((80, 90)))
    assert len(got) == 1
    QTest.keyClick(c, Qt.Key_Escape)                           # Esc again: select tool
    assert c.tool() == "select"


def test_tiny_scribble_is_ignored(qtbot):
    c = _sparse_canvas(qtbot)
    got = []
    c.add_requested.connect(got.append)
    c.set_tool("add")
    _drag(c, [(60, 60), (60.5, 60.4)])
    assert got == []


# ---------------------------------------------------------------- AppState
def test_add_grain_undo_persist_reload(env, qtbot):
    path = make_session(env, 1, label="Add")
    st = _open(qtbot, path)
    st.set_calibration(2.0)
    _analyse(st, qtbot)
    im = st.current_image()
    n0 = im.result.grain_count
    outline = _free_block(st, im)
    before = st.undo_stack.count()
    gid = st.add_grain(im.uid, outline)
    assert st.undo_stack.count() == before + 1
    assert st.undo_stack.undoText() == f"Add grain #{gid}"
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    g = next(g for g in im.raw.grains if g.grain_id == gid)
    assert 700 < g.area_px <= 36 * 36 and g.area_um2 == pytest.approx(g.area_px / 4.0)
    assert im.edits[-1]["op"] == "add" and im.edits[-1]["id"] == gid
    assert im.edits[-1]["area_px"] == g.area_px
    if gid not in im.excluded:
        assert im.result.grain_count == n0 + 1
    st.undo_stack.undo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert gid not in {g.grain_id for g in im.raw.grains} and im.edits == []
    st.undo_stack.redo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    st.flush()
    qtbot.waitUntil(lambda: st.save_state == "saved" and not st.is_dirty(), timeout=TIMEOUT)
    m = read_json(path / "manifest.json")
    e0 = next(e for e in m["images"] if e["filename"] == im.filename)
    assert [x["op"] for x in e0["grain_edits"]] == ["add"]
    # reopen: the added grain and its op come back
    st2 = _open(qtbot, path)
    im2 = st2.current_image()
    assert [x["op"] for x in im2.edits] == ["add"]
    assert gid in {g.grain_id for g in im2.raw.grains}
    assert im2.detector_labels is not None
    st2.flush()


def test_nothing_added_warns_and_pushes_nothing(env, qtbot):
    from ui.canvas import GrainCanvas
    from ui.canvas.edit_actions import GrainEditController
    path = make_session(env, 1, label="NoAdd")
    st = _open(qtbot, path)
    _analyse(st, qtbot)
    im = st.current_image()
    c = GrainCanvas()
    qtbot.addWidget(c)
    toasts = Toasts()
    ctl = GrainEditController(c, st, toasts)
    big = max(im.raw.grains, key=lambda g: g.area_px)
    r0, c0, r1, c1 = big.bbox
    cy, cx = (r0 + r1) // 2, (c0 + c1) // 2
    tiny = [(cx - 1, cy - 1), (cx + 1, cy - 1), (cx + 1, cy + 1)]   # inside a grain
    assert ctl.add(tiny) is None
    assert st.undo_stack.count() == 0 and im.edits == []
    title, body, sev = toasts.shown[-1]
    assert sev == "warning" and title == "No grain added" and body


def test_review_page_add_button_selects_new_grain_and_stays_active(env, qtbot):
    from ui.pages.review_page import ReviewPage
    path = make_session(env, 1, label="PageAdd")
    st = _open(qtbot, path)
    _analyse(st, qtbot)
    page = ReviewPage(st)
    qtbot.addWidget(page)
    page.resize(1400, 900)
    page.show()
    st.current_image_changed.emit(st.current_uid)
    im = st.current_image()
    assert "Add grain (A)" in page.btn_tool_add.toolTip()
    page.btn_tool_add.click()
    assert page.canvas.tool() == "add"
    QTest.keyClick(page.canvas, Qt.Key_V)
    assert page.btn_tool_select.isChecked()
    QTest.keyClick(page.canvas, Qt.Key_A)
    assert page.btn_tool_add.isChecked()
    outline = _free_block(st, im)
    page.canvas.add_requested.emit(outline)
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    gid = im.edits[-1]["id"]
    assert page.canvas.selected() == [gid] or gid in im.excluded
    assert page.canvas.tool() == "add"
    page.btn_undo.click()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.edits == []
    st.flush()
