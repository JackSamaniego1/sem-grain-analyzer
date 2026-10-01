"""UPDATE 4 batch 4 item C (D-40): animated grain-microstructure launcher splash."""
import os
import subprocess
import sys

import pytest

from ui.widgets.splash import DURATION_MS, GrainSplash, build_microstructure


def test_microstructure_is_seeded_and_about_sixty_grains():
    g1, e1 = build_microstructure()
    g2, e2 = build_microstructure()
    assert len(g1) == 60
    assert all(len(g["poly"]) >= 3 for g in g1)
    assert [g["poly"] for g in g1] == [g["poly"] for g in g2]   # deterministic
    assert len(e1) == len(e2) > 60
    # grains tile the frame: polygon areas sum to the frame area
    def area(poly):
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                       in zip(poly, poly[1:] + poly[:1]))) / 2
    assert sum(area(g["poly"]) for g in g1) == pytest.approx(560 * 300, rel=1e-6)
    # every element finishes within one pass
    assert all(0.0 <= g["start"] <= 0.7 for g in g1)
    assert all(0.0 <= e[4] <= 0.72 for e in e1)


def test_renders_headlessly_to_png(qtbot, tmp_path):
    s = GrainSplash("SEM Grain Analyzer", "3.0.0", "Lab", reduced_motion=False)
    qtbot.addWidget(s)
    for t in (0.0, 0.35, 1.0):
        img = s.render_frame(t, progress=t)
        assert not img.isNull() and img.width() == 560
        assert img.save(str(tmp_path / f"splash_{t}.png"))
    # the mid frame differs from both the empty and the final frame
    a, m, b = (s.render_frame(t) for t in (0.0, 0.35, 1.0))
    assert a != m and m != b
    s.show()
    assert not s.grab().isNull()


def test_reduced_motion_shows_final_frame_immediately(qtbot):
    s = GrainSplash("X", "1", reduced_motion=True)
    qtbot.addWidget(s)
    assert s.pass_done and s.anim_t() == 1.0
    s.show()
    assert s.render_frame(s.anim_t()) == s.render_frame(1.0)
    s.set_progress(0.5)
    assert s._progress == 0.5                      # no easing either
    called = []
    s.finish(lambda: called.append(1))
    assert called == [1] and not s.isVisible()     # closes at once, no fade


def test_env_flag_enables_reduced_motion(monkeypatch, qtbot):
    from ui.design import theme
    monkeypatch.setattr(theme, "reduced_motion", lambda: True)
    s = GrainSplash("X", "1")
    qtbot.addWidget(s)
    assert s.reduced_motion and s.pass_done


def test_waits_for_one_pass_when_window_ready_early(qtbot):
    s = GrainSplash("X", "1", reduced_motion=False)
    qtbot.addWidget(s)
    s.show()
    called = []
    s.finish(lambda: called.append(1))             # window ready immediately
    assert s.is_ready and not s.pass_done and called == []
    with qtbot.waitSignal(s.closed, timeout=DURATION_MS + 2000):
        pass
    assert s.pass_done and called == [1] and not s.isVisible()


def test_waits_for_window_when_pass_finishes_first(qtbot):
    s = GrainSplash("X", "1", reduced_motion=False)
    qtbot.addWidget(s)
    s.show()
    with qtbot.waitSignal(s.pass_completed, timeout=DURATION_MS + 2000):
        pass
    qtbot.wait(100)
    assert s.isVisible() and s.pass_done and not s.is_ready   # still waiting
    with qtbot.waitSignal(s.closed, timeout=2000):
        s.finish()
    assert not s.isVisible()


def test_clock_counts_rendered_time_not_blocked_time(qtbot):
    """A blocked GUI thread (e.g. building the main window) pauses the
    animation instead of skipping it: one tick advances at most MAX_TICK_MS."""
    import time
    from PySide6.QtWidgets import QApplication
    from ui.widgets.splash import MAX_TICK_MS
    s = GrainSplash("X", "1", reduced_motion=False)
    qtbot.addWidget(s)
    s.show()
    qtbot.wait(60)
    t0 = s.anim_t()
    time.sleep(0.6)                                # GUI thread blocked 600 ms
    QApplication.processEvents()
    t1 = s.anim_t()
    assert t1 - t0 <= (2 * MAX_TICK_MS) / DURATION_MS + 1e-9
    assert not s.pass_done


def test_advance_is_capped_and_pass_needs_full_rendered_duration(qtbot):
    from ui.widgets.splash import MAX_TICK_MS
    s = GrainSplash("X", "1", reduced_motion=False)
    qtbot.addWidget(s)
    s.advance(5000)                                # one huge stall
    assert s.anim_t() == pytest.approx(MAX_TICK_MS / DURATION_MS)
    s.advance(-10)
    assert s.anim_t() == pytest.approx(MAX_TICK_MS / DURATION_MS)
    n = int(DURATION_MS / MAX_TICK_MS)
    for _ in range(n - 2):
        s.advance(MAX_TICK_MS)
    assert s.anim_t() < 1.0
    s.advance(MAX_TICK_MS)
    assert s.anim_t() == pytest.approx(1.0)


def test_waits_for_full_pass_even_if_ready_after_a_stall(qtbot):
    """Window build blocked the GUI thread for longer than a pass: the splash
    must still play the remaining pass before handing over."""
    import time
    from PySide6.QtWidgets import QApplication
    s = GrainSplash("X", "1", reduced_motion=False)
    qtbot.addWidget(s)
    s.show()
    QApplication.processEvents()
    time.sleep(DURATION_MS / 1000 + 0.3)
    called = []
    s.finish(lambda: called.append(1))
    QApplication.processEvents()
    assert called == [] and s.isVisible()
    with qtbot.waitSignal(s.closed, timeout=DURATION_MS + 2000):
        pass
    assert called == [1]


def test_main_gives_splash_head_start():
    import main
    assert 250 <= main._SPLASH_HEAD_START_MS <= 300
    src = open(main.__file__, encoding="utf-8").read()
    assert "QTimer.singleShot(_SPLASH_HEAD_START_MS" in src


def test_main_uses_new_splash_and_startup_stays_lean():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "main.py"), encoding="utf-8").read()
    assert "QSplashScreen" not in src and "GrainSplash" in src
    code = (
        "import sys\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "import main\n"
        "s = main._create_splash()\n"
        "bad = [m for m in ('scipy', 'skimage', 'torch') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120, cwd=root, env=env)
    assert r.returncode == 0, r.stderr
