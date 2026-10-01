"""Batch 4 workstream A (D-38): the Analyze sidebar is a tiered step wizard.

Resolution profile (optional) -> 1 Set scan area -> 2 Set scale bar ->
3 Detection mode -> 4 Start analysis -> Progress.  A step is active only
when the step before it is done (derived from the session's data); later
steps are greyed with "Finish step N first"; Edit… appears once a step is unlocked;
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
def test_steps_are_tiered_and_edit_appears_when_unlocked(env, qtbot):
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    a.sync_wizard()
    assert _states(a) == ["active", "locked", "locked", "locked"]
    assert a.btn_scan_all.isEnabled() and a.btn_scan_cur.isEnabled()
    # review item 4: Edit… is offered as soon as its step is unlocked
    assert not a.btn_scan_edit.isHidden() and a.btn_scale_edit.isHidden()
    for b in (a.btn_scale_all, a.btn_scale_cur):
        assert not b.isEnabled() and b.toolTip() == "Finish step 1 first"
    for b in (a.btn_all, a.btn_cur):
        assert not b.isEnabled() and b.toolTip() == "Finish step 3 first"
    assert all(not m.isEnabled() for m in a.params.mode_cards.values())
    # locked step 3 shows only its header row: the mode tiles are hidden
    assert a.step_mode.body.isHidden() and not a.step_mode.title.isHidden()
    assert all(not m.isVisible() for m in a.params.mode_cards.values())
    # 1 -> done: Edit… shows, step 2 lights
    _step1(shell, qtbot)
    assert all(st.scan_for(im) is not None for im in st.images())
    assert all(st.px_for(im) <= 0 for im in st.images())       # scan area only
    assert _states(a) == ["done", "active", "locked", "locked"]
    assert not a.step_scan.check.isHidden()
    assert not a.btn_scan_edit.isHidden() and a.btn_scan_edit.isEnabled()
    assert a.btn_scale_all.isEnabled() and a.btn_scale_all.toolTip() != "Finish step 1 first"
    assert not a.btn_scale_edit.isHidden()
    assert a.connectors[1].is_lit() and not a.connectors[2].is_lit()
    assert a.step_mode.body.isHidden()                         # step 3 still locked
    # 2 -> done (derived from the data: every image has a scale)
    st.set_calibration(2.0)
    a.sync_wizard()
    assert _states(a) == ["done", "done", "active", "locked"]
    assert not a.btn_scale_edit.isHidden()
    assert not a.btn_all.isEnabled()
    # step 3 reachable: the tiles appear
    assert not a.step_mode.body.isHidden()
    assert all(m.isVisible() for m in a.params.mode_cards.values())
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
    assert a.step_mode.body.isHidden()                         # tiles hidden again
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


# ---------------------------------------------------------------- code-review fixes
def _ready_all(shell, qtbot, mode="threshold"):
    a, st = shell.analyze, shell.state
    for im in st.images():
        st.set_scan_rect((0, 0, 400, 300), im.uid)
    st.set_calibration_all(2.0)
    a.params.set_mode(mode, emit=True)
    a.sync_wizard()


def test_run_buttons_gate_per_target(env, qtbot):
    """Review 1: one image without a scale -- Analyze current on a ready
    image works, Analyze all does not; each disabled button says why."""
    shell = _open(qtbot, _session(env, sizes=((480, 360),) * 3))
    a, st = shell.analyze, shell.state
    _ready_all(shell, qtbot)
    imgs = st.images()
    bad = imgs[1]
    st.session.px_per_um = 0.0
    for im in (imgs[0], imgs[2]):
        st.set_calibration(2.0, im.uid)
    bad.px_override = 0.0
    assert not st.setup_ready(bad) and st.setup_ready(imgs[0])
    st.set_current_image(imgs[0].uid)
    a.sync_wizard()
    assert a.step_run.state() != "locked"          # mode chosen + images ready
    assert a.btn_cur.isEnabled() and not a.btn_all.isEnabled()
    assert "1 of 3 images still need" in a.btn_all.toolTip()
    # switching to the image without a scale: only Analyze current changes
    states = _states(a)
    st.set_current_image(bad.uid)
    a.sync_wizard()
    assert _states(a) == states                     # switching keeps the step states
    assert not a.btn_cur.isEnabled() and "needs a scale" in a.btn_cur.toolTip()
    # Analyze selected: every ticked image must be ready
    a.film.set_checked([imgs[0].uid, imgs[2].uid])
    a.sync_wizard()
    assert not a.btn_sel.isHidden() and a.btn_sel.isEnabled()
    a.film.set_checked([imgs[0].uid, bad.uid])
    a.sync_wizard()
    assert not a.btn_sel.isEnabled() and "1 of the 2 ticked" in a.btn_sel.toolTip()
    st.set_current_image(imgs[0].uid)
    a.sync_wizard()
    with qtbot.waitSignal(a.queue.queue_finished, timeout=TIMEOUT):
        a.btn_cur.click()
    assert imgs[0].result is not None and bad.result is None
    shell.close()


def test_other_find_buttons_wait_while_auto_find_runs(env, qtbot):
    """Review 2: no misleading "Nothing to check" while a find is running."""
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    for im in st.images():
        im.scan_rect, im.scan_source = (0, 0, 480, 360), "auto"
    a.sync_wizard()
    assert a.btn_scale_all.isEnabled()
    titles = []
    orig = shell.toasts.show_toast
    shell.toasts.show_toast = lambda title, *r, **k: (titles.append(title), orig(title, *r, **k))[1]
    a.toasts = shell.toasts
    a.btn_scan_all.click()                          # step 1 again, every image
    assert st.is_setting_up()
    assert not a.btn_scale_all.isEnabled() and not a.btn_scan_cur.isEnabled()
    assert "Still finding" in a.btn_scale_all.toolTip()
    assert a.find_scales() == 0
    assert "Nothing to check" not in titles and any("Still finding" in t for t in titles)
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    a.sync_wizard()
    assert a.btn_scale_all.isEnabled() and "Still finding" not in a.btn_scale_all.toolTip()
    shell.close()


def test_full_image_for_all_counts_when_size_arrives_later(env, qtbot):
    """Review 3: "Apply to all" with the full image on an image whose size
    is not known yet still completes step 1."""
    shell = _open(qtbot, _session(env))
    a, st = shell.analyze, shell.state
    late = st.images()[1]
    shape = late.shape
    late.shape = None                               # still loading its size
    st.set_scan_rect_all(None)
    assert late.scan_rect is None and late.scan_full_pending
    late.shape = shape                              # the size arrives
    assert st.scan_for(late) == (0, 0, shape[1], shape[0])
    assert a.step_done()["scan"]
    # a later per-image area replaces the pending full frame
    st.set_scan_rect((1, 2, 30, 40), late.uid)
    assert not late.scan_full_pending and st.scan_for(late) == (1, 2, 30, 40)
    shell.close()


def test_old_session_without_wizard_flag_has_mode_chosen(env, qtbot):
    """Review 5: params saved before the wizard existed = mode chosen; a
    wizard-era save says so explicitly."""
    import json
    rec = _session(env)
    m = json.loads((rec / "manifest.json").read_text(encoding="utf-8"))
    m["detection_params"] = {"detection_mode": "boundary", "blur": 1.0}
    (rec / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    shell = _open(qtbot, rec)
    a, st = shell.analyze, shell.state
    assert st.mode_chosen() and a.step_done()["mode"]
    assert a.params.mode() == "boundary"
    shell.close()
    # a wizard-era session saved before any choice stays "not chosen"
    m["detection_params"] = {"detection_mode": "boundary", "wizard": {"mode_chosen": False}}
    (rec / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    shell = _open(qtbot, rec)
    assert not shell.state.mode_chosen()
    shell.state.set_params(shell.analyze.params.get_params())      # e.g. the GPU check
    assert shell.state.session.params["wizard"] == {"mode_chosen": False}
    shell.close()


def test_shell_edit_dialogs_apply_to_current_or_all(env, qtbot, monkeypatch):
    """Review 6: Ctrl+R / Ctrl+K "Apply to current" writes only the shown
    image; "Apply to all" writes every image."""
    from ui.calibration_dialog import CalibrationDialog
    from ui.scan_area_dialog import ScanAreaDialog
    shell = _open(qtbot, _session(env, sizes=((480, 360),) * 3))
    st = shell.state
    cur = st.current_image()
    others = [im for im in st.images() if im is not cur]
    plan = {}

    def scan_exec(self):
        self.apply_scope = plan["scope"]
        self.scan_area_set.emit(*plan["rect"])
        return 1

    def cal_exec(self):
        self.apply_scope = plan["scope"]
        self.calibration_set.emit(plan["px"])
        return 1
    monkeypatch.setattr(ScanAreaDialog, "exec", scan_exec)
    monkeypatch.setattr(CalibrationDialog, "exec", cal_exec)
    plan.update(scope="image", rect=(10, 20, 100, 80))
    shell.open_scan_area()
    qtbot.waitUntil(lambda: st.scan_for(cur) == (10, 20, 100, 80), timeout=TIMEOUT)
    assert all(st.scan_for(im) is None for im in others)
    plan.update(scope="all", rect=(5, 6, 200, 150))
    shell.open_scan_area()
    qtbot.waitUntil(lambda: all(st.scan_for(im) == (5, 6, 200, 150) for im in st.images()),
                    timeout=TIMEOUT)
    plan.update(scope="image", px=3.5)
    shell.open_calibration()
    qtbot.waitUntil(lambda: st.px_for(cur) == pytest.approx(3.5), timeout=TIMEOUT)
    assert all(st.px_for(im) <= 0 for im in others)
    plan.update(scope="all", px=4.25)
    shell.open_calibration()
    qtbot.waitUntil(lambda: all(st.px_for(im) == pytest.approx(4.25) for im in st.images()),
                    timeout=TIMEOUT)
    shell.close()


def test_wizard_buttons_blocked_while_a_run_is_in_progress(env, qtbot):
    shell = _open(qtbot, _session(env))
    a = shell.analyze
    _ready_all(shell, qtbot)
    assert a.btn_all.isEnabled()
    with qtbot.waitSignal(a.queue.queue_finished, timeout=TIMEOUT):
        a.btn_all.click()
        assert a.queue.is_running()
        for b in (a.btn_scan_all, a.btn_scan_cur, a.btn_scale_all, a.btn_scale_cur,
                  a.btn_scan_edit, a.btn_scale_edit, a.btn_cur):
            assert not b.isEnabled(), b.objectName()
        assert "Wait for the analysis" in a.btn_scale_all.toolTip()
        assert not a.btn_cancel.isHidden()
    a.sync_wizard()
    for b in (a.btn_scan_all, a.btn_scale_all, a.btn_scan_edit, a.btn_all, a.btn_cur):
        assert b.isEnabled(), b.objectName()
    shell.close()


def test_empty_session_locks_every_step(env, qtbot):
    from pathlib import Path
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(env)
    pp = ws.create_project("P")
    sp = ws.create_sample(pp, "S")
    lp = ws.create_lot(pp, sp, "L")
    rec = Path(save_session(lp, {}, [], label="Empty", catalog=Catalog(env)).path)
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.show()
    shell.open_session(rec, prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    a = shell.analyze
    a.sync_wizard()
    assert _states(a) == ["locked"] * 4 and a.profile_step.is_locked()
    assert a.step_mode.body.isHidden()
    for b in (a.btn_all, a.btn_cur, a.btn_sel):
        assert not b.isEnabled()
    assert a.btn_scan_edit.isHidden() and a.btn_scale_edit.isHidden()
    assert not any(c.is_lit() for c in a.connectors)
    shell.close()
