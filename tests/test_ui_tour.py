"""
UI-10 first-run guided tour: auto-start rules, "don't show" persistence,
Skip / Esc / Help › Show tour, Next / Back, missing-anchor skipping, target
resolution and resize-following.  Offscreen; settings live in tmp_path.
"""
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QRect, QRectF, Qt  # noqa: E402

from data.models import AppSettings  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import make_session  # noqa: E402


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


# ---------------------------------------------------------------------- start-up
def test_first_run_starts_tour_when_enabled(env, qtbot):
    w = _make_shell(qtbot, tour=True)
    qtbot.waitUntil(lambda: w.tour.is_active(), timeout=3000)
    assert w.tour.index == 0 and w.tour.current_step().kind == "welcome"
    ov = w.tour.overlay
    assert ov.isVisible() and ov.geometry() == w.rect()
    assert ov.callout.dont_show.isVisible()           # offered on the welcome card
    assert ov.callout.btn_next.text() == "Start tour"
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


# ---------------------------------------------------------------------- navigation
def test_next_back_and_step_progress(shell):
    t = shell.tour
    shell.act_tour.trigger()
    assert t.index == 0
    t.next()
    assert t.index == 1 and shell.current_page() == "projects"
    c = t.overlay.callout
    assert c.step_lbl.text() == "STEP 1 OF 9"
    assert c.btn_back.isVisible() and c.btn_next.text() == "Next"
    assert not c.dont_show.isVisible()
    t.next()
    assert t.index == 2
    t.back()
    assert t.index == 1
    # keyboard: Right = next, Left = back
    from PySide6.QtTest import QTest
    QTest.keyClick(t.overlay, Qt.Key_Right)
    assert t.index == 2
    QTest.keyClick(t.overlay, Qt.Key_Left)
    assert t.index == 1


def test_every_step_reached_and_overlay_removed_on_finish(shell, qtbot):
    t = shell.tour
    seen, done = [], []
    t.step_changed.connect(seen.append)
    t.finished.connect(done.append)
    shell.start_tour()
    for _ in range(40):
        if not t.is_active():
            break
        t.next()
    assert done == [True]
    assert seen[0] == 0 and seen[-1] == len(t.steps) - 1
    assert seen == sorted(seen)
    assert t.overlay is None
    qtbot.waitUntil(lambda: not _overlays(shell), timeout=2000)


def test_skip_button_and_escape_close_with_undo_toast(shell, qtbot):
    t = shell.tour
    done = []
    t.finished.connect(done.append)
    shell.start_tour()
    t.next()
    qtbot.mouseClick(t.overlay.callout.btn_skip, Qt.LeftButton)
    assert done == [False] and not t.is_active()
    qtbot.waitUntil(lambda: not _overlays(shell), timeout=2000)
    # Esc also skips
    shell.start_tour()
    t.next()
    t.next()
    from PySide6.QtTest import QTest
    QTest.keyClick(t.overlay, Qt.Key_Escape)
    assert done == [False, False] and not t.is_active()
    # the "Tour closed" toast offers Undo, which resumes at the same step
    from ui.widgets.toast import Toast
    toasts = [x for x in shell.findChildren(Toast) if x.isVisible()]
    assert toasts
    t.start(2)
    assert t.index == 2


# ---------------------------------------------------------------------- anchors
def test_missing_anchor_is_skipped_both_directions(shell):
    from ui.tour import TourController, TourStep
    steps = [
        TourStep("w", "Welcome", "hi", kind="welcome"),
        TourStep("ghost", "Ghost", "gone", targets=("noSuchWidget",)),
        TourStep("real", "Tree", "tree", page="projects", targets=("tourProjectsTree",)),
        TourStep("f", "Done", "bye", kind="finish"),
    ]
    t = TourController(shell, steps)
    t.start()
    t.next()
    assert t.index == 2                                # ghost skipped
    t.back()
    assert t.index == 0                                # and skipped going back
    t.start(1)                                         # starting on it moves forward
    assert t.index == 2
    t.finish()
    assert not t.is_active()


def test_hidden_target_uses_fallback(shell):
    from ui.tour import TourController, TourStep
    from ui.tour.steps import rail_anchor
    step = TourStep("run", "Run", "x", page="analyze", targets=("tourAnalyzeAll",),
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
    # every default step is shown on a fresh install (targets or fallbacks)
    for step in default_steps():
        if not step.centred:
            if step.page:
                shell.go(step.page)
            assert not shell.tour.resolve(step).isEmpty(), step.key


def test_analyze_targets_resolve_with_open_session(env, shell, qtbot):
    path = make_session(env, n=2)
    shell.open_session(path, prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=10000)
    from ui.tour import default_steps
    steps = {s.key: s for s in default_steps()}
    shell.go("analyze")
    qtbot.wait(50)
    for key, name in (("add_images", "tourAddImages"), ("scale", "tourCalibration"),
                      ("run", "tourAnalyzeAll")):
        from PySide6.QtWidgets import QWidget
        w = shell.findChild(QWidget, name)
        assert w.isVisible(), key
        r = shell.tour.resolve(steps[key])
        c = QRectF(QRect(w.mapTo(shell, w.rect().topLeft()), w.size())).center()
        assert r.contains(c), key


# ---------------------------------------------------------------------- layout
def test_resize_follows_target_and_callout_avoids_it(shell, qtbot):
    t = shell.tour
    shell.start_tour(1)                                # Projects tree
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
    t.next()
    assert t.is_active()
    t.finish()


def test_clicks_inside_spotlight_reach_the_control(shell):
    t = shell.tour
    shell.start_tour(1)
    ov = t.overlay
    c = ov.spot().center().toPoint()
    assert not ov.mask().contains(c)                   # hole: the control gets the click
    from PySide6.QtCore import QPoint
    assert ov.mask().contains(QPoint(ov.width() - 5, ov.height() - 5))   # scrim blocks
    t.finish()
