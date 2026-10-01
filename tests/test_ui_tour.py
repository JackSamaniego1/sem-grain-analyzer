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
    assert c.step_lbl.text() == "STEP 1 OF 7" and not c.dont_show.isVisible()
    for k in (Qt.Key_Right, Qt.Key_PageDown, Qt.Key_Return, Qt.Key_Left):
        QTest.keyClick(t.overlay, k)
    assert _key(t) == "open_job"                        # keys never advance an action step
    t.finish()


def test_steps_are_the_spec_list_each_with_one_target_and_a_hook():
    from ui.tour import default_steps
    steps = default_steps()
    assert [s.key for s in steps] == ["welcome", "open_job", "scan", "scale", "mode", "run",
                                      "review", "export", "finish"]
    for s in steps[1:-1]:
        assert len(s.targets) == 1, s.key               # exactly one control
        assert s.advance_on is not None and s.kind == "click", s.key
    assert steps[2].targets == ("scan_all",) and steps[3].targets == ("scale_all",)
    assert steps[5].targets == ("run_all",) and steps[7].targets == ("tourExportPptx",)


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


# ---------------------------------------------------------------------- end to end
def test_end_to_end_by_doing_each_action(shell, qtbot, env):
    """Every step advances from the action itself: open the job, auto-find
    scan area and scale, choose a mode, analyse, select a grain, export."""
    st, a = shell.state, shell.analyze
    t = _start(shell, qtbot)
    seen, done = [], []
    t.step_changed.connect(lambda i: seen.append(t.steps[i].key))
    t.finished.connect(done.append)
    from PySide6.QtWidgets import QWidget

    def spot_on(name):
        w = shell.findChild(QWidget, name)
        c = QRectF(QRect(w.mapTo(shell, w.rect().topLeft()), w.size())).center()
        return t.overlay.spot().contains(c)

    # 2. open the Tutorial job: the spotlit "Open Lot" button
    assert shell.current_page() == "projects" and spot_on("tourNewSession")
    assert shell.projects.btn_primary.text().startswith("Open")
    btn = shell.projects.btn_primary
    assert not t.overlay.mask().contains(t.overlay.spot().center().toPoint())  # clicks reach it
    qtbot.mouseClick(btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "scan", timeout=TIMEOUT)
    assert Path(st.session.path) == t.tutorial_record and len(st.images()) == 3
    qtbot.waitUntil(lambda: not st.is_loading(), timeout=TIMEOUT)
    a.sync_wizard()
    # 3. step 1 All images
    assert shell.current_page() == "analyze" and spot_on("scan_all")
    a.btn_scan_all.click()
    qtbot.waitUntil(lambda: _key(t) == "scale", timeout=TIMEOUT)
    # 4. step 2 All images (scale bar + OCR on the bundled images)
    qtbot.waitUntil(lambda: a.btn_scale_all.isEnabled(), timeout=5000)
    a.btn_scale_all.click()
    # the label read off the image (10 µm) waits for the operator's OK: the
    # spotlight moves to the length row and its Apply button
    qtbot.waitUntil(lambda: a.scale_row.isVisible() and spot_on("scaleLengthRow"),
                    timeout=TIMEOUT)
    assert t.overlay.callout.title.text() == "Check the scale-bar length"
    qtbot.waitUntil(lambda: a.scale_row.bar_len.value() == pytest.approx(10.0), timeout=5000)
    assert a.scale_row.bar_same.isChecked()
    qtbot.mouseClick(a.scale_row.btn_bar, Qt.LeftButton)
    qtbot.waitUntil(lambda: _key(t) == "mode", timeout=TIMEOUT)
    assert all(st.px_for(im) == pytest.approx(16.0, rel=0.02) for im in st.images())
    # 5. step 3: the tiles are shown now; choose Boundary
    assert not a.step_mode.body.isHidden()
    a.params.mode_cards["boundary"].clicked.emit()
    qtbot.waitUntil(lambda: _key(t) == "run", timeout=5000)
    # 6. step 4 Analyze all: the progress card is spotlit while it runs
    assert spot_on("run_all")
    a.btn_all.click()
    qtbot.waitUntil(lambda: a.queue.is_running() and spot_on("wizard_progress") or
                    _key(t) != "run", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: _key(t) == "review", timeout=TIMEOUT)
    assert all(im.result is not None for im in st.images())
    # 7. Review: click (select) a grain
    assert shell.current_page() == "review"
    cv = shell.review.canvas
    qtbot.waitUntil(lambda: cv._labels is not None, timeout=TIMEOUT)
    gid = int(next(v for v in set(cv._labels.ravel().tolist()) if v > 0))
    cv.select([gid])
    qtbot.waitUntil(lambda: _key(t) == "export", timeout=5000)
    # 8. Reports: the report is built for the user, then PowerPoint
    rp = shell.reports
    assert shell.current_page() == "reports"
    qtbot.waitUntil(lambda: rp.btn_pptx.isEnabled(), timeout=TIMEOUT)
    assert spot_on("tourExportPptx")
    rp.btn_pptx.click()
    qtbot.waitUntil(lambda: _key(t) == "finish", timeout=TIMEOUT)
    assert list((t.tutorial_record / "exports").glob("*.pptx"))
    # 9. Finish: Done
    c = t.overlay.callout
    assert c.btn_primary.text() == "Done" and not c.btn_skip.isVisible()
    qtbot.mouseClick(c.btn_primary, Qt.LeftButton)
    assert done == [True] and not t.is_active()
    assert seen == ["scan", "scale", "mode", "run", "review", "export", "finish"]
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
    qtbot.waitUntil(lambda: _key(t) == "review", timeout=TIMEOUT)
    shell.review.canvas.selection_changed.emit([1])
    shell.review.canvas.selected = lambda: [1]
    shell.review.canvas.selection_changed.emit([1])
    qtbot.waitUntil(lambda: _key(t) == "export", timeout=5000)
    shell.reports.exported.emit(["x.xlsx"])               # not the PowerPoint: stays
    qtbot.wait(50)
    assert _key(t) == "export"
    shell.reports.exported.emit([str(t.tutorial_record / "exports" / "r.pptx")])
    qtbot.waitUntil(lambda: _key(t) == "finish", timeout=5000)
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
