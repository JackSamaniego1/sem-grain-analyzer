"""UPDATE 4 item 7 (UI side): no crashes or lost work when the operator
interacts while images are being analysed.

Covers: the analysis queue's stale-timer race, close while analysing (waits,
never destroys a running thread), the crash log, thread caps at start-up,
the ONE analysis gate (``AppState.analysis_lock``) on every edit path, view
actions that stay allowed, session open / close while analysing, the undo
stack after a re-analysis, load shedding (dedicated filter pool, no wasted
label copy), the image-details follow-ups, and a randomised stress test.
"""
from __future__ import annotations

import os
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import confirm_setup, make_session  # noqa: E402

TIMEOUT = 90000


# ======================================================================
# fixtures / helpers
# ======================================================================

@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    import core.ai_device as aid
    from ui.ai_probe import reset_device_probe
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: aid.AiDeviceInfo())
    reset_device_probe()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)
    reset_device_probe()


class Prompt:
    """Stands in for the gate's dialog: records every question."""

    def __init__(self, edit="wait", session="wait", dont_warn=False):
        self.edit, self.session, self.dont_warn = edit, session, dont_warn
        self.calls = []

    def __call__(self, kind, action):
        self.calls.append((kind, action))
        return (self.session if kind == "session" else self.edit), self.dont_warn


def _slow_detector(monkeypatch, delay: float, honour_cancel: bool = True):
    """GrainDetector.analyze takes ``delay`` s longer (cancel is checked
    every 20 ms unless ``honour_cancel`` is False: an uninterruptible step)."""
    from core.cancel import make_cancel_check
    from core.grain_detector import GrainDetector
    orig = GrainDetector.analyze
    stats = {"active": 0, "peak": 0, "calls": 0}
    lock = threading.Lock()

    def slow(self, *a, **k):
        with lock:
            stats["active"] += 1
            stats["calls"] += 1
            stats["peak"] = max(stats["peak"], stats["active"])
        try:
            check = make_cancel_check(k.get("cancel"))
            end = time.monotonic() + delay
            while time.monotonic() < end:
                if honour_cancel:
                    check()
                time.sleep(0.02)
            return orig(self, *a, **k)
        finally:
            with lock:
                stats["active"] -= 1
    monkeypatch.setattr(GrainDetector, "analyze", slow)
    return stats


def _open_shell(qtbot, path: Path):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1400, 900)
    shell.show()
    shell.open_session(path)
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    shell.analyze.params.set_mode("threshold")
    return shell


def _close_shell(shell, qtbot):
    from ui.workers import alive_analysis_threads
    shell.confirm_close = lambda: True
    shell.close()
    qtbot.waitUntil(lambda: not shell.isVisible(), timeout=TIMEOUT)
    qtbot.waitUntil(lambda: alive_analysis_threads() == 0, timeout=TIMEOUT)


def _analysed_state(qtbot, root, n=2):
    """AppState with ``n`` images analysed synchronously (threshold mode)."""
    from core.grain_detector import DetectionParams
    from ui.app_state import AppState
    from ui.workers import analyze_image, read_image
    st = AppState()
    st.open_session(make_session(root, n, label="Gate"))
    qtbot.waitUntil(lambda: st.session is not None, timeout=15000)
    st.set_calibration(2.0)
    for im in st.images():
        raw = analyze_image(read_image(im.path), 2.0, DetectionParams(detection_mode="threshold"),
                            draw_overlay=True)
        st.set_result(im.uid, raw)
    qtbot.waitUntil(lambda: all(im.status == "done" and im.result is not None
                                for im in st.images()) and not st.is_filtering(),
                    timeout=TIMEOUT)
    return st


def _run_lock(st, busy=(), prompt=None):
    """Simulate a running analysis with ``busy`` images queued / running."""
    lock = st.analysis_lock
    stopped = []
    lock.bind(busy_uids=lambda: set(busy), stop=lambda: stopped.append(1))
    lock.prompt = prompt or Prompt()
    lock.set_active(True)
    return lock, stopped


def _neighbours(lab: np.ndarray, kept: set):
    """Two touching kept grain ids."""
    a, b = lab[:, :-1], lab[:, 1:]
    m = (a != b) & (a > 0) & (b > 0)
    for x, y in zip(a[m].tolist(), b[m].tolist()):
        if x in kept and y in kept:
            return [int(x), int(y)]
    return None


# ======================================================================
# 2. AnalysisQueue: generation counter, one thread at a time
# ======================================================================

def test_queue_stale_timer(qapp, qtbot, monkeypatch):
    """Cancel in the gap between two images and restart at once: the old
    run's delayed "next image" call must not start a second worker."""
    import ui.workers as w
    from core.grain_detector import DetectionParams
    from ui.workers import AnalysisJob, AnalysisQueue, alive_analysis_threads

    active = {"now": 0, "peak": 0}
    lk = threading.Lock()

    def fake_analyze(img, *a, **k):
        with lk:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.15)
        with lk:
            active["now"] -= 1
        from core.grain_detector import AnalysisResult
        return AnalysisResult()
    monkeypatch.setattr(w, "analyze_image", fake_analyze)
    monkeypatch.setattr(AnalysisQueue, "NEXT_DELAY_MS", 400)     # a wide gap

    q = AnalysisQueue()
    img = np.zeros((20, 20, 3), np.uint8)
    jobs = lambda tag: [AnalysisJob(f"{tag}{i}", img.copy(), 1.0, DetectionParams())  # noqa: E731
                        for i in range(3)]
    done = []
    q.job_finished.connect(lambda uid, _r: done.append(uid))
    assert q.start(jobs("a"))
    # the first image finishes -> its thread ends -> the 400 ms gap begins
    qtbot.waitUntil(lambda: done == ["a0"] and q._thread is None, timeout=10000)
    assert q.is_running() and not q.is_idle()
    q.cancel()                                     # in the gap: finishes at once
    assert not q.is_running() and q.is_idle()
    assert q.start(jobs("b"))                      # restart immediately
    started = q.threads_started
    assert not q.start(jobs("c"))                  # never a second run on top
    q._next(q._gen)                                # a thread is running: ignored
    assert q.threads_started == started
    with qtbot.waitSignal(q.queue_finished, timeout=20000):
        pass
    qtbot.wait(600)                                # the stale timer has fired by now
    assert active["peak"] == 1                     # never two detectors at once
    assert done == ["a0", "b0", "b1", "b2"]
    assert q.threads_started == 4
    assert alive_analysis_threads() == 0 and q.is_idle()


def test_queue_threads_start_below_gui_priority(qapp, qtbot, monkeypatch):
    import ui.workers as w
    from PySide6.QtCore import QThread
    from core.grain_detector import AnalysisResult, DetectionParams
    from ui.workers import AnalysisJob, AnalysisQueue
    seen = []

    def fake(img, *a, **k):
        seen.append(QThread.currentThread().priority())
        return AnalysisResult()
    monkeypatch.setattr(w, "analyze_image", fake)
    q = AnalysisQueue()
    with qtbot.waitSignal(q.queue_finished, timeout=10000):
        q.start([AnalysisJob(1, np.zeros((8, 8, 3), np.uint8), 1.0, DetectionParams())])
    assert seen == [QThread.Priority.LowPriority]
    for pool in w.all_pools():
        assert pool.threadPriority() == QThread.Priority.LowPriority


def test_deliver_dropped_while_shutting_down(qapp, qtbot, monkeypatch):
    import ui.workers as w
    got = []
    sig = w._TaskSignals(lambda v: got.append(v), lambda m: got.append(m))
    monkeypatch.setattr(w, "_SHUTTING_DOWN", True)
    sig._deliver_done(1)
    sig._deliver_failed("x")
    assert got == []
    monkeypatch.setattr(w, "_SHUTTING_DOWN", False)
    sig._deliver_done(2)
    assert got == [2]


# ======================================================================
# 3. close while analysing
# ======================================================================

def test_close_waits(env, qtbot, monkeypatch):
    """"Stop and close?" -> the window stays alive (Stopping…) until the
    image in flight has stopped -- however long that takes -- and no
    running thread is ever destroyed."""
    from ui.workers import alive_analysis_threads
    shell = _open_shell(qtbot, make_session(env, 2, label="Close"))
    confirm_setup(shell, qtbot)
    # an image in flight that cannot be interrupted for ~2.5 s
    _slow_detector(monkeypatch, 2.5, honour_cancel=False)
    q = shell.analyze.queue
    shell.analyze_all()
    qtbot.waitUntil(lambda: q.thread_alive(), timeout=10000)
    # "Keep analysing": nothing happens
    shell.confirm_close = lambda: False
    shell.close()
    assert shell.isVisible() and q.is_running() and not shell.is_closing()
    # "Stop and close": waits without a time limit, UI alive meanwhile
    shell.confirm_close = lambda: True
    t0 = time.monotonic()
    shell.close()
    assert shell.isVisible() and shell.is_closing()
    assert "Stopping" in shell.statusBar().currentMessage()
    shell.close()                                  # a second click: still waiting
    assert shell.isVisible()
    ticks = []
    from PySide6.QtCore import QTimer
    tick = QTimer()
    tick.timeout.connect(lambda: ticks.append(1))
    tick.start(50)
    qtbot.waitUntil(lambda: not shell.isVisible(), timeout=TIMEOUT)
    tick.stop()
    assert time.monotonic() - t0 > 1.0             # it really waited for the image
    assert len(ticks) >= 10                        # the event loop kept running
    assert alive_analysis_threads() == 0


def test_close_question_is_not_nested(env, qtbot, monkeypatch):
    """A second close request while "Stop and close?" is open (its nested
    event loop) must not open a second question."""
    shell = _open_shell(qtbot, make_session(env, 2, label="Nest"))
    confirm_setup(shell, qtbot)
    _slow_detector(monkeypatch, 1.5)
    q = shell.analyze.queue
    shell.analyze_all()
    qtbot.waitUntil(lambda: q.thread_alive(), timeout=10000)
    asked = []

    def confirm():
        asked.append(1)
        shell.close()                              # e.g. Alt+F4 again meanwhile
        shell.close()
        return False                               # "Keep analysing"
    shell.confirm_close = confirm
    shell.close()
    assert asked == [1]
    assert shell.isVisible() and not shell.is_closing() and q.is_running()
    # the flag is cleared again: a later close asks once more
    shell.close()
    assert asked == [1, 1]
    _close_shell(shell, qtbot)


def test_close_when_idle_closes_at_once(env, qtbot):
    shell = _open_shell(qtbot, make_session(env, 1, label="Idle"))
    asked = []
    shell.confirm_close = lambda: asked.append(1) or True
    shell.close()
    assert not shell.isVisible() and asked == []


# ======================================================================
# 4. crash log
# ======================================================================

def test_excepthook_logs(qapp, qtbot, tmp_path, monkeypatch):
    from ui import crash_log
    shown = []
    monkeypatch.setattr(crash_log, "notify", lambda t, x: shown.append((t, x)))
    monkeypatch.setattr(crash_log, "NOTIFY_MIN_INTERVAL_S", 0.0)
    was = sys.excepthook
    path = crash_log.install(tmp_path / "logs")
    try:
        assert path == tmp_path / "logs" / crash_log.LOG_NAME
        # 1. uncaught error on the GUI thread
        try:
            raise ValueError("boom in the main thread")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        # 2. uncaught error in a worker thread
        t = threading.Thread(target=lambda: 1 / 0, name="worker-x")
        t.start()
        t.join()
        # 3. an exception in a Qt slot does not kill the process
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            sig = Signal()

        def bad_slot():
            raise RuntimeError("slot failed")
        em = Emitter()
        em.sig.connect(bad_slot)
        monkeypatch.setattr(sys, "excepthook", sys.excepthook)   # pytest-qt restores
        with qtbot.capture_exceptions() as caught:
            em.sig.emit()
        assert caught and "slot failed" in str(caught[0][1])
        crash_log.report(*caught[0])                               # what the hook does
        text = path.read_text(encoding="utf-8")
        assert "boom in the main thread" in text and "Traceback" in text
        assert "ZeroDivisionError" in text and "worker-x" in text
        assert "slot failed" in text
        assert shown and str(path) in shown[0][1]
        assert "nothing is sent" in shown[0][1]
        assert (tmp_path / "logs" / crash_log.NATIVE_NAME).exists()   # faulthandler
        import faulthandler
        assert faulthandler.is_enabled()
    finally:
        crash_log.uninstall()
    assert sys.excepthook is was


def test_crash_log_is_size_capped(tmp_path, monkeypatch):
    import logging
    from ui import crash_log
    monkeypatch.setattr(crash_log, "MAX_BYTES", 2000)
    monkeypatch.setattr(crash_log, "notify", lambda t, x: None)
    path = crash_log.install(tmp_path / "logs")
    try:
        for i in range(200):
            logging.getLogger("grain_analyzer.test").warning("line %d %s", i, "x" * 50)
        files = list((tmp_path / "logs").glob(crash_log.LOG_NAME + "*"))
        assert 1 < len(files) <= crash_log.BACKUPS + 1
        assert all(f.stat().st_size <= 2600 for f in files)
        assert path.exists()
    finally:
        crash_log.uninstall()


def test_crash_log_hooks_without_writable_folder(tmp_path, monkeypatch):
    """The log folder cannot be written: the hooks are installed anyway
    (on-screen message + stderr need no file)."""
    from ui import crash_log
    shown = []
    monkeypatch.setattr(crash_log, "notify", lambda t, x: shown.append((t, x)))
    monkeypatch.setattr(crash_log, "NOTIFY_MIN_INTERVAL_S", 0.0)
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    was_sys, was_thread = sys.excepthook, threading.excepthook
    for folder, probe_fails in ((blocker / "logs", False), (tmp_path / "ro", True)):
        if probe_fails:                            # exists, but files cannot be created
            def deny(d):
                raise PermissionError("read-only")
            monkeypatch.setattr(crash_log, "_probe_writable", deny)
        shown.clear()
        path = crash_log.install(folder)
        try:
            assert path is None and crash_log.is_installed()
            assert sys.excepthook is crash_log._sys_hook
            assert threading.excepthook is crash_log._thread_hook
            try:
                raise ValueError("no log folder")
            except ValueError:
                sys.excepthook(*sys.exc_info())
            t = threading.Thread(target=lambda: 1 / 0, name="worker-y")
            t.start()
            t.join()
            assert len(shown) == 2 and "not writable" in shown[0][1]
            assert "nothing is sent" in shown[0][1].lower()
        finally:
            crash_log.uninstall()
        assert sys.excepthook is was_sys and threading.excepthook is was_thread
    assert not (tmp_path / "ro" / crash_log.LOG_NAME).exists()


def _visible_boxes():
    from PySide6.QtWidgets import QApplication, QMessageBox
    return [w for w in QApplication.topLevelWidgets()
            if isinstance(w, QMessageBox) and w.isVisible()]


def _close_boxes(qtbot):
    from ui import crash_log
    for b in _visible_boxes():
        b.done(0)
    qtbot.waitUntil(lambda: not crash_log.notice_visible(), timeout=5000)


def test_crash_notice_one_box_at_a_time(qapp, qtbot, tmp_path, monkeypatch):
    """An error repeating (e.g. in a timer slot) shows one message box while
    it is open, not a new one every interval."""
    from ui import crash_log
    monkeypatch.setattr(crash_log, "notify", None)
    monkeypatch.setattr(crash_log, "NOTIFY_MIN_INTERVAL_S", 0.0)
    crash_log.install(tmp_path / "logs")
    try:
        crash_log.prepare_gui()
        n = crash_log._state["notifier"]
        base = n.shown
        for i in range(4):
            crash_log.report(RuntimeError, RuntimeError(f"tick {i}"), None)
            qapp.processEvents()
        qtbot.waitUntil(crash_log.notice_visible, timeout=5000)
        qtbot.wait(50)
        assert n.shown == base + 1 and len(_visible_boxes()) == 1
        _close_boxes(qtbot)
        crash_log.report(RuntimeError, RuntimeError("again"), None)   # closed: shows again
        qtbot.waitUntil(crash_log.notice_visible, timeout=5000)
        assert n.shown == base + 2
        _close_boxes(qtbot)
    finally:
        crash_log.uninstall()


def test_crash_notifier_created_from_worker_lives_on_gui_thread(qapp, qtbot, tmp_path,
                                                               monkeypatch):
    from ui import crash_log
    monkeypatch.setattr(crash_log, "notify", None)
    monkeypatch.setattr(crash_log, "NOTIFY_MIN_INTERVAL_S", 0.0)
    monkeypatch.setitem(crash_log._state, "notifier", None)   # nothing prepared yet
    crash_log.install(tmp_path / "logs")
    try:
        t = threading.Thread(target=lambda: crash_log.report(
            ValueError, ValueError("first report from a worker"), None), name="worker-z")
        t.start()
        t.join()
        n = crash_log._state["notifier"]
        assert n is not None and n.thread() is qapp.thread()
        qtbot.waitUntil(crash_log.notice_visible, timeout=5000)    # shown on the GUI thread
        crash_log.prepare_gui()                                      # idempotent
        assert crash_log._state["notifier"] is n
        _close_boxes(qtbot)
    finally:
        crash_log.uninstall()


def test_crash_log_never_uses_network():
    src = (Path(__file__).resolve().parents[1] / "ui" / "crash_log.py").read_text("utf-8")
    for bad in ("socket", "urllib", "http", "requests", "QtNetwork", "webbrowser"):
        assert bad not in src


# ======================================================================
# 1. thread caps at start-up
# ======================================================================

def test_perf_thread_caps(tmp_path, monkeypatch):
    import inspect

    import cv2

    import main
    from ui import crash_log
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    try:
        caps = main.configure_runtime()
        assert caps and caps["opencv_threads"] >= 1 and caps["ocr_threads"] in (1, 2)
        assert cv2.getNumThreads() == caps["opencv_threads"]
        assert "torch" not in sys.modules or caps["torch_threads"] >= 1
        assert crash_log.is_installed()
        assert (tmp_path / "appdata" / "GrainAnalyzer" / "logs").is_dir()
    finally:
        crash_log.uninstall()
    # configured before the QApplication and the main window exist
    src = inspect.getsource(main.main)
    assert src.index("configure_runtime()") < src.index("QApplication(")
    assert src.index("configure_runtime()") < src.index("AppShell")


# ======================================================================
# 5. the ONE gate
# ======================================================================

def test_lock_blocks_edits(env, qtbot):
    """Every gated method: "Wait" changes nothing; "Continue anyway" holds
    for the run (asked once); an image queued / running is always refused."""
    from core.grain_edit import GrainEditError
    from ui.app_state import EditRefused
    from ui.filtering import options_from_dict, options_to_dict
    st = _analysed_state(qtbot, env, 3)
    a, b, busy = st.images()
    gid = a.result.grains[0].grain_id
    pair = _neighbours(a.raw.label_image, st.kept_ids(a.uid))
    assert pair
    prompt = Prompt(edit="wait")
    lock, _ = _run_lock(st, busy=[busy.uid], prompt=prompt)
    assert lock.is_active()
    px0, scan0 = st.session.px_per_um, st.session.scan_rect
    f0 = options_to_dict(st.session.filters)
    flip = options_from_dict(dict(f0, exclude_border=not f0["exclude_border"]))

    def refused_all():
        assert st.delete_grains(a.uid, [gid]) is False
        assert st.restore_grains(a.uid, [gid]) is False
        for call in (lambda: st.merge_grains(a.uid, pair),
                     lambda: st.split_grain(a.uid, [(0, 0), (50, 50)]),
                     lambda: st.add_grain(a.uid, [(1, 1), (30, 1), (30, 30)])):
            with pytest.raises(EditRefused):
                call()
        st.set_filter_options(flip)
        st.set_filter_options(flip, a.uid)
        st.apply_filters_to_all(flip)
        st.set_scan_rect((5, 5, 100, 100), a.uid)
        st.set_calibration(9.0, a.uid)
        assert st.auto_setup([a.uid]) == 0
        assert st.remove_images([a.uid]) == 0
        assert a.manual == set() and a.edits == [] and st.undo_stack.count() == 0
        assert options_to_dict(st.session.filters) == f0 and a.filter_override is None
        assert a.scan_rect is None and a.px_override == 0.0
        assert st.session.px_per_um == px0 and st.session.scan_rect == scan0
        assert len(st.images()) == 3

    refused_all()
    assert len(prompt.calls) == 12 and all(k == "edit" for k, _ in prompt.calls)
    # "Continue anyway": asked once, then allowed for the rest of the run
    prompt.edit = "continue"
    prompt.calls.clear()
    assert st.delete_grains(a.uid, [gid]) is True
    assert st.restore_grains(a.uid, [gid]) is True
    try:
        st.merge_grains(a.uid, pair)
    except GrainEditError as exc:                 # geometry may refuse; the gate may not
        assert not isinstance(exc, EditRefused)
    st.set_calibration(3.0, b.uid)
    assert b.px_override == 3.0
    assert st.undo() is True and st.redo() is True
    assert len(prompt.calls) == 1
    # ... but never on an image that is queued / running
    assert st.delete_grains(busy.uid, [busy.result.grains[0].grain_id]) is False
    with pytest.raises(EditRefused):
        st.merge_grains(busy.uid, [1, 2])
    st.set_calibration(5.0, busy.uid)
    assert busy.px_override == 0.0
    assert st.remove_images([busy.uid]) == 0
    st.set_scan_rect_all((0, 0, 50, 50))           # changes the running image too
    assert busy.scan_rect is None
    assert len(prompt.calls) == 1                  # a notice, no second dialog
    # the run ends: everything is allowed again, no questions
    lock.set_active(False)
    assert st.delete_grains(busy.uid, [busy.result.grains[0].grain_id]) is True
    assert len(prompt.calls) == 1
    # a new run asks again
    lock.set_active(True)
    assert st.delete_grains(b.uid, [b.result.grains[0].grain_id]) is True
    assert len(prompt.calls) == 2


def test_lock_dont_warn_again(env, qtbot):
    st = _analysed_state(qtbot, env, 2)
    a, b = st.images()
    prompt = Prompt(edit="wait", dont_warn=True)
    lock, _ = _run_lock(st, prompt=prompt)
    assert st.warn_edit_during_analysis
    assert st.delete_grains(a.uid, [a.result.grains[0].grain_id]) is False   # "Wait"
    assert not st.warn_edit_during_analysis
    assert st.delete_grains(a.uid, [a.result.grains[0].grain_id]) is True    # no question
    assert len(prompt.calls) == 1
    # still refused for a busy image
    lock.bind(busy_uids=lambda: {b.uid})
    assert st.delete_grains(b.uid, [b.result.grains[0].grain_id]) is False
    # stored locally and re-enabled from Settings
    from ui.app_state import load_ui_state
    stored = getattr(st.settings, "warn_edit_during_analysis", None)
    assert stored is False or load_ui_state().get("warn_edit_during_analysis") is False
    st.set_warn_edit_during_analysis(True)
    assert st.warn_edit_during_analysis


def test_locked_controls_look_disabled(env, qtbot):
    from ui.analysis_lock import LOCKED_TIP
    shell = _open_shell(qtbot, make_session(env, 2, label="Tips"))
    st = shell.state
    im = st.images()[0]
    lock, _ = _run_lock(st, busy=[im.uid], prompt=Prompt())
    for a in (shell.act_close, shell.act_new, shell.act_run_all, shell.act_run_cur):
        assert not a.isEnabled() and a.toolTip() == LOCKED_TIP
    shell.go("review")
    st.set_current_image(im.uid)
    rv = shell.review
    assert not rv.btn_tool_split.isEnabled() and not rv.btn_tool_add.isEnabled()
    assert "being analyzed" in rv.btn_tool_split.toolTip()
    lock.set_active(False)
    for a in (shell.act_close, shell.act_new, shell.act_run_all, shell.act_run_cur):
        assert a.isEnabled() and a.toolTip() != LOCKED_TIP
    assert rv.btn_tool_split.isEnabled() and "being analyzed" not in rv.btn_tool_split.toolTip()
    _close_shell(shell, qtbot)


def test_pan_zoom_view_allowed(env, qtbot):
    """Viewing never asks: pan, zoom, display mode, opacity, switching image,
    hover, selection and opening Review."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    shell = _open_shell(qtbot, make_session(env, 2, label="View"))
    st = shell.state
    a, b = st.images()
    prompt = Prompt(edit="wait")
    lock, _ = _run_lock(st, busy=[a.uid], prompt=prompt)
    shell.go("analyze")
    cv = shell.analyze.canvas
    cv.zoom_by(1.5)
    cv.fit(animate=False)
    cv.actual_size()
    cv.center_on(40, 40)
    for v in ("original", "overlay", "mask"):
        cv.set_view(v)
    st.set_overlay_opacity(0.4)
    shell.analyze.canvas.opacity_pill.slider.setValue(70)   # batch 4: the pill only
    st.set_current_image(b.uid)
    st.set_current_image(a.uid)
    QTest.mouseMove(cv, QPoint(60, 60))
    QTest.mouseMove(cv, QPoint(90, 70))
    cv.select([1])
    cv.clear_selection()
    shell.go("review")
    shell.review.canvas.zoom_by(1.25)
    shell.go("analyze")
    qtbot.wait(50)
    assert prompt.calls == []
    lock.set_active(False)
    _close_shell(shell, qtbot)


def test_open_session_blocked(env, qtbot):
    """Opening / closing a lot while analysing: always refused; "Stop
    analysis" stops the run and the lot opens once it has stopped."""
    from ui.app_state import AppState
    p1 = make_session(env, 1, label="One")
    p2 = make_session(env, 1, label="Two", lot="L-2")
    st = AppState()
    st.open_session(p1)
    qtbot.waitUntil(lambda: st.session is not None, timeout=15000)
    prompt = Prompt(session="wait", edit="continue")
    lock, stopped = _run_lock(st, prompt=prompt)
    res = []
    st.open_session(p2, on_done=res.append)
    assert res == [False] and st.session.path == p1
    assert st.close_session() is False and st.session is not None
    st.open_records([p1, p2], on_done=res.append)
    assert res == [False, False] and st.session.path == p1
    old_root = st.root
    st.set_workspace_root(env.parent / "other")
    assert st.root == old_root
    assert [k for k, _ in prompt.calls] == ["session"] * 4      # never "Continue anyway"
    assert stopped == []
    # "Stop analysis": the run is stopped, then the lot opens by itself
    prompt.session = "stop"
    st.open_session(p2, on_done=res.append)
    assert stopped == [1] and st.session.path == p1
    lock.set_active(False)                         # the queue has finished
    # round 3: opening ADDS to the analyzer (p1 stays, p2 joins it)
    qtbot.waitUntil(lambda: st.session is not None and st.record_loaded(p2), timeout=15000)
    assert st.record_loaded(p1) and res[-1] is True


def test_flush_waits_only_for_own_work(env, qtbot):
    """flush() no longer waits for unrelated background tasks on the
    global pool and keeps the event loop running while it waits."""
    from ui.workers import run_task
    st = _analysed_state(qtbot, env, 1)
    ev = threading.Event()
    run_task(lambda: ev.wait(20))                  # e.g. a slow catalog search
    t0 = time.monotonic()
    st.flush()
    assert time.monotonic() - t0 < 5.0
    ev.set()


# ======================================================================
# 6. undo stack after a re-analysis
# ======================================================================

def test_undo_after_reanalysis(env, qtbot):
    from core.grain_detector import DetectionParams
    from ui.workers import analyze_image, read_image
    st = _analysed_state(qtbot, env, 2)
    a, b = st.images()
    ga = a.result.grains[0].grain_id
    gb = b.result.grains[0].grain_id
    assert st.delete_grains(b.uid, [gb])           # an older edit on another image
    assert st.delete_grains(a.uid, [ga])
    pair = _neighbours(a.raw.label_image, st.kept_ids(a.uid))
    st.merge_grains(a.uid, pair)
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert st.undo_stack.count() == 3 and a.edits
    # re-analysis of image a
    raw = analyze_image(read_image(a.path), 2.0, DetectionParams(detection_mode="threshold"))
    st.set_result(a.uid, raw)
    qtbot.waitUntil(lambda: a.status == "done" and not st.is_filtering(), timeout=TIMEOUT)
    assert a.raw is raw and a.manual == set() and a.edits == []
    # undo skips a's stale commands and undoes b's removal only
    assert st.undo() is True
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert gb not in b.manual
    assert a.raw is raw and a.manual == set() and a.edits == []
    assert st.undo() is False and not st.undo_stack.canUndo()
    assert all(getattr(st.undo_stack.command(i), "uid", None) != a.uid
               or st.undo_stack.command(i).isObsolete()
               for i in range(st.undo_stack.count()))
    assert st.redo() is True and gb in b.manual


def _scale_scan_data(st):
    return (st.session.px_per_um, st.session.scan_rect,
            [(im.px_override, im.scale_source, im.bar_um, im.scan_rect, im.scan_source)
             for im in st.images()])


def test_undo_redo_scale_and_scan_refused_while_busy(env, qtbot):
    """Scale / scan-area changes are undo commands; undo / redo while one
    of their images is queued / running is refused BEFORE the stack moves
    (index, canUndo / canRedo and data unchanged) and works after the run."""
    from ui.app_state import ScaleCommand, ScanAreaCommand
    st = _analysed_state(qtbot, env, 2)
    a, _b = st.images()
    stack = st.undo_stack
    px0 = st.session.px_per_um
    d_start = _scale_scan_data(st)
    snap_scale = st.set_calibration_all(3.0)
    snap_scan = st.set_scan_rect_all((0, 0, 50, 50))
    assert snap_scale and snap_scan
    assert stack.count() == 2 and stack.index() == 2
    assert isinstance(stack.command(0), ScaleCommand)
    assert isinstance(stack.command(1), ScanAreaCommand)
    assert a.uid in stack.command(0).uids() and a.uid in stack.command(1).uids()
    lock, _ = _run_lock(st, busy=[a.uid], prompt=Prompt(edit="continue"))

    def unchanged(index, can_undo, can_redo, data):
        assert stack.index() == index
        assert stack.canUndo() is can_undo and stack.canRedo() is can_redo
        assert _scale_scan_data(st) == data

    # undo of the scan-area command: refused cleanly
    d2 = _scale_scan_data(st)
    assert st.undo() is False
    unchanged(2, True, False, d2)
    assert st.restore_scans(snap_scan) is False            # the toast's "Undo"
    unchanged(2, True, False, d2)
    # the run ends: undo works
    lock.set_active(False)
    assert st.undo() is True
    assert stack.index() == 1 and st.session.scan_rect is None
    d1 = _scale_scan_data(st)
    # redo of the scan-area command and undo of the scale command while busy
    lock.set_active(True)
    assert st.redo() is False
    unchanged(1, True, True, d1)
    assert st.undo() is False
    unchanged(1, True, True, d1)
    assert st.restore_scales(snap_scale) is False
    unchanged(1, True, True, d1)
    lock.set_active(False)
    assert st.undo() is True
    assert stack.index() == 0 and _scale_scan_data(st) == d_start
    assert st.session.px_per_um == px0
    # redo while busy: refused; after the run both redo
    lock.set_active(True)
    assert st.redo() is False
    unchanged(0, False, True, d_start)
    lock.set_active(False)
    assert st.redo() is True and st.session.px_per_um == 3.0
    assert st.redo() is True and _scale_scan_data(st) == d2
    assert stack.index() == 2


def test_toast_undo_of_a_buried_scale_change(env, qtbot):
    """The toast's "Undo" of a scale change with a scan change on top:
    the scale goes back, the scan change stays undoable, the stack stays
    consistent."""
    st = _analysed_state(qtbot, env, 2)
    stack = st.undo_stack
    px0 = st.session.px_per_um
    snap = st.set_calibration_all(3.0)
    st.set_scan_rect_all((0, 0, 40, 40))
    assert st.restore_scales(snap) is True
    assert st.session.px_per_um == px0
    assert st.session.scan_rect == (0, 0, 40, 40)
    assert st.restore_scales(snap) is False                   # only once
    assert st.undo() is True and st.session.scan_rect is None
    assert st.undo() is False and not stack.canUndo()        # the scale command is gone
    assert st.session.px_per_um == px0


def test_obsolete_commands_release_label_arrays(env, qtbot):
    from core.grain_detector import DetectionParams
    from ui.app_state import GrainGeometryCommand
    from ui.workers import analyze_image, read_image
    st = _analysed_state(qtbot, env, 2)
    a, b = st.images()
    pair = _neighbours(a.raw.label_image, st.kept_ids(a.uid))
    assert pair
    st.merge_grains(a.uid, pair)
    assert st.delete_grains(b.uid, [b.result.grains[0].grain_id])   # on top of it
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    merge = st.undo_stack.command(0)
    assert isinstance(merge, GrainGeometryCommand) and merge.before is not None
    raw = analyze_image(read_image(a.path), 2.0, DetectionParams(detection_mode="threshold"))
    st.set_result(a.uid, raw)
    qtbot.waitUntil(lambda: a.status == "done" and not st.is_filtering(), timeout=TIMEOUT)
    # still lower in the stack (b's removal is on top), but obsolete and empty
    assert st.undo_stack.count() == 2 and st.undo_stack.command(0) is merge
    assert merge.isObsolete() and merge.before is None and merge.after is None
    merge.undo()                                   # a safe no-op
    merge.redo()
    assert a.raw is raw and a.edits == []
    assert st.undo() is True                       # b's removal
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert st.undo() is False and not st.undo_stack.canUndo()
    assert a.raw is raw


# ======================================================================
# lock always released when a run ends
# ======================================================================

def test_run_end_releases_lock_even_if_restore_fails(env, qtbot, monkeypatch):
    shell = _open_shell(qtbot, make_session(env, 1, label="Rel"))
    confirm_setup(shell, qtbot)
    page, st = shell.analyze, shell.state
    boom = []

    def bad():
        boom.append(1)
        raise RuntimeError("refresh failed")
    shell.analyze_all()
    assert st.analysis_lock.is_active()
    monkeypatch.setattr(page, "_on_checked_changed", bad)
    with qtbot.capture_exceptions() as caught:
        qtbot.waitUntil(lambda: bool(boom) and not page.queue.is_running(), timeout=TIMEOUT)
    assert caught and "refresh failed" in str(caught[0][1])
    assert not st.analysis_lock.is_active()
    monkeypatch.undo()
    # the queue refuses to start (a cancelled thread still finishing): no lock
    monkeypatch.setattr(page.queue, "start", lambda jobs: False)
    shell.analyze_all()
    assert not st.analysis_lock.is_active()
    assert all(im.status != "queued" for im in st.images())
    monkeypatch.undo()
    _close_shell(shell, qtbot)


# ======================================================================
# ui_state.json saved off the UI thread
# ======================================================================

def test_ui_state_writer_debounced_off_thread(tmp_path):
    import json

    from data.models import write_json_atomic
    from ui.ui_state_store import UiStateWriter
    writes = []

    def slow_write(path, data):
        time.sleep(0.3)                              # a slow / scanned disk
        writes.append((threading.current_thread() is threading.main_thread(), dict(data)))
        write_json_atomic(path, data)
    w = UiStateWriter(slow_write, debounce_s=0.1)
    path = tmp_path / "ui_state.json"
    state = {"a": 0}
    t0 = time.monotonic()
    for i in range(20):
        state["a"] = i
        w.submit(path, state)
    assert time.monotonic() - t0 < 0.25              # never waits for the disk
    state["a"] = 999                                 # later edits: not in the snapshot
    assert w.pending()
    assert w.flush()
    assert not w.pending()
    assert 1 <= len(writes) <= 2 and writes[-1][1]["a"] == 19   # last write wins
    assert not any(main for main, _ in writes)
    assert json.loads(path.read_text("utf-8"))["a"] == 19
    assert not list(tmp_path.glob("*.tmp"))


def test_persist_ui_state_does_not_block_and_flushes_on_close(env, qtbot, monkeypatch):
    import json

    from data.models import write_json_atomic
    from ui import ui_state_store
    from ui.app_state import load_ui_state, ui_state_path
    from ui.ui_state_store import UiStateWriter

    def slow_write(path, data):
        time.sleep(0.35)
        write_json_atomic(path, data)
    monkeypatch.setattr(ui_state_store, "_writer", UiStateWriter(slow_write, debounce_s=5.0))
    shell = _open_shell(qtbot, make_session(env, 1, label="UiState"))
    st = shell.state
    t0 = time.monotonic()
    st.set_overlay_opacity(0.33)                     # e.g. slider released
    st.persist_ui_state()
    assert time.monotonic() - t0 < 0.2
    assert load_ui_state()["overlay_opacity"] == pytest.approx(0.33)   # reader flushes
    st.ui_state["probe_key"] = "on close"
    st.persist_ui_state()                            # 5 s debounce: still pending
    _close_shell(shell, qtbot)
    data = json.loads(ui_state_path().read_text("utf-8"))
    assert data["probe_key"] == "on close" and "last_page" in data


# ======================================================================
# 8. load shedding
# ======================================================================

def test_filters_and_autofind_use_small_pool(env, qtbot, monkeypatch):
    import ui.app_state as app_state
    from ui import workers
    assert workers.filter_pool().maxThreadCount() in (1, 2)
    assert workers.filter_pool_threads(2) == 1 and workers.filter_pool_threads(4) == 1
    assert workers.filter_pool_threads(16) == 2
    st = _analysed_state(qtbot, env, 2)
    pools = []
    real = app_state.run_task

    def spy(fn, *a, **k):
        pools.append((getattr(fn, "__name__", ""), k.get("pool")))
        return real(fn, *a, **k)
    monkeypatch.setattr(app_state, "run_task", spy)
    opts = st.session.filters
    opts.exclude_border = not opts.exclude_border
    st.set_filter_options(opts)
    st.auto_setup()
    qtbot.waitUntil(lambda: not st.is_filtering() and not st.is_setting_up(), timeout=TIMEOUT)
    mine = [p for n, p in pools if n in ("_filter_task", "setup_probe")]
    assert len(mine) >= 4 and all(p is workers.filter_pool() for p in mine)


def test_save_payload_has_no_extra_label_copy(env, qtbot):
    st = _analysed_state(qtbot, env, 1)
    im = st.images()[0]
    st._dirty.add(im.uid)
    entries, _meta = st._record_payload(st.session, st.session.records[0], {im.uid})
    snap = entries[0].result
    assert snap.label_image is im.raw.label_image              # shared, not copied
    assert snap is not im.result and snap.grains is not im.result.grains


# ======================================================================
# 9. image-details follow-ups
# ======================================================================

def _info_dict(instrument="JSM-7800F"):
    from core.image_info import ImageInfo
    info = ImageInfo()
    info._set("instrument", instrument, "metadata")
    return info.to_dict()


def test_saved_details_do_not_refill_cleared_fields(env, qtbot):
    from ui.app_state import AppState
    st = AppState()
    st.open_session(make_session(env, 1, label="Det"))
    qtbot.waitUntil(lambda: st.session is not None, timeout=15000)
    svc, doc = st.image_details, st.session
    qtbot.waitUntil(lambda: not svc._pending and svc._running is None, timeout=TIMEOUT)
    im = doc.images[0]
    doc.meta.instrument = ""                       # the operator cleared it
    im.image_info = None
    svc._batch_done(doc, [(im.uid, _info_dict(), True)], False, [im.uid], svc._gen)
    assert im.image_info is not None and doc.meta.instrument == ""   # saved: not refilled
    im.image_info = None
    svc._batch_done(doc, [(im.uid, _info_dict("Fresh SEM"), False)], False, [im.uid], svc._gen)
    assert doc.meta.instrument == "Fresh SEM"       # freshly read: fills the empty field


def test_details_reset_keeps_new_documents_bookkeeping(env, qtbot, monkeypatch):
    import ui.image_details as idt
    from ui.app_state import AppState
    gates = []

    def slow_batch(record_path, items):
        ev = threading.Event()
        gates.append(ev)
        ev.wait(20)
        return [(uid, _info_dict("Late"), False) for uid, _f, _p in items]
    monkeypatch.setattr(idt, "read_batch", slow_batch)
    st = AppState()
    st.open_session(make_session(env, 1, label="Pend"))
    qtbot.waitUntil(lambda: st.session is not None and len(gates) == 1, timeout=15000)
    svc, doc = st.image_details, st.session
    im = doc.images[0]
    assert im.uid in svc._pending
    svc.reset()                                    # e.g. the lot was closed / reopened
    svc.request(doc, [im])                         # same image asked again
    qtbot.waitUntil(lambda: len(gates) == 2, timeout=15000)
    gates[0].set()                                 # the OLD batch arrives first
    qtbot.wait(300)
    assert im.uid in svc._pending                  # still waiting for the new batch
    assert im.image_info is None                   # old reading ignored
    gates[1].set()
    qtbot.waitUntil(lambda: im.uid not in svc._pending and im.image_info is not None,
                    timeout=15000)


# ======================================================================
# stress test
# ======================================================================

#: allowed UI-thread stall; raise with GRAIN_STALL_MS on a slow / busy CI box
STALL_MS = int(os.environ.get("GRAIN_STALL_MS", "200"))


def test_stress_interactions_during_analysis(env, qtbot, monkeypatch):
    """5 images through a slow detector while hover / grain_at / view and
    opacity changes / image switches / delete + undo ("Continue anyway") /
    filter changes fire from timers: no exception, all 5 results, undo
    stack consistent, the UI thread never stalls."""
    from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QTimer
    from PySide6.QtTest import QTest
    from ui.workers import alive_analysis_threads
    shell = _open_shell(qtbot, make_session(env, 5, label="Stress"))
    st = shell.state
    confirm_setup(shell, qtbot)
    stats = _slow_detector(monkeypatch, 0.8)
    prompt = Prompt(edit="continue", session="wait")
    st.analysis_lock.prompt = prompt
    rng = random.Random(1234)
    errors = []
    pushed = {"n": 0, "undo": 0}

    def act():
        try:
            imgs = st.images()
            im = rng.choice(imgs)
            k = rng.randrange(9)
            cv = shell.analyze.canvas if shell.current_page() == "analyze" else shell.review.canvas
            if k == 0:
                QTest.mouseMove(cv, QPoint(rng.randrange(20, 300), rng.randrange(20, 300)))
            elif k == 1:
                cv.grain_at(QPointF(rng.uniform(0, 200), rng.uniform(0, 200)))
            elif k == 2:
                cv.set_view(rng.choice(["original", "overlay", "mask"]))
                # as while dragging the slider / pill: the setting is written
                # to disk once on release, not on every step
                st.set_overlay_opacity(rng.uniform(0.2, 1.0), persist=False)
            elif k == 3:
                st.set_current_image(im.uid)
            elif k == 4:
                cv.zoom_by(rng.choice([0.8, 1.25]))
            elif k in (5, 6):
                ready = [x for x in imgs if x.result is not None and x.result.grains]
                if ready:
                    x = rng.choice(ready)
                    g = rng.choice(x.result.grains).grain_id
                    if st.delete_grains(x.uid, [g]):     # refused if x is busy
                        pushed["n"] += 1
            elif k == 7 and st.undo_stack.canUndo():
                st.undo()
                pushed["undo"] += 1
            elif k == 8:
                o = st.session.filters
                o.exclude_border = not o.exclude_border
                st.set_filter_options(o)
            if rng.random() < 0.1:
                shell.go(rng.choice(["analyze", "review"]))
        except Exception as exc:                    # noqa: BLE001
            errors.append(repr(exc))

    gaps = []
    clock = QElapsedTimer()

    def beat():
        if clock.isValid():
            gaps.append(clock.restart())
        else:
            clock.start()
    hb = QTimer()
    hb.timeout.connect(beat)
    driver = QTimer()
    driver.timeout.connect(act)
    q = shell.analyze.queue
    done = []
    q.job_finished.connect(lambda uid, _r: done.append(uid))
    shell.analyze_all()
    assert q.is_running()
    hb.start(20)
    driver.start(35)
    qtbot.waitUntil(lambda: not q.is_running(), timeout=TIMEOUT)
    driver.stop()
    hb.stop()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert errors == []
    assert pushed["n"] >= 3 and pushed["undo"] >= 1, pushed      # it really edited
    assert len(done) == 5 and stats["calls"] == 5 and stats["peak"] == 1
    assert all(im.status == "done" and im.result is not None for im in st.images())
    # the gate asked once for the run and was never needed for viewing
    assert [k for k, _ in prompt.calls] in ([], ["edit"])
    # undo stack consistent: every live command belongs to an existing image,
    # and undoing everything puts every hand-removed grain back
    stack = st.undo_stack
    uids = {im.uid for im in st.images()}
    for i in range(stack.count()):
        cmd = stack.command(i)
        assert cmd.isObsolete() or getattr(cmd, "uid", None) in uids
    guard = 0
    while st.undo() and guard < 500:
        guard += 1
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    assert not stack.canUndo()
    assert all(not im.manual for im in st.images()), [im.manual for im in st.images()]
    # the UI thread kept up (heartbeat every 20 ms).  Robust on a loaded
    # machine: the typical gap (median) and the 90th percentile must stay
    # small; a single worst gap only has to stay clear of a real freeze.
    assert len(gaps) >= 20, "no heartbeat"
    g = sorted(gaps)
    median = g[len(g) // 2]
    p90 = g[min(len(g) - 1, int(len(g) * 0.9))]
    assert median <= max(60, STALL_MS // 3), (median, g[-5:])
    assert p90 <= STALL_MS, (p90, g[-5:])
    assert g[-1] <= max(1500, 5 * STALL_MS), (g[-1], g[-5:])
    assert alive_analysis_threads() == 0
    _close_shell(shell, qtbot)
