"""
UPDATE 4 item 9: overlay-opacity pill in the top-right corner of the image
on Analyze and Review (shown in Overlay only; live; one value for both pages,
remembered across images and restarts).

UPDATE 4 item 13: the Review page lists images in the same Job > Part > Lot
tree as Analyze (ImageTree, browse-only), selection / Up-Down still work.

Review follow-ups: tiny scale-bar lengths never round to 0 (a); the
"enter the length" pulse waits for the row to be on screen (b).
"""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from tests.test_ui_shell_analysis import _analyse_all, _open_shell, env  # noqa: E402,F401
from tests.test_ui_ux_v301 import TIMEOUT, _lots, _shell  # noqa: E402
from tests.ui_shell_helpers import make_session  # noqa: E402


# ---------------------------------------------------------------- item 9: canvas level
def _fake_result(h=120, w=160):
    lab = np.zeros((h, w), np.int32)
    lab[20:60, 20:70] = 1
    lab[70:110, 90:150] = 2
    grains = [SimpleNamespace(grain_id=1, centroid_x=45.0, centroid_y=40.0, bbox=(20, 20, 60, 70)),
              SimpleNamespace(grain_id=2, centroid_x=120.0, centroid_y=90.0,
                              bbox=(70, 90, 110, 150))]
    return SimpleNamespace(label_image=lab, grains=grains, valid_mask=None)


def _canvas(qtbot):
    from ui.canvas import GrainCanvas
    from ui.design.theme import set_reduced_motion
    set_reduced_motion(True)
    c = GrainCanvas()
    qtbot.addWidget(c)
    c.resize(640, 480)
    c.enable_opacity_control()
    c.show()
    bgr = np.full((120, 160, 3), 128, np.uint8)
    c.set_image(bgr, _fake_result())
    return c


def test_pill_only_in_overlay_view_and_top_right(qapp, qtbot):
    c = _canvas(qtbot)
    pill = c.opacity_pill
    try:
        c.set_view("original")
        assert not pill.is_shown() and not pill.isVisible()
        c.set_view("overlay")
        assert pill.is_shown() and pill.isVisible()
        g = pill.geometry()
        assert g.right() > c.width() - 30 and g.top() <= 16          # top-right corner
        c.resize(800, 500)
        assert pill.geometry().right() > 800 - 30                    # follows resizes
        for v in ("mask", "excluded"):
            c.set_view(v)
            assert not pill.is_shown()
        c.set_view("overlay")
        c.set_image(None)                                            # nothing to draw
        assert not pill.is_shown()
        # accessible + tooltips
        assert pill.slider.accessibleName() == "Overlay opacity"
        assert pill.toolTip() and pill.slider.toolTip()
        assert pill.slider.focusPolicy() & Qt.TabFocus
    finally:
        from ui.design.theme import set_reduced_motion
        set_reduced_motion(False)


def test_pill_value_reaches_canvas_and_changes_the_image(qapp, qtbot):
    c = _canvas(qtbot)
    try:
        c.set_view("overlay")
        c.set_overlay_opacity(1.0)
        full = c.grab().toImage()
        edits = []
        c.overlay_opacity_edited.connect(lambda v, final: edits.append((v, final)))
        c.opacity_pill.slider.setValue(20)                           # user drags / steps
        assert c.overlay_opacity() == pytest.approx(0.20)
        assert edits and edits[0] == (pytest.approx(0.20), False)    # live
        assert c.opacity_pill.readout.text() == "20 %"
        faint = c.grab().toImage()
        assert faint != full                                         # the overlay faded
        c.opacity_pill.slider.setValue(0)
        assert c.grab().toImage() not in (full, faint)
        # settles -> committed once
        qtbot.waitUntil(lambda: any(f for _v, f in edits), timeout=3000)
        # keyboard: Right arrow steps 5 %
        c.opacity_pill.slider.setFocus()
        QTest.keyClick(c.opacity_pill.slider, Qt.Key_Right)
        assert c.overlay_opacity() == pytest.approx(0.05)
        # programmatic sync does not echo back as a user edit
        n = len(edits)
        c.set_overlay_opacity(0.6)
        assert c.opacity_pill.value() == pytest.approx(0.6) and len(edits) == n
    finally:
        from ui.design.theme import set_reduced_motion
        set_reduced_motion(False)


# ---------------------------------------------------------------- item 9: pages
@pytest.fixture
def analysed3(env, qtbot):  # noqa: F811
    shell = _open_shell(qtbot, make_session(env, 3, label="Run", black=True))
    _analyse_all(shell, qtbot)
    yield shell
    shell.close()


def _pick(shell, qtbot, uid):
    shell.state.set_current_image(uid)
    qtbot.waitUntil(lambda: shell.state.current_uid == uid, timeout=5000)


def test_opacity_persists_across_images_pages_and_restart(analysed3, qtbot):
    from ui.app_state import AppState, load_ui_state
    shell = analysed3
    st = shell.state
    uids = [im.uid for im in st.images()]
    a, r = shell.analyze, shell.review
    shell.go("analyze")
    _pick(shell, qtbot, uids[0])
    a.view_seg.set_current_index(1)                                  # Overlay
    assert a.canvas.opacity_pill.is_shown()
    a.view_seg.set_current_index(0)                                  # Original -> hidden
    assert not a.canvas.opacity_pill.is_shown()
    a.view_seg.set_current_index(1)
    a.canvas.opacity_pill.slider.setValue(35)
    a.canvas.opacity_pill.slider.sliderReleased.emit()               # drag ends
    assert st.overlay_opacity == pytest.approx(0.35)
    assert not hasattr(a, "opacity")                                 # batch 4: no side slider
    assert load_ui_state()["overlay_opacity"] == pytest.approx(0.35)
    for uid in (uids[1], uids[2]):                                   # image switches
        _pick(shell, qtbot, uid)
        assert a.canvas.overlay_opacity() == pytest.approx(0.35)
        assert a.canvas.opacity_pill.value() == pytest.approx(0.35)
    shell.go("review")                                               # other page
    assert r.canvas.overlay_opacity() == pytest.approx(0.35)
    r.view_seg.set_current_index(1)
    assert r.canvas.opacity_pill.is_shown()
    r.view_seg.set_current_index(2)                                  # Mask -> hidden
    assert not r.canvas.opacity_pill.is_shown()
    r.view_seg.set_current_index(1)
    r.canvas.opacity_pill.slider.setValue(70)
    r.canvas.opacity_pill.slider.sliderReleased.emit()
    assert a.canvas.overlay_opacity() == pytest.approx(0.70)         # back on Analyze
    assert a.canvas.opacity_pill.value() == pytest.approx(0.70)
    assert AppState().overlay_opacity == pytest.approx(0.70)         # next start
    # the display-mode memory (item 14) is untouched
    _pick(shell, qtbot, uids[0])
    assert r.canvas.view() == "overlay" and r._view_pref == "overlay"


# ---------------------------------------------------------------- item 13
def test_review_tree_groups_by_job_part_lot(env, qtbot):  # noqa: F811
    from ui.pages.image_tree import ROLE_KIND, ImageTree
    sample = _lots(env, 3, 2)
    shell = _shell(qtbot)
    shell.load_into_analyzer([sample])
    st = shell.state
    qtbot.waitUntil(lambda: st.session is not None and st.session.multi
                    and not st.is_loading(), timeout=TIMEOUT)
    shell.go("review")
    tree = shell.review.film
    assert isinstance(tree, ImageTree)
    kinds = sorted({g.data(0, ROLE_KIND) for g in tree.groups().values()})
    assert kinds == ["lot", "project", "sample"]                     # Job > Part > Lot
    lots = [g for g in tree.groups().values() if g.data(0, ROLE_KIND) == "lot"]
    assert len(lots) == 3 and all(g.childCount() == 2 for g in lots)
    # browse-only: no tick boxes, no add / remove / context menu
    assert not tree.checkable and not tree.manage
    assert not tree.add_btn.isVisible() and not tree.restore_btn.isVisible()
    assert tree.tree.contextMenuPolicy() == Qt.NoContextMenu
    # Analyze keeps its tick boxes
    assert shell.analyze.film.checkable and shell.analyze.film.manage

    order = tree.image_order()
    assert sorted(order, key=str) == sorted((im.uid for im in st.images()), key=str)
    # clicking a row drives the canvas
    it = tree.item(order[3])
    tree.tree.scrollToItem(it)
    QTest.mouseClick(tree.tree.viewport(), Qt.LeftButton, pos=tree.tree.visualItemRect(it).center())
    qtbot.waitUntil(lambda: st.current_uid == order[3], timeout=5000)
    assert tree.tree.currentItem() is it
    # Down / Up step image to image across lot folders (group rows skipped)
    tree.tree.setFocus()
    QTest.keyClick(tree.tree, Qt.Key_Down)
    qtbot.waitUntil(lambda: st.current_uid == order[4], timeout=5000)
    tree.set_current(order[1])
    st.set_current_image(order[1])
    QTest.keyClick(tree.tree, Qt.Key_Down)                           # lot 1 -> lot 2
    qtbot.waitUntil(lambda: st.current_uid == order[2], timeout=5000)
    QTest.keyClick(tree.tree, Qt.Key_Up)
    qtbot.waitUntil(lambda: st.current_uid == order[1], timeout=5000)
    # a collapsed folder opens when stepping into it
    lot3 = tree.item(order[4]).parent()
    lot3.setExpanded(False)
    tree.set_current(order[3])
    st.set_current_image(order[3])
    tree.step(+1)
    qtbot.waitUntil(lambda: st.current_uid == order[4], timeout=5000)
    assert lot3.isExpanded()
    # ends clamp
    tree.set_current(order[-1])
    tree.step(+1)
    assert tree._current == order[-1]
    # current image chosen elsewhere is mirrored in the tree
    st.set_current_image(order[0])
    qtbot.waitUntil(lambda: tree.tree.currentItem() is tree.item(order[0]), timeout=5000)
    shell.close()


def test_review_tree_status_updates(analysed3, qtbot):
    from ui.pages.image_tree import ROLE_LINE2, ROLE_TONE
    shell = analysed3
    shell.go("review")
    tree = shell.review.film
    for im in shell.state.images():
        it = tree.item(im.uid)
        assert it.data(0, ROLE_TONE) == "success" and "grains" in it.data(0, ROLE_LINE2)


# ---------------------------------------------------------------- follow-ups a / b
def test_tiny_bar_length_is_not_rounded_to_zero(qapp, qtbot):
    from ui.calibration_dialog import length_decimals_for, set_length_value, split_length_um
    from ui.pages.analyze_page import ScaleLengthRow
    v, unit = split_length_um(0.0000004)                              # 0.0004 nm
    assert unit == "nm" and v == pytest.approx(0.0004)
    assert length_decimals_for(50.0) == 3 and length_decimals_for(0.0004) >= 6
    t = ScaleLengthRow()
    qtbot.addWidget(t)
    t.set_bar_length_um(0.0000004)
    assert t.bar_len.value() > 0 and t.bar_len.text() != t.bar_len.specialValueText()
    assert t.bar_length_um() == pytest.approx(0.0000004, rel=1e-3)
    t.set_bar_length_um(20.0)                                        # everyday value: 3 dp
    assert t.bar_len.decimals() == 3 and t.bar_len.value() == 20.0
    # calibration dialog box keeps a tiny value too (its minimum was 0.001)
    from PySide6.QtWidgets import QDoubleSpinBox
    spin = QDoubleSpinBox()
    spin.setRange(0.001, 100000.0)
    spin.setDecimals(3)
    set_length_value(spin, 0.0004)
    assert spin.value() == pytest.approx(0.0004)


def _row(qtbot):
    """Batch 4: the length row (wizard step 2) inside a host standing in
    for its page."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from ui.pages.analyze_page import ScaleLengthRow
    host = QWidget()
    t = ScaleLengthRow()
    QVBoxLayout(host).addWidget(t)
    t.hide()
    qtbot.addWidget(host)
    t.host = host
    return t


def test_attention_waits_until_the_row_is_on_screen(qapp, qtbot):
    from ui.widgets.attention import AttentionRing
    t = _row(qtbot)
    t.host.resize(900, 200)
    t.bar_row.show()                        # row wanted, but its page is not shown yet
    assert not t.bar_row.isVisible()
    assert t.draw_attention_to_length()     # deferred, not dropped
    assert t.attention_pending()
    assert getattr(t.bar_len, "_attention_ring", None) is None
    t.host.show()                           # page appears -> pulse
    qtbot.waitUntil(lambda: isinstance(getattr(t.bar_len, "_attention_ring", None),
                                       AttentionRing), timeout=3000)
    assert not t.attention_pending()
    # a hidden row (nothing to enter) never queues a pulse
    t2 = _row(qtbot)
    assert not t2.draw_attention_to_length() and not t2.attention_pending()
    t2.host.show()
    t2.bar_row.show()
    qtbot.wait(50)
    assert getattr(t2.bar_len, "_attention_ring", None) is None


def test_attention_ring_timer_attribute_defined(qapp, qtbot):
    from PySide6.QtWidgets import QLineEdit, QWidget
    from ui.design.theme import set_reduced_motion
    from ui.widgets.attention import AttentionRing
    host = QWidget()
    qtbot.addWidget(host)
    box = QLineEdit(host)
    host.show()
    set_reduced_motion(False)
    ring = AttentionRing(box, 50)
    assert ring._timer is None               # animated path: attribute exists anyway
    ring.finish()


# ---------------------------------------------------------------- review fixes
def test_pill_clicks_never_reach_the_canvas(qapp, qtbot):
    from PySide6.QtCore import QPoint
    c = _canvas(qtbot)
    pill = c.opacity_pill
    try:
        c.set_view("overlay")
        c.select([1])
        assert c.selected() == [1]
        spot = QPoint(6, pill.height() // 2)                          # pill padding, not slider
        assert pill.childAt(spot) is None
        QTest.mouseClick(pill, Qt.LeftButton, pos=spot)
        assert c.selected() == [1]                                    # selection kept
        QTest.mouseClick(pill.readout, Qt.LeftButton)
        assert c.selected() == [1]
        QTest.mouseDClick(pill, Qt.LeftButton, pos=spot)
        assert c.selected() == [1]
        for tool in ("lasso", "split"):                               # no stroke starts
            c.set_tool(tool)
            QTest.mousePress(pill, Qt.LeftButton, pos=spot)
            QTest.mouseMove(pill, QPoint(spot.x() + 40, spot.y() + 5))
            assert not c._drawing and c.stroke() == []
            QTest.mouseRelease(pill, Qt.LeftButton, pos=spot)
            assert c.selected() == [1]
        c.set_tool("select")
        # fading out: click-through, so the image underneath gets the click
        assert not pill.is_click_through()
        c.set_view("original")
        assert pill.is_click_through()
        c.set_view("overlay")
        assert not pill.is_click_through()
    finally:
        from ui.design.theme import set_reduced_motion
        set_reduced_motion(False)


def test_pill_drag_does_not_commit_before_release(qapp, qtbot):
    c = _canvas(qtbot)
    try:
        c.set_view("overlay")
        pill = c.opacity_pill
        got = []
        pill.value_committed.connect(got.append)
        pill.slider.setValue(40)                    # keyboard-style change arms the timer
        assert pill._commit.isActive()
        pill.slider.sliderPressed.emit()            # user grabs the handle
        assert not pill._commit.isActive()
        pill.slider.setSliderDown(True)
        pill.slider.setValue(50)
        qtbot.wait(COMMIT_WAIT)
        assert got == []                            # nothing written mid-drag
        pill.slider.setSliderDown(False)
        pill.slider.sliderReleased.emit()
        assert got and got[-1] == pytest.approx(0.5)
    finally:
        from ui.design.theme import set_reduced_motion
        set_reduced_motion(False)


COMMIT_WAIT = 600


def test_deferred_attention_is_cancelled_and_expires(qapp, qtbot, monkeypatch):
    t = _row(qtbot)
    t.bar_row.show()
    assert t.draw_attention_to_length() and t.attention_pending()
    t.cancel_attention()                                    # image / scale changed
    assert not t.attention_pending()
    t.host.show()
    qtbot.wait(50)
    assert getattr(t.bar_len, "_attention_ring", None) is None
    # expiry: a pulse requested long before the row shows is dropped
    t2 = _row(qtbot)
    t2._attention_expiry.setInterval(30)
    t2.bar_row.show()
    assert t2.draw_attention_to_length()
    qtbot.waitUntil(lambda: not t2.attention_pending(), timeout=2000)
    t2.host.show()
    qtbot.wait(50)
    assert getattr(t2.bar_len, "_attention_ring", None) is None
    # the tile going away with a pulse queued is harmless (timer owned by the tile)
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    host3 = QWidget()
    from ui.pages.analyze_page import ScaleLengthRow
    t3 = ScaleLengthRow()
    QVBoxLayout(host3).addWidget(t3)
    t3.bar_row.show()
    t3.draw_attention_to_length()
    host3.show()
    host3.deleteLater()
    qtbot.wait(50)


def test_analyze_page_cancels_deferred_attention_on_image_change(analysed3, qtbot):
    shell = analysed3
    tile = shell.analyze.scale_row
    shell.go("review")                                      # Analyze not on screen
    tile.bar_row.show()
    assert tile.draw_attention_to_length() and tile.attention_pending()
    uids = [im.uid for im in shell.state.images()]
    _pick(shell, qtbot, uids[1] if shell.state.current_uid == uids[0] else uids[0])
    assert not tile.attention_pending()
    tile.bar_row.show()
    tile.draw_attention_to_length()
    shell.state.calibration_changed.emit()
    assert not tile.attention_pending()
