"""
Hard crash (access violation) during the test run / after closing a window:
Python's cycle collector ran on a pool thread and destroyed Qt objects of a
closed window there (an AppState and its active QTimer).  The timer stayed
registered on the GUI thread, whose next event processing delivered it to the
freed object.  ui/gc_guard.py keeps every collection on the GUI thread.
"""
import gc
import threading
import time
import weakref

import pytest

pytest.importorskip("pytestqt")


class _Holder:
    """Cyclic garbage."""

    def __init__(self):
        self.me = self


def _make_cyclic_qobject_with_running_timer():
    from PySide6.QtCore import QObject, QTimer

    class Svc(QObject):
        def __init__(self):
            super().__init__()
            self.t = QTimer(self)
            self.t.setSingleShot(True)
            self.t.setInterval(30)
            self.t.timeout.connect(self.tick)      # bound method: a cycle
            self.me = self

        def tick(self):
            pass

    s = Svc()
    s.t.start()
    return weakref.ref(s)


def _churn_on_worker_thread(n=200_000):
    """Allocate like a busy pool thread: far past every gc threshold, so an
    enabled collector WOULD run here (on this thread)."""
    def work():
        junk = []
        for i in range(n):
            junk.append(_Holder())
            if len(junk) > 1000:
                junk.clear()
    th = threading.Thread(target=work)
    th.start()
    th.join()


def test_cycle_collection_never_runs_on_a_worker_thread(qapp):
    from ui import gc_guard
    assert gc_guard.install()
    assert gc_guard.is_installed() and not gc.isenabled()

    ref = _make_cyclic_qobject_with_running_timer()
    assert ref() is not None
    _churn_on_worker_thread()
    # Without the guard the worker thread collected the QObject (and freed its
    # still-registered timer there -> access violation on the next event pass).
    assert ref() is not None, "a Qt object was garbage-collected on a worker thread"

    gc.collect()                                   # GUI thread: the safe place
    assert ref() is None
    end = time.monotonic() + 0.15                  # the timer's due time passes
    while time.monotonic() < end:
        qapp.processEvents()                       # would crash if freed off-thread


def test_gui_thread_timer_collects_when_due(qapp, qtbot):
    from ui import gc_guard
    gc_guard.install()
    ref = _make_cyclic_qobject_with_running_timer()
    # push the allocation counter past the gen-0 threshold
    for _ in range(gc.get_threshold()[0] * 3):
        _Holder()
    qtbot.waitUntil(lambda: ref() is None, timeout=5000)


def test_collect_if_due_does_nothing_off_the_gui_thread(qapp):
    from ui import gc_guard
    out = []
    for _ in range(gc.get_threshold()[0] * 3):
        _Holder()
    th = threading.Thread(target=lambda: out.append(gc_guard.collect_if_due()))
    th.start()
    th.join()
    assert out == [0]


def test_app_shell_installs_the_guard(qapp, qtbot, tmp_path, monkeypatch):
    from data.models import AppSettings
    from data.settings import save_settings
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    save_settings(AppSettings(workspace_root=str(tmp_path / "ws"), operator="T"))
    import ui.gc_guard as gg
    monkeypatch.setattr(gg, "_collector", None)
    gc.enable()
    try:
        from ui.app_shell import AppShell
        from ui.app_state import AppState
        w = AppShell(AppState(), probe_device=False)
        qtbot.addWidget(w)
        assert gg.is_installed() and not gc.isenabled()
    finally:
        gc.disable()
