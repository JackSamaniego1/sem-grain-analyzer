"""
Guided tour (UI-10, rebuilt in batch 4 / D-39): action-driven steps over a
bundled Tutorial job.

Auto-start rules, "don't show" persistence, Skip / Esc / Help › Show tour,
no Next / Back buttons, the Tutorial job (created once, reset when already
analysed, assets bundled and loadable, auto-find works on them), each step
advancing from AppState / page signals, missing-anchor skipping, target
resolution and resize-following, and a headless end-to-end run performing
every action.  Offscreen; settings live in tmp_path.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QRect, QRectF, Qt  # noqa: E402

from data.models import AppSettings  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import make_session  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 120000


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


def _make_shell(qtbot, tour=None):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    w = AppShell(AppState(), probe_device=False, tour=tour)
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    return w


@pytest.fixture
def shell(env, qtbot):
    w = _make_shell(qtbot)
    yield w
    w.close()


def _overlays(shell):
    from ui.tour.overlay import TourOverlay
    return [o for o in shell.findChildren(TourOverlay) if o.isVisible()]


def _stored_flag():
    from ui.app_state import load_ui_state
    return bool(load_ui_state().get("tour", {}).get("dont_show", False))


def _key(t):
    return t.current_step().key if t.is_active() else None


def _start(shell, qtbot):
    """Help › Show tour, wait for the Tutorial job, then press Start."""
    t = shell.tour
    with qtbot.waitSignal(t.tutorial_ready, timeout=TIMEOUT):
        shell.act_tour.trigger()
    assert _key(t) == "welcome"
    qtbot.mouseClick(t.overlay.callout.btn_primary, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "open_job", timeout=5000)
    return t


def _projects(root):
    from data.workspace import Workspace
    return [p.name for p in Workspace(root).list_projects()]


# ---------------------------------------------------------------------- start-up
def test_first_run_starts_tour_when_enabled(env, qtbot):
    w = _make_shell(qtbot, tour=True)
    qtbot.waitUntil(lambda: w.tour.is_active(), timeout=3000)
    assert w.tour.index == 0 and w.tour.current_step().kind == "welcome"
    ov = w.tour.overlay
    assert ov.isVisible() and ov.geometry() == w.rect()
    assert ov.callout.dont_show.isVisible()           # offered on the welcome card
    assert ov.callout.btn_primary.text() == "Start tour"
    assert ov.callout.btn_skip.isVisible()
    w.close()


def test_tour_never_autostarts_under_tests(shell, qtbot):
    qtbot.wait(1000)
    assert not shell.tour.is_active()
    assert not _overlays(shell)


def test_dont_show_persists_and_blocks_autostart(env, qtbot):
    w = _make_shell(qtbot, tour=True)
    qtbot.waitUntil(lambda: w.tour.is_active(), timeout=3000)
    w.tour.overlay.callout.dont_show.setChecked(True)
    assert _stored_flag() is True                      # saved immediately, locally
    qtbot.mouseClick(w.tour.overlay.callout.btn_skip, Qt.LeftButton)
    assert not w.tour.is_active()
    w.close()
    w2 = _make_shell(qtbot, tour=True)
    qtbot.wait(1100)
    assert w2.tour.dont_show() and not w2.tour.is_active()
    # Help › Show tour replays whatever the checkbox says
    w2.act_tour.trigger()
    assert w2.tour.is_active() and w2.tour.index == 0
    assert w2.tour.overlay.callout.dont_show.isChecked()
    w2.tour.overlay.callout.dont_show.setChecked(False)
    assert _stored_flag() is False
    w2.close()


# ---------------------------------------------------------------------- no Next / Back
def test_no_next_or_back_buttons(shell, qtbot):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton
    from ui.tour.overlay import TourCallout, TourOverlay
    for name in ("btn_next", "btn_back"):
        assert not hasattr(TourCallout, name)
    for sig in ("next_requested", "back_requested"):
        assert not hasattr(TourOverlay, sig)
    from ui.tour import TourController
    assert not hasattr(TourController, "next") and not hasattr(TourController, "back")
    t = _start(shell, qtbot)
    c = t.overlay.callout
    texts = [b.text() for b in c.findChildren(QAbstractButton) if b.isVisible()]
    assert texts == ["Skip tour"], texts                # action step: Skip only
    n = len([s for s in t.steps if not s.centred])
    assert c.step_lbl.text() == f"STEP 1 OF {n}" and n == 22
    assert not c.dont_show.isVisible()
    for k in (Qt.Key_Right, Qt.Key_PageDown, Qt.Key_Return, Qt.Key_Left):
        QTest.keyClick(t.overlay, k)
    assert _key(t) == "open_job"                        # keys never advance an action step
    t.finish()


STEP_KEYS = ["welcome", "open_job", "scan", "scale", "mode", "run",
             "go_review", "select", "merge", "split", "add", "delete", "undo", "opacity",
             "view", "filter",
             "outline", "reorder", "inspector", "preview", "compare", "excel",
             "export", "finish"]


def test_steps_are_the_spec_list_each_with_a_target_and_a_hook():
    from ui.tour import default_steps
    steps = default_steps()
    assert [s.key for s in steps] == STEP_KEYS
    for s in steps[1:-1]:
        assert 1 <= len(s.targets) <= 2, s.key          # one control (or an arrow pair)
        if s.kind == "info":
            assert s.advance_on is None                 # "Got it" only
        else:
            assert s.advance_on is not None and s.kind == "click", s.key
    info = [s.key for s in steps if s.kind == "info"]
    assert info == ["preview", "compare"]               # few "look at this" steps
    by = {s.key: s for s in steps}
    assert by["scan"].targets == ("scan_all",) and by["scale"].targets == ("scale_all",)
    assert by["run"].targets == ("run_all",) and by["export"].targets == ("tourExportPptx",)
    assert by["go_review"].targets == ("tourRail_review",) and by["go_review"].page is None
    for k, t in (("merge", "tourToolMerge"), ("split", "tourToolSplit"),
                 ("add", "tourToolAdd"), ("delete", "tourToolDelete"),
                 ("undo", "tourToolUndo"), ("view", "tourReviewView")):
        assert by[k].targets == (t,) and "tourReviewCanvas" in by[k].areas, k
    # the shortcut is in the text of every Review tool step
    for k, key in (("select", "V"), ("merge", "M"), ("split", "C"), ("add", "A"),
                   ("delete", "Delete"), ("undo", "Ctrl+Z")):
        assert key in by[k].text("body", None), k


def test_tour_texts_use_american_spelling():
    """Regression guard (user request): no British spellings in the tour."""
    import re

    from tests.test_american_spelling import BRITISH, string_literals
    for p in (ROOT / "ui" / "tour").glob("*.py"):
        for line, text in string_literals(p):
            assert not re.search(r"(?i)analys(e|ed|es|ing)\b", text), (p.name, line, text)
            assert not BRITISH.search(text), (p.name, line, text)

# ---------------------------------------------------------------------- Tutorial job
def test_assets_bundled_and_loadable():
    import cv2
    from core.resources import TUTORIAL_IMAGES, resource_path, tutorial_images
    paths = tutorial_images()
    assert [p.name for p in paths] == list(TUTORIAL_IMAGES)
    assert sum(p.stat().st_size for p in paths) < 3_000_000
    for p in paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        assert img is not None and img.shape[:2] == (960, 1280)
    assert resource_path("assets", "tutorial").is_dir()
    spec = (ROOT / "grain_analyzer.spec").read_text(encoding="utf-8")
    assert "('assets/tutorial', 'assets/tutorial')" in spec and "*tutorial_datas" in spec


def test_frozen_bundle_path_is_searched_first(tmp_path, monkeypatch):
    import sys
    from core import resources
    d = tmp_path / "assets" / "tutorial"
    d.mkdir(parents=True)
    for n in resources.TUTORIAL_IMAGES:
        (d / n).write_bytes(b"x")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.tutorial_images()[0].parent == d


def test_missing_assets_say_reinstall_never_download(tmp_path, monkeypatch):
    from core import resources
    monkeypatch.setattr(resources, "_HERE", tmp_path)
    with pytest.raises(FileNotFoundError) as e:
        resources.tutorial_images()
    msg = str(e.value).lower()
    assert "reinstall" in msg and "http" not in msg and "download" not in msg


def test_autofind_succeeds_on_the_tutorial_images():
    import cv2
    from core.resources import tutorial_images
    from ui.app_state import setup_probe
    for p in tutorial_images():
        out = setup_probe(cv2.imread(str(p)), str(p), want_meta=False, want_ocr=True)
        assert out["info"], p.name                       # JEOL info bar -> scan area
        assert abs(out["bar_px"] - 160) <= 3, p.name     # 10 µm bar, 16 px/µm
        assert out["ocr"]["um"] == pytest.approx(10.0), p.name


def test_tutorial_job_created_once_and_reset_when_used(env):
    from data.tutorial import LOT, PROJECT, SAMPLE, ensure_tutorial_job, is_pristine
    a = ensure_tutorial_job(env)
    b = ensure_tutorial_job(env)
    assert a["created"] and not b["created"] and a["record"] == b["record"]
    assert _projects(env) == [PROJECT]
    rec = Path(a["record"])
    assert rec.name == LOT and rec.parent.name == SAMPLE
    assert len(list((rec / "images").iterdir())) == 3
    assert is_pristine(rec, 3)
    # the user ran the tutorial before: a scale was saved -> reset to fresh
    m = json.loads((rec / "manifest.json").read_text(encoding="utf-8"))
    m["images"][0]["px_per_um"], m["images"][0]["has_calibration"] = 16.0, True
    (rec / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (rec / "exports").mkdir(exist_ok=True)
    (rec / "exports" / "keep.txt").write_text("mine", encoding="utf-8")
    assert not is_pristine(rec, 3)
    c = ensure_tutorial_job(env)
    assert c["reset"] and c["record"] == a["record"] and is_pristine(rec, 3)
    assert (rec / "exports" / "keep.txt").exists()       # the user's exports are kept
    assert _projects(env) == [PROJECT]
    assert len(list((rec / "images").iterdir())) == 3


def test_tour_start_creates_job_once_and_selects_it(shell, qtbot, env):
    t = shell.tour
    for _ in range(2):
        with qtbot.waitSignal(t.tutorial_ready, timeout=TIMEOUT):
            shell.start_tour()
        t.skip()
    assert _projects(env) == ["Tutorial"]
    assert t.tutorial_record is not None and t.tutorial_record.is_dir()
    node = shell.state.current_node
    assert node is not None and Path(node.path) == t.tutorial_record


def _spot_on(shell, t, name):
    from PySide6.QtWidgets import QWidget
    w = shell.findChild(QWidget, name)
    if w is None or not w.isVisible():
        return False
    c = QRectF(QRect(w.mapTo(shell, w.rect().topLeft()), w.size())).center()
    return t.overlay.spot().contains(c)


def _clickable(t, shell, w):
    """``w``'s centre is not covered by the scrim (spotlight or work area)."""
    c = w.mapTo(shell, w.rect().center())
    return not t.overlay.mask().contains(c)


def _grains(shell):
    import numpy as np
    st = shell.state
    im = st.current_image()
    lab = im.raw.label_image
    kept = st.kept_ids(im.uid)
    return im, lab, kept, np


def _touching_pair(shell):
    im, lab, kept, np = _grains(shell)
    for d in (1, 2, 3, 4):
        a, b = lab[:, :-d], lab[:, d:]
        m = (a > 0) & (b > 0) & (a != b)
        for x, y in zip(a[m][:400], b[m][:400]):
            if int(x) in kept and int(y) in kept:
                yield [int(x), int(y)]


def _big_grain(shell):
    im, lab, kept, np = _grains(shell)
    ids, counts = np.unique(lab[lab > 0], return_counts=True)
    order = [int(i) for i in ids[np.argsort(-counts)] if int(i) in kept]
    gid = order[0]
    ys, xs = np.nonzero(lab == gid)
    cy = int(np.median(ys))
    row = xs[ys == cy]
    return gid, [(float(row.min() - 3), float(cy)), (float(row.max() + 3), float(cy))]


def _do_review_actions(shell, t, qtbot, by_ui=True):
    """Perform every Review step's action on the tutorial image."""
    rv, st = shell.review, shell.state
    cv = rv.canvas
    # select
    assert _key(t) == "select" and _spot_on(shell, t, "tourToolSelect")
    assert _clickable(t, shell, cv)                         # the image is a work area
    body = QRectF(t.overlay.callout.geometry()).adjusted(14, 14, -14, -14)
    cvr = QRectF(QRect(cv.mapTo(shell, cv.rect().topLeft()), cv.size()))
    assert not body.intersects(cvr)                         # callout keeps off the image
    gid, line = _big_grain(shell)
    cv.select([gid])
    qtbot.waitUntil(lambda: _key(t) == "merge", timeout=5000)
    # merge any two touching grains
    for pair in _touching_pair(shell):
        cv.select(pair)
        if rv.edits.merge() is not None:
            break
    qtbot.waitUntil(lambda: _key(t) == "split", timeout=TIMEOUT)
    # cut a grain
    gid, line = _big_grain(shell)
    cv.split_requested.emit(line)
    qtbot.waitUntil(lambda: _key(t) == "add", timeout=TIMEOUT)
    # add: trying it is enough (every pixel already belongs to a grain here)
    h, w = st.current_image().shape[:2]
    cv.add_requested.emit([(w * 0.4, h * 0.4), (w * 0.45, h * 0.4), (w * 0.45, h * 0.45)])
    qtbot.waitUntil(lambda: _key(t) == "delete", timeout=TIMEOUT)
    # delete any grain
    gid, _line = _big_grain(shell)
    cv.select([gid])
    rv.delete_selected()
    qtbot.waitUntil(lambda: _key(t) == "undo", timeout=TIMEOUT)
    shell.act_undo.trigger()                                # Ctrl+Z
    qtbot.waitUntil(lambda: _key(t) in ("opacity", "view"), timeout=TIMEOUT)
    if _key(t) == "opacity":
        assert _spot_on(shell, t, "tourOpacityPill")
        cv.overlay_opacity_edited.emit(0.5, True)           # the pill, dragged
        qtbot.waitUntil(lambda: _key(t) == "view", timeout=5000)
    rv.view_seg.set_current_index(0)                        # Original
    qtbot.waitUntil(lambda: _key(t) == "filter", timeout=5000)
    rv.view_seg.set_current_index(1)
    assert _clickable(t, shell, rv.filters.border.switch)
    rv.filters.border.switch.click()                        # a filter switch
    qtbot.waitUntil(lambda: _key(t) == "outline", timeout=TIMEOUT)


def _do_report_actions(shell, t, qtbot):
    from PySide6.QtTest import QTest
    rp = shell.reports
    assert shell.current_page() == "reports"
    qtbot.waitUntil(lambda: rp.model is not None and rp.outline.topLevelItemCount() > 2,
                    timeout=TIMEOUT)
    qtbot.waitUntil(lambda: _spot_on(shell, t, "tourReportOutline"), timeout=5000)
    # untick a section in the outline
    sec = next(s for s in rp.model.sections if s.enabled and s.id not in ("raw", "images"))
    rp.outline.toggled.emit(("section", sec.id), False)
    qtbot.waitUntil(lambda: _key(t) == "reorder", timeout=5000)
    rp.outline.toggled.emit(("section", sec.id), True)
    assert _clickable(t, shell, rp.outline)                 # select a section to move it
    rp.select(("image", rp.model.images[0].id))           # images can be reordered
    rp.btn_down.click()
    qtbot.waitUntil(lambda: _key(t) == "inspector", timeout=5000)
    rp.inspector.title.setFocus()
    QTest.keyClicks(rp.inspector.title, " (tutorial)")
    qtbot.waitUntil(lambda: _key(t) == "preview", timeout=5000)
    for key in ("preview", "compare"):                      # "Got it" cards
        c = t.overlay.callout
        assert c.btn_primary.isVisible() and c.btn_primary.text() == "Got it"
        assert c.btn_skip.isVisible()
        qtbot.mouseClick(c.btn_primary, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "excel", timeout=5000)
    rp.btn_xlsx.click()
    qtbot.waitUntil(lambda: _key(t) == "export", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: rp.btn_pptx.isEnabled(), timeout=TIMEOUT)
    assert _spot_on(shell, t, "tourExportPptx")
    rp.btn_pptx.click()
    qtbot.waitUntil(lambda: _key(t) == "finish", timeout=TIMEOUT)


def _setup_and_run(shell, t, qtbot):
    st, a = shell.state, shell.analyze
    assert shell.current_page() == "projects" and _spot_on(shell, t, "tourNewSession")
    assert shell.projects.btn_primary.text().startswith("Open")
    assert not t.overlay.mask().contains(t.overlay.spot().center().toPoint())  # clicks reach it
    qtbot.mouseClick(shell.projects.btn_primary, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "scan", timeout=TIMEOUT)
    assert Path(st.session.path) == t.tutorial_record and len(st.images()) == 3
    qtbot.waitUntil(lambda: not st.is_loading(), timeout=TIMEOUT)
    a.sync_wizard()
    assert shell.current_page() == "analyze" and _spot_on(shell, t, "scan_all")
    a.btn_scan_all.click()
    qtbot.waitUntil(lambda: _key(t) == "scale", timeout=TIMEOUT)
    # one click: the label read off each image is applied; the strip shows it
    assert "strip under the image" in t.overlay.callout.body.text()
    assert _clickable(t, shell, a.setup_tile)               # the strip is left clear
    qtbot.waitUntil(lambda: a.btn_scale_all.isEnabled(), timeout=5000)
    a.btn_scale_all.click()
    qtbot.waitUntil(lambda: _key(t) == "mode", timeout=TIMEOUT)
    assert all(st.px_for(im) == pytest.approx(16.0, rel=0.02) for im in st.images())
    assert a.setup_tile.scale_val.text().startswith("Scale bar: 10 µm · 160 px → 16 px/µm")
    assert a.setup_tile.scale_src.text() == "Read from image"
    assert not a.scale_row.isVisible()                      # nothing to confirm
    assert not a.step_mode.body.isHidden()
    a.params.mode_cards["boundary"].clicked.emit()
    qtbot.waitUntil(lambda: _key(t) == "run", timeout=5000)
    assert _spot_on(shell, t, "run_all")
    a.btn_all.click()
    qtbot.waitUntil(lambda: a.queue.is_running() and _spot_on(shell, t, "wizard_progress") or
                    _key(t) != "run", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: _key(t) == "go_review", timeout=TIMEOUT)
    assert all(im.result is not None for im in st.images())
    # the Review nav item, not the canvas: the page opens when the user clicks it
    assert shell.current_page() == "analyze" and _spot_on(shell, t, "tourRail_review")
    qtbot.mouseClick(shell.rail.item("review"), Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "select", timeout=5000)
    assert shell.current_page() == "review"
    qtbot.waitUntil(lambda: shell.review.canvas._labels is not None, timeout=TIMEOUT)


# ---------------------------------------------------------------------- end to end
def test_end_to_end_by_doing_each_action(shell, qtbot, env):
    """Every step advances from the action itself: open the job, auto-find
    scan area and scale, choose a mode, analyze, review tools, report
    designer tools, Excel and PowerPoint export."""
    t = _start(shell, qtbot)
    keys, done = [], []
    t.step_changed.connect(lambda i: keys.append(t.steps[i].key))
    t.finished.connect(done.append)
    _setup_and_run(shell, t, qtbot)
    _do_review_actions(shell, t, qtbot)
    _do_report_actions(shell, t, qtbot)
    assert list((t.tutorial_record / "exports").glob("*.pptx"))
    assert list((t.tutorial_record / "exports").glob("*.xlsx"))
    c = t.overlay.callout
    assert c.btn_primary.text() == "Done" and not c.btn_skip.isVisible()
    qtbot.mouseClick(c.btn_primary, Qt.LeftButton)
    assert done == [True] and not t.is_active()
    assert keys == STEP_KEYS[2:]
    assert _projects(env) == ["Tutorial"]
    qtbot.waitUntil(lambda: not _overlays(shell), timeout=2000)


def test_steps_advance_on_signals_in_order(shell, qtbot):
    """Same walk with the AppState / page signals emitted directly (the user
    did each action another way, e.g. from the menu)."""
    st, a = shell.state, shell.analyze
    t = _start(shell, qtbot)
    shell.open_session(t.tutorial_record)                 # e.g. from search / tree
    qtbot.waitUntil(lambda: _key(t) == "scan", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: not st.is_loading(), timeout=TIMEOUT)
    for im in st.images():
        im.scan_rect = (0, 0, 1280, 896)
    st.setup_changed.emit()
    qtbot.waitUntil(lambda: _key(t) == "scale", timeout=5000)
    st.set_calibration_all(16.0)                          # Ctrl+K "Apply to all"
    qtbot.waitUntil(lambda: _key(t) == "mode", timeout=5000)
    a.params.set_mode("threshold", emit=True)
    qtbot.waitUntil(lambda: _key(t) == "run", timeout=5000)
    with qtbot.waitSignal(a.queue.queue_finished, timeout=TIMEOUT):
        shell.act_run_all.trigger()                       # F5
    qtbot.waitUntil(lambda: _key(t) == "go_review", timeout=TIMEOUT)
    shell.go("review")                                    # Ctrl+3
    qtbot.waitUntil(lambda: _key(t) == "select", timeout=5000)
    for key in ("select", "merge", "split", "add", "delete", "undo", "opacity", "view",
                "filter", "toggle", "order", "edit"):
        t.note(key)
    qtbot.waitUntil(lambda: _key(t) == "preview", timeout=TIMEOUT)
    qtbot.mouseClick(t.overlay.callout.btn_primary, Qt.LeftButton)
    qtbot.mouseClick(t.overlay.callout.btn_primary, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "excel", timeout=5000)
    shell.reports.exported.emit(["x.pptx"])               # not the workbook: stays
    qtbot.wait(50)
    assert _key(t) == "excel"
    shell.reports.exported.emit(["book.xlsx"])
    qtbot.waitUntil(lambda: _key(t) == "finish", timeout=5000)   # pptx already seen
    t.finish()


def test_out_of_order_actions_never_dead_end(shell, qtbot):
    """An action done before its step (here: every Review edit while the
    analysis step is still open) completes that step when it comes up."""
    from ui.tour import TourController
    t = TourController(shell)
    t.start(prepare=False)
    t.seen.clear()
    for key in ("review_page", "select", "merge", "split", "add_try", "delete", "undo"):
        t.note(key)
    keys = [s.key for s in t.steps]
    i = keys.index("go_review")
    t.start(i, prepare=False)
    shell.go("review")
    # every step whose action was already done is passed by itself
    qtbot.waitUntil(lambda: not t.is_active() or t.index > keys.index("undo"), timeout=5000)
    t.finish()


def test_review_edit_kinds_are_told_apart(shell, qtbot):
    """watch_actions classifies the undo stack: merge / split / add /
    delete / undo of a grain edit -- a scale undo is not a grain undo."""
    from PySide6.QtGui import QUndoCommand

    from ui.tour import TourController
    t = TourController(shell)
    t.start(prepare=False)
    t.seen.clear()
    stack = shell.state.undo_stack

    class GrainGeometryCommand(QUndoCommand):
        pass

    class ExcludeGrainsCommand(QUndoCommand):
        pass

    class ScaleCommand(QUndoCommand):
        pass
    for cls, text in ((GrainGeometryCommand, "Merge 2 grains"),
                      (GrainGeometryCommand, "Split grain #4"),
                      (GrainGeometryCommand, "Add grain #9"), (ExcludeGrainsCommand, "x"),
                      (ScaleCommand, "Set scale")):
        stack.push(cls(text))
    assert {"merge", "split", "add", "delete"} <= t.seen and "undo" not in t.seen
    stack.undo()                                            # the scale: not a grain edit
    assert "undo" not in t.seen
    stack.undo()
    assert "undo" in t.seen
    stack.clear()
    t.finish()


def test_tutorial_reset_at_start_when_already_analysed(shell, qtbot, env):
    from data.tutorial import ensure_tutorial_job
    rec = Path(ensure_tutorial_job(env)["record"])
    shell.open_session(rec)
    qtbot.waitUntil(lambda: shell.state.session is not None and not shell.state.is_loading(),
                    timeout=TIMEOUT)
    shell.state.set_calibration_all(16.0)
    shell.state.flush()
    t = shell.tour
    with qtbot.waitSignal(t.tutorial_ready, timeout=TIMEOUT):
        shell.start_tour()
    assert shell.state.session is None                    # closed for the reset
    qtbot.mouseClick(t.overlay.callout.btn_primary, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "open_job", timeout=5000)
    shell.open_session(rec)
    qtbot.waitUntil(lambda: _key(t) == "scan", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: not shell.state.is_loading(), timeout=TIMEOUT)
    qtbot.wait(100)
    assert _key(t) == "scan"                              # actionable again, not skipped
    assert all(shell.state.px_for(im) <= 0 for im in shell.state.images())
    t.finish()


def test_already_done_step_advances_at_once(shell, qtbot):
    from ui.tour import TourController, TourStep
    hits = []
    steps = [TourStep("w", "Welcome", "hi", kind="welcome"),
             TourStep("d", "Done", "x", kind="click", page="projects",
                      targets=("tourProjectsTree",), done=lambda sh: True,
                      advance_on=lambda sh, tour: (lambda: hits.append("off"))),
             TourStep("f", "Bye", "bye", kind="finish")]
    t = TourController(shell, steps)
    t.start(prepare=False)
    t.advance()
    qtbot.waitUntil(lambda: _key(t) == "f", timeout=2000)
    assert hits == ["off"]                                # hook disconnected on leaving
    t.finish()


# ---------------------------------------------------------------------- skip
def test_skip_button_and_escape_close_with_undo_toast(shell, qtbot, env):
    t = _start(shell, qtbot)
    done = []
    t.finished.connect(done.append)
    qtbot.mouseClick(t.overlay.callout.btn_skip, Qt.LeftButton)
    assert done == [False] and not t.is_active()
    qtbot.waitUntil(lambda: not _overlays(shell), timeout=2000)
    assert _projects(env) == ["Tutorial"]                 # skipping leaves the job in place
    # Esc also skips
    shell.start_tour()
    from PySide6.QtTest import QTest
    QTest.keyClick(t.overlay, Qt.Key_Escape)
    assert done == [False, False] and not t.is_active()
    from ui.widgets.toast import Toast
    toasts = [x for x in shell.findChildren(Toast) if x.isVisible()]
    assert toasts
    t.start(1, prepare=False)                             # Undo resumes at the same step
    assert t.index == 1
    t.finish()


# ---------------------------------------------------------------------- anchors
def test_missing_anchor_is_skipped(shell):
    from ui.tour import TourController, TourStep
    steps = [
        TourStep("w", "Welcome", "hi", kind="welcome"),
        TourStep("ghost", "Ghost", "gone", targets=("noSuchWidget",)),
        TourStep("real", "Tree", "tree", page="projects", targets=("tourProjectsTree",)),
        TourStep("f", "Done", "bye", kind="finish"),
    ]
    t = TourController(shell, steps)
    t.start(prepare=False)
    t.advance()
    assert t.index == 2                                # ghost skipped
    t.start(1, prepare=False)                          # starting on it moves forward
    assert t.index == 2
    t.finish()
    assert not t.is_active()


def test_hidden_target_uses_fallback(shell):
    from ui.tour import TourController, TourStep
    from ui.tour.steps import rail_anchor
    step = TourStep("run", "Run", "x", page="analyze", targets=("run_all",),
                    fallbacks=((rail_anchor("analyze"),),))
    t = TourController(shell, [step])
    t.start()                                          # no session: Analyze shows its empty state
    assert t.index == 0 and shell.current_page() == "analyze"
    rail = shell.rail.item("analyze")
    spot = t.overlay.spot()
    r = QRectF(QRect(rail.mapTo(shell, rail.rect().topLeft()), rail.size()))
    assert spot.contains(r.center())
    t.finish()


def test_every_step_target_exists(shell):
    from PySide6.QtWidgets import QWidget
    from ui.tour import default_steps
    for step in default_steps():
        for names in (step.targets,) + tuple(step.fallbacks):
            for name in names:
                assert shell.findChild(QWidget, name) is not None, (step.key, name)


# ---------------------------------------------------------------------- layout
def test_resize_follows_target_and_callout_avoids_it(shell, qtbot):
    from ui.tour import TourController, TourStep
    t = TourController(shell, [TourStep("tree", "Tree", "x", page="projects",
                                        targets=("tourProjectsTree",)),
                               TourStep("f", "Bye", "bye", kind="finish")])
    t.start(prepare=False)
    ov = t.overlay
    for size in ((1200, 740), (1700, 1000), (1300, 820)):
        shell.resize(*size)
        qtbot.wait(300)                                # > follow interval
        assert ov.geometry() == shell.rect()
        tree = shell.projects.tree
        r = QRectF(QRect(tree.mapTo(shell, tree.rect().topLeft()), tree.size()))
        assert ov.spot().contains(r.center())
        body = QRectF(ov.callout.geometry()).adjusted(14, 14, -14, -14)
        assert QRectF(ov.rect()).contains(QRectF(ov.callout.geometry()))
        inter = body.intersected(ov.spot())
        assert inter.width() * inter.height() < 0.05 * r.width() * r.height()
    # theme switch mid-tour is harmless
    shell.toggle_theme()
    shell.toggle_theme()
    t.advance()
    assert t.is_active()
    t.finish()


def test_clicks_inside_spotlight_reach_the_control(shell, qtbot):
    t = _start(shell, qtbot)
    ov = t.overlay
    c = ov.spot().center().toPoint()
    assert not ov.mask().contains(c)                   # hole: the control gets the click
    from PySide6.QtCore import QPoint
    assert ov.mask().contains(QPoint(ov.width() - 5, ov.height() - 5))   # scrim blocks
    t.finish()


def test_help_menu_restarts_the_tour(shell, qtbot):
    t = _start(shell, qtbot)
    t.skip()
    shell.act_tour.trigger()
    assert t.is_active() and t.index == 0 and _key(t) == "welcome"
    t.finish()


# ---------------------------------------------------------------------- round 3c: scope
def _user_lot_from_tutorial_images(root):
    """A user job whose images carry a readable scale bar (Auto-find reads
    the label), next to which the tour runs."""
    from core.resources import tutorial_images
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(root)
    pp = ws.create_project("Mine")
    sp = ws.create_sample(pp, "P-9", material="Steel")
    lp = ws.create_lot(pp, sp, "L-9")
    ref = save_session(lp, {"operator": "Tester"},
                       [ImageEntry(source_path=str(p)) for p in tutorial_images()[:2]],
                       label="Mine", catalog=Catalog(root))
    return Path(ref.path)


def _user_snapshot(st, im):
    return dict(uid=im.uid, px=st.px_for(im), scan=st.scan_for(im),
                px_override=im.px_override, scan_rect=im.scan_rect,
                scale_source=im.scale_source, scan_source=im.scan_source,
                result=id(im.result), n=len(im.result.grains),
                stale=st.stale_reason(im), status=im.status)


def test_tour_never_touches_the_users_loaded_images(shell, qtbot, env):
    """Round 3c: with the operator's own images loaded (one job set up by
    Auto-find from the scale-bar label, one by hand, both analysed), a
    complete tour run -- open the Tutorial job, "All images" scan area and
    scale, Analyze all -- acts on the Tutorial job only: the user's images
    are bit-for-bit unchanged and the tour's steps are not pre-satisfied by
    them."""
    import cv2
    from core.grain_detector import DetectionParams
    from ui.workers import analyze_image
    st, a = shell.state, shell.analyze
    auto_rec = _user_lot_from_tutorial_images(env)
    hand_rec = make_session(env, 2, label="Hand", project="ByHand", sample="P-8", lot="L-8")
    shell.load_into_analyzer([auto_rec, hand_rec])
    qtbot.waitUntil(lambda: len(st.images()) == 4 and not st.is_loading(), timeout=TIMEOUT)
    auto = [im for im in st.images() if st.session.record_for(im).path == auto_rec]
    hand = [im for im in st.images() if im not in auto]
    assert st.auto_setup([im.uid for im in auto]) == 2
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    assert all(im.scale_source == "label" and st.setup_ready(im) for im in auto), \
        [(im.scale_source, st.setup_issues(im)) for im in auto]
    for im in hand:
        st.set_calibration(3.0, im.uid)
        st.set_scan_rect((5, 5, 180, 180), im.uid)
    for im in auto + hand:
        pix = im.image_bgr if im.image_bgr is not None else cv2.imread(str(im.path))
        st.set_result(im.uid, analyze_image(pix, st.px_for(im),
                                            DetectionParams(detection_mode="threshold"),
                                            st.scan_for(im)))
    qtbot.waitUntil(lambda: all(im.result is not None for im in auto + hand)
                    and not st.is_filtering(), timeout=TIMEOUT)
    mine = auto + hand
    before = [_user_snapshot(st, im) for im in mine]
    st.save_now()
    st.flush()
    user_files = {p for rec in (auto_rec, hand_rec) for p in rec.rglob("*") if p.is_file()}

    t = _start(shell, qtbot)
    qtbot.mouseClick(shell.projects.btn_primary, Qt.LeftButton)       # open the Tutorial job
    qtbot.waitUntil(lambda: len(st.images()) == 7 and not st.is_loading(), timeout=TIMEOUT)
    qtbot.wait(200)
    assert _key(t) == "scan"                       # not pre-satisfied by the user's images
    tut = [im for im in st.images() if im not in mine]
    assert len(tut) == 3 and not a.step_done()["scan"] and not a.step_done()["scale"]
    a.sync_wizard()
    a.btn_scan_all.click()
    qtbot.waitUntil(lambda: _key(t) == "scale", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: a.btn_scale_all.isEnabled(), timeout=5000)
    a.btn_scale_all.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    qtbot.wait(300)
    assert _key(t) == "mode", (_key(t), a.step_done(), [(st.px_for(im), im.scale_source,
                                                          st.setup_issues(im)) for im in tut])
    assert all(st.px_for(im) == pytest.approx(16.0, rel=0.02) for im in tut)
    a.params.mode_cards["boundary"].clicked.emit()
    qtbot.waitUntil(lambda: _key(t) == "run", timeout=5000)
    assert a.btn_all.isEnabled()
    a.btn_all.click()
    qtbot.wait(500)
    qtbot.waitUntil(lambda: not a.queue.is_running(), timeout=TIMEOUT)
    qtbot.wait(500)
    assert _key(t) in ("go_review", "select"), _key(t)        # past the run step
    assert all(im.result is not None for im in tut)
    assert [_user_snapshot(st, im) for im in mine] == before
    # Review + Reports steps (edits, report, Excel / PowerPoint export)
    if _key(t) == "go_review":
        qtbot.mouseClick(shell.rail.item("review"), Qt.LeftButton)
        qtbot.waitUntil(lambda: _key(t) == "select", timeout=5000)
    qtbot.waitUntil(lambda: shell.review.canvas._labels is not None, timeout=TIMEOUT)
    assert st.in_scope(st.current_image())                 # edits land on a Tutorial image
    _do_review_actions(shell, t, qtbot)
    _do_report_actions(shell, t, qtbot)
    rp = shell.reports
    tut_paths = {str(im.path) for im in tut}
    assert rp.model is not None and rp.model.images
    assert {i.image_path for i in rp.model.images} <= tut_paths   # tutorial images only
    assert list((t.tutorial_record / "exports").glob("*.xlsx"))
    assert list((t.tutorial_record / "exports").glob("*.pptx"))
    assert [_user_snapshot(st, im) for im in mine] == before
    t.finish()
    assert not st.scope_active()
    assert [_user_snapshot(st, im) for im in mine] == before
    st.flush()
    after_files = {p for rec in (auto_rec, hand_rec) for p in rec.rglob("*") if p.is_file()}
    assert after_files <= user_files, sorted(map(str, after_files - user_files))
