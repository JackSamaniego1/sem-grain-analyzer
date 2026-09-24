"""
UI-05 / INN-04 on the review canvas: lasso select, merge, split — undoable,
re-measured, persisted with the session (edited labels + edit list +
detector labels) and honoured by the saved (exported) result.
"""
import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from data.models import AppSettings, read_json  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import make_session  # noqa: E402

TIMEOUT = 90000


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)


def _open(qtbot, path):
    from ui.app_state import AppState
    st = AppState()
    st.open_session(path)
    qtbot.waitUntil(lambda: st.session is not None, timeout=15000)
    return st


def _analyse(st, qtbot):
    from core.grain_detector import DetectionParams
    from ui.workers import analyze_image
    for im in st.images():
        raw = analyze_image(im.image_bgr, st.px_for(im), DetectionParams(detection_mode="threshold"),
                            st.scan_for(im))
        st.set_result(im.uid, raw)
    qtbot.waitUntil(lambda: all(i.status == "done" for i in st.images()) and not st.is_filtering(),
                    timeout=TIMEOUT)


def _idle(st, qtbot):
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    st.flush()
    qtbot.waitUntil(lambda: st.save_state == "saved" and not st.is_dirty(), timeout=TIMEOUT)


def _touching_pair(st, uid):
    """Two kept grains that the merge accepts."""
    from core.grain_edit import GrainEditError, merge_grains
    im = st.session.image(uid)
    lab = im.raw.label_image
    kept = st.kept_ids(uid)
    a, b = lab[:, :-2].ravel(), lab[:, 2:].ravel()
    pairs = {(int(x), int(y)) for x, y in zip(a, b) if x != y and x in kept and y in kept}
    for x, y in sorted(pairs):
        try:
            merge_grains(lab, [x, y])
            return x, y
        except GrainEditError:
            continue
    pytest.skip("no touching grain pair in the synthetic image")


def _big_grain(st, uid):
    im = st.session.image(uid)
    kept = st.kept_ids(uid)
    return max((g for g in im.raw.grains if g.grain_id in kept), key=lambda g: g.area_px)


def test_merge_split_undo_persist_and_export(env, qtbot):
    path = make_session(env, 1, label="Edits")
    st = _open(qtbot, path)
    st.set_calibration(2.0)
    _analyse(st, qtbot)
    im = st.current_image()
    n0 = im.result.grain_count
    g0 = im.result.astm_g
    a, b = _touching_pair(st, im.uid)
    area_ab = sum(g.area_px for g in im.raw.grains if g.grain_id in (a, b))

    gid = st.merge_grains(im.uid, [a, b])
    assert gid == min(a, b)
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count == n0 - 1
    merged = next(g for g in im.result.grains if g.grain_id == gid)
    assert merged.area_px >= area_ab
    assert merged.area_um2 == pytest.approx(merged.area_px / 4.0)
    assert max(a, b) not in {g.grain_id for g in im.result.grains}
    assert im.edits and im.edits[0]["op"] == "merge"
    assert im.detector_labels is not None and (im.detector_labels == max(a, b)).any()

    big = _big_grain(st, im.uid)
    r0, c0, r1, c1 = big.bbox
    cx = (c0 + c1) / 2.0
    pieces = st.split_grain(im.uid, [(cx, r0 - 3), (cx, r1 + 3)])
    assert pieces[0] == big.grain_id and len(pieces) >= 2
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count >= n0            # -1 merge, +1 (or more) split
    ids = {g.grain_id: g for g in im.raw.grains}
    assert sum(ids[p].area_px for p in pieces) == big.area_px

    # undo both, redo both
    st.undo_stack.undo()
    st.undo_stack.undo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count == n0 and im.edits == [] and im.detector_labels is None
    st.undo_stack.redo()
    st.undo_stack.redo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    n_edit = im.result.grain_count
    assert [e["op"] for e in im.edits] == ["merge", "split"]
    _idle(st, qtbot)

    # --- persisted: manifest edit list, labels.npz (edited + detector), grains.json
    m = read_json(path / "manifest.json")
    e0 = next(e for e in m["images"] if e["filename"] == im.filename)
    assert [x["op"] for x in e0["grain_edits"]] == ["merge", "split"]
    stem = im.filename.rsplit(".", 1)[0]
    with np.load(path / "results" / f"{stem}.labels.npz") as npz:
        assert "detector_label_image" in npz
        assert not (npz["label_image"] == max(a, b)).any()
        assert (npz["detector_label_image"] == max(a, b)).any()
    saved = read_json(path / "results" / f"{stem}.grains.json")["grains"]
    assert len(saved) == n_edit and any(g["grain_id"] == gid for g in saved)
    summ = read_json(path / "results" / f"{stem}.summary.json")
    assert summ["grain_count"] == n_edit
    if g0 is not None:
        assert summ["astm_g"] is not None

    # --- reopen: the edits survive and stay undoable-consistent
    st2 = _open(qtbot, path)
    im2 = st2.current_image()
    assert im2.result.grain_count == n_edit
    assert [x["op"] for x in im2.edits] == ["merge", "split"]
    assert im2.detector_labels is not None
    assert max(a, b) not in {g.grain_id for g in im2.raw.grains}
    st2.flush()


def test_merge_rejects_far_apart_grains(env, qtbot):
    from core.grain_edit import GrainEditError
    path = make_session(env, 1, label="Far")
    st = _open(qtbot, path)
    _analyse(st, qtbot)
    im = st.current_image()
    kept = sorted(st.kept_ids(im.uid), key=lambda i: next(
        g.centroid_x + g.centroid_y for g in im.raw.grains if g.grain_id == i))
    with pytest.raises(GrainEditError):
        st.merge_grains(im.uid, [kept[0], kept[-1]])
    with pytest.raises(GrainEditError):
        st.split_grain(im.uid, [(1.0, 1.0), (1.5, 1.5)])
    assert st.undo_stack.count() == 0


def test_lasso_then_delete_is_one_undo_step(env, qtbot):
    from ui.canvas import GrainCanvas
    path = make_session(env, 1, label="Lasso")
    st = _open(qtbot, path)
    _analyse(st, qtbot)
    im = st.current_image()
    c = GrainCanvas()
    qtbot.addWidget(c)
    c.resize(700, 700)
    c.show()
    c.set_image(im.image_bgr, im.result, raw=im.raw, excluded=im.excluded)
    h, w = im.image_bgr.shape[:2]
    ids = c.lasso_select([(0, 0), (w * 0.6, 0), (w * 0.6, h * 0.6), (0, h * 0.6)])
    assert len(ids) >= 3 and not set(ids) & set(im.excluded)
    n0 = im.result.grain_count
    st.delete_grains(im.uid, ids)
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count == n0 - len(ids)
    st.undo_stack.undo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count == n0
    st.flush()


# ---------------------------------------------------------------- canvas gestures
def _canvas_with_grid(qtbot):
    from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector
    from ui.canvas import GrainCanvas
    lab = np.zeros((100, 100), dtype=np.int32)
    k = 1
    for r in range(3):
        for col in range(3):
            y, x = 3 + r * 31, 3 + col * 31
            lab[y:y + 30, x:x + 30] = k
            k += 1
    res = AnalysisResult(label_image=lab, valid_mask=np.ones(lab.shape, bool))
    res.grains = GrainDetector()._measure_grains(
        lab, DetectionParams(min_grain_size_px=0, max_grain_size_px=0), 0.0)
    bgr = np.full((100, 100, 3), 90, np.uint8)
    c = GrainCanvas()
    qtbot.addWidget(c)
    c.resize(420, 420)
    c.show()
    c.set_image(bgr, res, raw=res, excluded={})
    return c


def _drag(c, pts_img, mods=Qt.NoModifier):
    def w(pt):
        return QPoint(int(round(c._offset.x() + pt[0] * c._scale)),
                      int(round(c._offset.y() + pt[1] * c._scale)))
    QTest.mousePress(c, Qt.LeftButton, mods, w(pts_img[0]))
    for pt in pts_img[1:]:
        QTest.mouseMove(c, w(pt))
    QTest.mouseRelease(c, Qt.LeftButton, mods, w(pts_img[-1]))


def test_canvas_lasso_gesture_selects_grains_inside(qtbot):
    c = _canvas_with_grid(qtbot)
    QTest.keyClick(c, Qt.Key_L)
    assert c.tool() == "lasso"
    _drag(c, [(1, 1), (40, 1), (68, 1), (68, 66), (30, 66), (1, 66), (1, 30)])
    assert sorted(c.selected()) == [1, 2, 4, 5]
    _drag(c, [(66, 66), (99, 66), (99, 99), (66, 99)], Qt.ControlModifier)   # Ctrl adds
    assert sorted(c.selected()) == [1, 2, 4, 5, 9]
    QTest.keyClick(c, Qt.Key_Escape)
    assert c.tool() == "select"


def test_canvas_cut_gesture_emits_image_coordinates(qtbot):
    c = _canvas_with_grid(qtbot)
    c.zoom_by(1.7)                      # non-trivial scale + offset
    got = []
    c.split_requested.connect(got.append)
    QTest.keyClick(c, Qt.Key_C)
    assert c.tool() == "split"
    _drag(c, [(49, 25), (49, 45), (49, 70)])
    assert got, "cut line not emitted"
    xs = [p[0] for p in got[0]]
    ys = [p[1] for p in got[0]]
    assert all(abs(x - 49) < 1.5 for x in xs)
    assert min(ys) < 30 and max(ys) > 66
    from core.grain_edit import grain_under_line
    assert grain_under_line(c._labels, got[0]) == 5


def test_canvas_merge_key_needs_two(qtbot):
    c = _canvas_with_grid(qtbot)
    got = []
    c.merge_requested.connect(got.append)
    c.select([1])
    QTest.keyClick(c, Qt.Key_M)
    assert got == []
    c.select([1, 2])
    QTest.keyClick(c, Qt.Key_M)
    assert got == [[1, 2]]


def test_review_page_tools_and_merge_button(env, qtbot):
    from ui.pages.review_page import ReviewPage
    path = make_session(env, 1, label="Page")
    st = _open(qtbot, path)
    _analyse(st, qtbot)
    page = ReviewPage(st)
    qtbot.addWidget(page)
    page.resize(1400, 900)
    page.show()
    st.current_image_changed.emit(st.current_uid)
    im = st.current_image()
    n0 = im.result.grain_count
    a, b = _touching_pair(st, im.uid)
    page.btn_tool_lasso.click()
    assert page.canvas.tool() == "lasso"
    QTest.keyClick(page.canvas, Qt.Key_V)
    assert page.btn_tool_select.isChecked()
    page.canvas.select([a, b])
    assert page.btn_merge.isEnabled() and page.btn_merge.text() == "Merge 2"
    page.btn_merge.click()
    assert page.canvas.selected() == [min(a, b)]          # merged grain highlighted
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count == n0 - 1
    assert page.gmodel.rowCount() == n0 - 1
    page.btn_undo.click()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert im.result.grain_count == n0
    st.flush()
