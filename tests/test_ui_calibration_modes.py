"""UI-11: calibration dialog measurement modes (Rectangle / Level line /
Free line), snapping, handles, tilt warning and the resulting px/µm."""
import math

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from test_scale_bar_snap import sem_with_scale_bar
from ui.calibration_dialog import (CalibrationDialog, load_calibration_mode,
                                   save_calibration_mode)

NOMOD = Qt.KeyboardModifier.NoModifier
ALT = Qt.KeyboardModifier.AltModifier


@pytest.fixture
def bar_img():
    return sem_with_scale_bar("black", length=120, thick=4)


@pytest.fixture
def make_dlg(qtbot, tmp_path, bar_img):
    prefs = tmp_path / "cal_prefs.json"

    def _make(mode=None):
        img, (x0, x1, y0, y1) = bar_img
        dlg = CalibrationDialog(img, mode=mode, prefs_path=prefs)
        qtbot.addWidget(dlg)
        dlg.resize(1000, 740)
        dlg.show()
        c = dlg.canvas
        c.set_zoom(4.0)
        c.center_on((x0 + x1) / 2, (y0 + y1) / 2)
        return dlg
    _make.prefs = prefs
    return _make


def _send(w, kind, ix, iy, mods=NOMOD, button=Qt.MouseButton.LeftButton):
    p = w._to_widget(ix, iy)
    types = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}
    buttons = button if kind != "release" else Qt.MouseButton.NoButton
    ev = QMouseEvent(types[kind], QPointF(p), QPointF(w.mapToGlobal(p.toPoint())),
                     button if kind != "move" else Qt.MouseButton.NoButton, buttons, mods)
    QApplication.sendEvent(w, ev)


def click(w, ix, iy, mods=NOMOD):
    _send(w, "press", ix, iy, mods)
    _send(w, "release", ix, iy, mods)


def drag(w, a, b, mods=NOMOD, steps=4):
    _send(w, "press", *a, mods)
    for k in range(1, steps + 1):
        t = k / steps
        _send(w, "move", a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, mods)
    _send(w, "release", *b, mods)


# ------------------------------------------------------------------ modes
def test_default_mode_is_level_line_and_mode_persists(make_dlg):
    dlg = make_dlg()
    assert dlg.mode() == "level" and dlg.mode_seg.current_text() == "Level line"
    assert dlg.mode_seg.options() == ["Rectangle", "Level line", "Free line"]
    dlg.set_mode("rect")
    assert dlg.canvas.mode() == "rect"
    assert load_calibration_mode(make_dlg.prefs) == "rect"
    assert make_dlg().mode() == "rect"                      # remembered
    save_calibration_mode("bogus", make_dlg.prefs)          # ignored
    assert load_calibration_mode(make_dlg.prefs) == "rect"


def test_corrupt_prefs_fall_back_to_level(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_calibration_mode(p) == "level"
    assert load_calibration_mode(tmp_path / "missing.json") == "level"


# ------------------------------------------------------------- level line
def test_level_line_locks_y_and_snaps_to_bar_ends(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg()
    c = dlg.canvas
    cy = (y0 + y1) / 2
    click(c, x0 + 3.2, cy + 0.4)
    assert c.point_count() == 1 and "Point 1" in dlg.lbl_dist.text()
    click(c, x1 - 2.7, cy + 9.0)                            # clicked well below the line
    (ax, ay), (bx, by) = c.points()
    assert ay == by                                         # locked horizontal
    assert (ax, bx) == (x0, x1)                             # snapped to the bar ends
    assert c.pixel_distance() == pytest.approx(x1 - x0)
    assert c.is_snapped() and dlg.badge_snap.isVisibleTo(dlg)
    assert "120.0 px" in dlg.lbl_dist.text() and "± 1.0 px" in dlg.lbl_dist.text()


def test_level_line_end_drag_stays_horizontal(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg()
    c = dlg.canvas
    cy = (y0 + y1) / 2
    click(c, x0 - 30, cy, ALT)                              # Alt: no snapping
    click(c, x1 - 30, cy + 5, ALT)
    (ax, ay), (bx, by) = c.points()
    assert (ax, bx) == pytest.approx((x0 - 30, x1 - 30)) and ay == by == pytest.approx(cy)
    assert not c.is_snapped()
    # drag the right end down-right: only x follows the mouse
    drag(c, (bx, by), (bx + 12.5, by + 20), ALT)
    (ax2, ay2), (bx2, by2) = c.points()
    assert ay2 == by2 == pytest.approx(cy)
    assert bx2 == pytest.approx(bx + 12.5)
    assert c.pixel_distance() == pytest.approx(bx2 - ax2)
    # dragging next to the bar end (no Alt) snaps onto it, with the loupe shown
    _send(c, "press", ax2, ay2)
    _send(c, "move", x0 + 4, ay2 - 30)
    assert c._loupe_active()
    assert c.points()[0][0] == x0 and c.points()[0][1] == pytest.approx(cy)
    _send(c, "release", x0 + 4, ay2 - 30)
    assert not c._loupe_active() or c.underMouse()


def test_level_line_arrow_keys_move_only_x(make_dlg, bar_img, qtbot):
    _, (x0, x1, y0, y1) = bar_img
    c = make_dlg().canvas
    c.set_points((x0, y0), (x1, y0 + 7))
    assert c.points()[1][1] == c.points()[0][1]             # set_points keeps level
    before = c.points()
    qtbot.keyClick(c, Qt.Key.Key_Right)
    qtbot.keyClick(c, Qt.Key.Key_Down)
    qtbot.keyClick(c, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    after = c.points()
    assert after[1][0] == pytest.approx(before[1][0] - 9) and after[1][1] == before[1][1]


# --------------------------------------------------------------- rectangle
def test_rectangle_counts_width_only(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg("rect")
    c = dlg.canvas
    dlg.chk_snap.setChecked(False)
    drag(c, (x0 - 20, y0 - 12), (x1 + 25, y1 + 10))
    assert c.pixel_distance() == pytest.approx((x1 + 25) - (x0 - 20))
    level_w = c.pixel_distance()
    c.reset_points()
    drag(c, (x0 - 20, y0 - 20), (x1 + 25, y1 + 3))           # a "tilted" drag
    assert c.pixel_distance() == pytest.approx(level_w)
    assert c.tilt_deg() == 0.0


def test_rectangle_snaps_to_bar_and_snap_can_be_undone(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg("rect")
    c = dlg.canvas
    drag(c, (x0 - 18, y0 - 10), (x1 + 22, y1 + 9))          # loose box
    assert c.pixel_distance() == pytest.approx(x1 - x0, abs=1)
    assert c.is_snapped() and dlg.badge_snap.isVisibleTo(dlg)
    assert dlg.btn_undo_snap.isVisibleTo(dlg)
    dlg.btn_undo_snap.click()
    assert c.pixel_distance() == pytest.approx((x1 + 22) - (x0 - 18))
    assert not c.is_snapped() and not dlg.badge_snap.isVisibleTo(dlg)
    # Alt while drawing: no snap
    c.reset_points()
    drag(c, (x0 - 18, y0 - 10), (x1 + 22, y1 + 9), ALT)
    assert not c.is_snapped()


def test_rectangle_handles_resize_move_and_nudge(make_dlg, bar_img, qtbot):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg("rect")
    c = dlg.canvas
    c.set_rect(x0 - 10, y0 - 8, x1 + 10, y1 + 8)
    w0 = c.pixel_distance()
    assert w0 == pytest.approx(x1 - x0 + 20) and f"{w0:.1f} px" in dlg.lbl_dist.text()
    rx0, ry0, rx1, ry1 = c.box()
    ym = (ry0 + ry1) / 2
    # right-edge handle, Alt: no snap -> width grows by exactly 7 px
    drag(c, (rx1, ym), (rx1 + 7, ym + 15), ALT)
    assert c.pixel_distance() == pytest.approx(w0 + 7)
    assert f"{w0 + 7:.1f} px" in dlg.lbl_dist.text()          # readout follows live
    # bottom-left corner handle changes left edge and bottom
    drag(c, (rx0, ry1), (rx0 - 5, ry1 + 6), ALT)
    assert c.box()[0] == pytest.approx(rx0 - 5) and c.box()[3] == pytest.approx(ry1 + 6)
    assert c.pixel_distance() == pytest.approx(w0 + 12)
    # top handle: height only, width unchanged
    r = c.box()
    drag(c, ((r[0] + r[2]) / 2, r[1]), ((r[0] + r[2]) / 2, r[1] - 4), ALT)
    assert c.box()[1] == pytest.approx(r[1] - 4) and c.pixel_distance() == pytest.approx(w0 + 12)
    # drag inside moves the box without changing the width
    r = c.box()
    cx, cy = (r[0] + r[2]) / 2 + 13, (r[1] + r[3]) / 2 + 1
    drag(c, (cx, cy), (cx - 6, cy + 2))
    assert c.box()[0] == pytest.approx(r[0] - 6) and c.box()[1] == pytest.approx(r[1] + 2)
    assert c.pixel_distance() == pytest.approx(w0 + 12)
    # arrow keys nudge 1 px, Shift+arrow 10 px
    r = c.box()
    qtbot.keyClick(c, Qt.Key.Key_Right)
    qtbot.keyClick(c, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    assert c.box()[0] == pytest.approx(r[0] + 1) and c.box()[1] == pytest.approx(r[1] - 10)
    assert c.pixel_distance() == pytest.approx(w0 + 12)
    # resizing without Alt snaps the vertical edges to the bar ends
    r = c.box()
    drag(c, (r[2], (r[1] + r[3]) / 2), (r[2] + 2, (r[1] + r[3]) / 2))
    assert c.is_snapped() and c.pixel_distance() == pytest.approx(x1 - x0, abs=1)


def test_mode_switch_keeps_measurement(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg()
    dlg.canvas.set_points((x0, y0 + 2), (x1, y0 + 2))
    dlg.set_mode("rect")
    assert dlg.canvas.box()[0] == x0 and dlg.canvas.pixel_distance() == pytest.approx(x1 - x0)
    dlg.set_mode("free")
    assert dlg.canvas.pixel_distance() == pytest.approx(x1 - x0)
    assert dlg.btn_apply.isEnabled()


def test_prefill_shows_box_or_line_by_mode(make_dlg, bar_img):
    from core.scale_bar import find_scale_bar_line
    img, (x0, x1, y0, y1) = bar_img
    bar = find_scale_bar_line(img)
    assert bar is not None
    for mode in ("rect", "level", "free"):
        dlg = make_dlg(mode)
        assert dlg.prefill(bar, None if mode == "rect" else 20.0)
        assert dlg.canvas.pixel_distance() == pytest.approx(x1 - x0)
        assert dlg.canvas.is_snapped() and dlg.lbl_auto.isVisibleTo(dlg)
        if mode == "rect":
            assert dlg.canvas.box() is not None and "box" in dlg.lbl_auto.text()
        else:
            assert dlg.canvas.points()[0][1] == dlg.canvas.points()[1][1]


# --------------------------------------------------------------- free line
def test_free_line_tilt_warning_and_level_it(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg("free")
    c = dlg.canvas
    L = 120.0
    # 2 deg tilt -> warning
    click(c, x0, y0)
    click(c, x0 + L, y0 + L * math.tan(math.radians(2.0)))
    assert c.tilt_deg() == pytest.approx(2.0, abs=0.01)
    assert c.pixel_distance() == pytest.approx(L / math.cos(math.radians(2.0)))
    assert dlg.tilt_warning_shown() and "2.0°" in dlg.lbl_tilt.text()
    assert dlg.btn_level.isVisibleTo(dlg)
    # 0.9 deg -> readout, no warning
    c.set_points((x0, y0), (x0 + L, y0 + L * math.tan(math.radians(0.9))))
    assert dlg.lbl_tilt.isVisibleTo(dlg) and not dlg.tilt_warning_shown()
    # 1.1 deg -> warning (threshold 1 deg)
    c.set_points((x0, y0), (x0 + L, y0 - L * math.tan(math.radians(1.1))))
    assert dlg.tilt_warning_shown()
    dlg.btn_level.click()
    assert c.tilt_deg() == pytest.approx(0.0) and c.pixel_distance() == pytest.approx(L)
    assert not dlg.tilt_warning_shown()
    # large tilt: no "Level it" (the bar is really tilted)
    c.set_points((x0, y0), (x0 + 50, y0 + 40))
    assert dlg.tilt_warning_shown() and not dlg.btn_level.isVisibleTo(dlg)
    # tilt readout only in free mode
    dlg.set_mode("level")
    assert not dlg.lbl_tilt.isVisibleTo(dlg)


def test_free_line_end_drag_moves_freely(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    c = make_dlg("free").canvas
    c.set_points((x0, y0), (x1, y0))
    drag(c, (x1, y0), (x1 + 3, y0 + 4))
    assert c.points()[1] == pytest.approx((x1 + 3, y0 + 4))


# ------------------------------------------------------------ calibration
@pytest.mark.parametrize("mode", ["rect", "level", "free"])
def test_px_per_um_is_correct_in_each_mode(make_dlg, bar_img, mode):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg(mode)
    c = dlg.canvas
    if mode == "rect":
        drag(c, (x0 - 15, y0 - 9), (x1 + 15, y1 + 9))        # snaps to 120 px
    elif mode == "level":
        click(c, x0 + 2, (y0 + y1) / 2)
        click(c, x1 - 2, (y0 + y1) / 2 + 3)
    else:
        c.set_points((x0, y0), (x1, y0))
    assert c.pixel_distance() == pytest.approx(120.0)
    dlg.length_spin.setValue(20.0)
    dlg.unit_combo.setCurrentText("µm")
    assert dlg.px_per_um() == pytest.approx(6.0)
    assert "6.0000" in dlg.lbl_result.text() and "0.16667 µm/px" in dlg.lbl_result.text()
    dlg.unit_combo.setCurrentText("nm")
    dlg.length_spin.setValue(20000.0)
    assert dlg.px_per_um() == pytest.approx(6.0)
    got = []
    dlg.calibration_set.connect(got.append)
    dlg._apply()
    assert got == [pytest.approx(6.0)]


def test_reset_clears_measurement(make_dlg, bar_img):
    _, (x0, x1, y0, y1) = bar_img
    dlg = make_dlg()
    dlg.canvas.set_points((x0, y0), (x1, y0))
    assert dlg.btn_apply.isEnabled()
    dlg.btn_reset.click()
    assert dlg.canvas.point_count() == 0 and not dlg.btn_apply.isEnabled()
    assert dlg.px_per_um() is None and not dlg.badge_snap.isVisibleTo(dlg)
