"""Batch 4 workstream A (D-38): the Analyze sidebar is a tiered step wizard.

Resolution profile (optional) -> 1 Set scan area -> 2 Set scale bar ->
3 Detection mode -> 4 Start analysis -> Progress.  A step is active only
when the step before it is done (derived from the session's data); later
steps are greyed with "Finish step N first"; Edit… appears once a step is
done; the tile under the image is a read-only details strip.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")

from tests.test_ui_resolution_profiles import TIMEOUT, _open, _session, env  # noqa: E402,F401

NEW_NAMES = ("wizard_profile", "wizard_step_scan", "wizard_step_scale", "wizard_step_mode",
             "wizard_step_run", "wizard_progress", "scan_all", "scan_current", "scan_edit",
             "scale_all", "scale_current", "scale_edit", "run_all", "run_current",
             "run_selected")


def _states(a):
    return [c.state() for c in a.steps]


def _step1(shell, qtbot):
    a, st = shell.analyze, shell.state
    a.btn_scan_all.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    a.sync_wizard()


# ---------------------------------------------------------------- the widget
def test_step_card_states_tooltips_and_check(qapp, qtbot):
    from ui.design.theme import set_reduced_motion
    from ui.widgets import AnimatedButton, StepCard, StepConnector
    set_reduced_motion(True)
    try:
        card = StepCard(2, "Set scale bar")
        qtbot.addWidget(card)
        b = AnimatedButton("All images")
        b.setToolTip("Find the scale")
        card.add_widget(b)
        card.register(b)
        assert card.is_locked() and not card.body.isEnabled() and not b.isEnabled()
        assert b.toolTip() == "Finish step 1 first" and card.check.isHidden()
        card.set_state("active")
        assert card.body.isEnabled() and b.toolTip() == "Find the scale"
        assert card.lit() == pytest.approx(1.0)
        card.set_state("done")
        assert not card.check.isHidden() and card.body.isEnabled()   # still clickable
        card.set_state("locked")
        assert card.lit() == pytest.approx(0.0) and card.check.isHidden()
        with pytest.raises(ValueError):
            card.set_state("finished")
        c = StepConnector()
        qtbot.addWidget(c)
        assert not c.is_lit()
        c.set_lit(True)
        assert c.is_lit()
    finally:
        set_reduced_motion(False)


# ---------------------------------------------------------------- layout
def test_sidebar_order_names_and_progress_last(env, qtbot):
    from PySide6.QtWidgets import QWidget
    from ui.widgets import StepCard, StepConnector
    shell = _open(qtbot, _session(env))
    a = shell.analyze
    for name in NEW_NAMES:
        assert shell.findChild(QWidget, name) is not None, name
    lay = a.wizard_layout
    widgets = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget()]
    assert widgets[-1] is a.progress_card                      # progress at the bottom
    cards = [w.objectName() for w in widgets if isinstance(w, StepCard)]
    assert cards == ["wizard_profile", "wizard_step_scan", "wizard_step_scale",
                     "wizard_step_mode", "wizard_step_run"]
    # an arrow connector between every two neighbours
    for prev, nxt in zip(widgets, widgets[1:]):
        assert isinstance(prev, StepConnector) != isinstance(nxt, StepConnector)
    # the profile card sits in the optional first step; ParamPanel in step 3
    assert a.profiles_card.parentWidget().parentWidget() is a.profile_step
    assert a.params.parentWidget().parentWidget() is a.step_mode
    assert a.filters_host.parentWidget() is a.params.advanced.content_widget()
    shell.close()


def test_removed_widgets_and_old_names_are_gone(env, qtbot):
    from PySide6.QtWidgets import QAbstractButton, QWidget
    shell = _open(qtbot, _session(env))
    a = shell.analyze
    for attr in ("sec_cal", "sec_scan", "sec_overlay", "opacity", "opacity_val", "scan_this",
                 "btn_scan_clear", "cal_badge", "scan_lbl"):
        assert not hasattr(a, attr), attr
    for attr in ("sec_invalid", "inv_thr", "show_invalid"):
        assert not hasattr(a.params, attr), attr
    for name in ("tourCalibration", "tourAutoFind", "tourAnalyzeAll", "tourDetectionMode"):
        assert shell.findChild(QWidget, name) is None, name
    # the details strip under the image: read-only, no buttons at all
    assert a.setup_tile.findChildren(QAbstractButton) == []
    assert not hasattr(a.setup_tile, "btn_auto") and not hasattr(a.setup_tile, "bar_row")
    # the canvas opacity pill stays
    assert a.canvas.opacity_pill is not None
    shell.close()


# ---------------------------------------------------------------- gating
def test_steps_are_tiered_and_edit_appears_only_when_done(env, qtbot):
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    a.sync_wizard()
    assert _states(a) == ["active", "locked", "locked", "locked"]
    assert a.btn_scan_all.isEnabled() and a.btn_scan_cur.isEnabled()
    assert a.btn_scan_edit.isHidden() and a.btn_scale_edit.isHidden()
    for b in (a.btn_scale_all, a.btn_scale_cur):
        assert not b.isEnabled() and b.toolTip() == "Finish step 1 first"
    for b in (a.btn_all, a.btn_cur):
        assert not b.isEnabled() and b.toolTip() == "Finish step 3 first"
    assert all(not m.isEnabled() for m in a.params.mode_cards.values())
    # 1 -> done: Edit… shows, step 2 lights
    _step1(shell, qtbot)
    assert all(st.scan_for(im) is not None for im in st.images())
    assert all(st.px_for(im) <= 0 for im in st.images())       # scan area only
    assert _states(a) == ["done", "active", "locked", "locked"]
    assert not a.step_scan.check.isHidden()
    assert not a.btn_scan_edit.isHidden() and a.btn_scan_edit.isEnabled()
    assert a.btn_scale_all.isEnabled() and a.btn_scale_all.toolTip() != "Finish step 1 first"
    assert a.btn_scale_edit.isHidden()
    assert a.connectors[1].is_lit() and not a.connectors[2].is_lit()
    # 2 -> done (derived from the data: every image has a scale)
    st.set_calibration(2.0)
    a.sync_wizard()
    assert _states(a) == ["done", "done", "active", "locked"]
    assert not a.btn_scale_edit.isHidden()
    assert not a.btn_all.isEnabled()
    # 3 -> choosing a mode marks it done and lights step 4
    a.params.mode_cards["boundary"].clicked.emit()
    a.sync_wizard()
    assert st.session.params.get("detection_mode") == "boundary" and st.mode_chosen()
    assert _states(a) == ["done", "done", "done", "active"]
    assert a.btn_all.isEnabled() and a.btn_cur.isEnabled()
    # switching images never resets the wizard
    other = [im for im in st.images() if im.uid != st.current_uid][0]
    st.set_current_image(other.uid)
    a.sync_wizard()
    assert _states(a) == ["done", "done", "done", "active"]
    # a later change that un-does step 1 greys the steps after it again
    st.set_scan_rect_all(None)
    for im in st.images():
        im.scan_rect = None
    a.sync_wizard()
    assert _states(a)[0] == "active" and _states(a)[1:] == ["locked"] * 3
    assert not a.btn_all.isEnabled()
    shell.close()


def test_current_image_buttons_only_touch_the_shown_image(env, qtbot):
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    a.sync_wizard()
    cur = st.current_image()
    a.btn_scan_cur.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    a.sync_wizard()
    others = [im for im in st.images() if im is not cur]
    assert st.scan_for(cur) is not None and all(st.scan_for(im) is None for im in others)
    assert a.step_scan.is_active()                              # not every image yet
    assert "1 of 2" in a.step_scan.status.text()
    assert not a.btn_scan_edit.isHidden()                       # it was tried: Edit… offered
    shell.close()


def test_scale_only_find_keeps_the_scan_area(env, qtbot):
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    for im in st.images():
        im.scan_rect, im.scan_source = (3, 4, 100, 90), "auto"
    a.sync_wizard()
    assert a.step_scale.is_active()
    a.btn_scale_all.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    assert all(im.scan_rect == (3, 4, 100, 90) for im in st.images())
    a.sync_wizard()
    assert not a.btn_scale_edit.isHidden()      # nothing found: Edit… is the way on
    shell.close()


def test_gpu_check_alone_does_not_choose_the_mode(env, qtbot):
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    assert not st.mode_chosen()                     # defaults filled in, nothing chosen
    a.params._device_update = True
    try:
        a.params.changed.emit()
    finally:
        a.params._device_update = False
    a.sync_wizard()
    assert not st.mode_chosen() and not a.step_done()["mode"]
    # the operator's pick is saved with the session (and survives a reopen)
    a.params.set_mode("threshold", emit=True)
    assert st.mode_chosen() and st.session.params["wizard"] == {"mode_chosen": True}
    a.params.blur.setValue(2.5)                     # later edits keep the marker
    assert st.mode_chosen()
    shell.close()


def test_profile_pick_fills_steps_1_and_2_and_lights_step_3(env, qtbot):
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    card = a.profiles_card
    card.set_enabled(True)
    im = st.current_image()
    st.set_scan_rect((10, 20, 400, 300), im.uid)
    assert card.create_profile("Zeiss 5kx", 20.0, "µm", 100.0) is not None
    st.set_scan_rect(None, im.uid)
    im.scan_rect = None
    a.sync_wizard()
    assert _states(a)[:3] == ["active", "locked", "locked"]
    card.combo.activated.emit(0)                       # the operator picks the profile
    a.sync_wizard()
    for x in st.images():
        assert st.px_for(x) == pytest.approx(5.0) and st.scan_for(x) == (10, 20, 400, 300)
    assert _states(a)[:3] == ["done", "done", "active"]
    assert all(m.isEnabled() for m in a.params.mode_cards.values() if m.available)
    shell.close()


# ---------------------------------------------------------------- Edit… dialogs
def test_scan_dialog_apply_to_current_or_all_and_full_image(qapp, qtbot):
    import numpy as np
    from ui.scan_area_dialog import ScanAreaDialog
    img = np.zeros((120, 160, 3), np.uint8)
    dlg = ScanAreaDialog(img, current_rect=(10, 10, 50, 40))
    qtbot.addWidget(dlg)
    assert dlg.canvas.get_rect() == (10, 10, 50, 40)              # shows the current area
    assert dlg.btn_apply_image.text() == "Apply to current"
    assert dlg.btn_apply.text() == "Apply to all"
    got = []
    dlg.scan_area_set.connect(lambda *r: got.append(r))
    dlg.btn_full.click()                                         # selects, does not apply
    assert got == [] and dlg.canvas.get_rect() == (0, 0, 160, 120)
    dlg.btn_apply_image.click()
    assert got == [(0, 0, 160, 120)] and dlg.apply_scope == "image"
    dlg2 = ScanAreaDialog(img, current_rect=(5, 5, 60, 60))
    qtbot.addWidget(dlg2)
    dlg2.scan_area_set.connect(lambda *r: got.append(r))
    dlg2.btn_apply.click()
    assert got[-1] == (5, 5, 60, 60) and dlg2.apply_scope == "all"


def test_calibration_dialog_offers_metadata_scale(qapp, qtbot):
    import numpy as np
    from ui.calibration_dialog import CalibrationDialog
    dlg = CalibrationDialog(np.zeros((100, 100, 3), np.uint8), mode="level")
    qtbot.addWidget(dlg)
    assert dlg.btn_meta.isHidden()
    dlg.offer_metadata_scale(7.75, "Zeiss")
    assert not dlg.btn_meta.isHidden() and "7.75" in dlg.btn_meta.text()
    with qtbot.waitSignal(dlg.metadata_requested, timeout=1000):
        dlg.btn_meta.click()


# ---------------------------------------------------------------- 1366 × 768
def test_headless_1366x768_nothing_clipped(env, qtbot, tmp_path):
    from PySide6.QtWidgets import QAbstractButton, QLabel, QSizePolicy
    shell = _open(qtbot, _session(env))
    a = shell.analyze
    shell.resize(1366, 768)
    qtbot.wait(300)
    _step1(shell, qtbot)
    qtbot.wait(200)
    shell.grab().save(str(tmp_path / "analyze_1366.png"))
    side = a.side_scroll
    inner = side.widget()
    assert inner.width() <= side.viewport().width()             # never scrolls sideways
    bad = []
    for root in (inner, a.setup_tile):
        for w in root.findChildren(QAbstractButton):
            if w.isVisibleTo(root) and w.text():
                need = w.fontMetrics().horizontalAdvance(w.text().replace("&", ""))
                if need + 8 > w.width():
                    bad.append(("button", w.text(), need, w.width()))
        for w in root.findChildren(QLabel):
            if (not w.isVisibleTo(root) or w.wordWrap() or not w.text() or w.pixmap()
                    and not w.pixmap().isNull()):
                continue
            if w.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored:
                continue                                         # elides by design
            if w.sizeHint().width() > w.width() + 1:
                bad.append(("label", w.text(), w.sizeHint().width(), w.width()))
    assert not bad, bad
    shell.close()
