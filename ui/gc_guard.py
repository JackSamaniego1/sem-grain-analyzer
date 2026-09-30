"""
Python's cyclic garbage collector runs on the GUI thread only.

Why: Python starts an automatic collection on whichever thread happens to
allocate when the allocation counter passes its threshold -- often a pool
thread (image reads, saves, data-bar OCR) or the ui-state writer.  Qt objects
kept alive only by a reference cycle (a window's ``AppState`` and its
children: lambdas / bound methods that capture ``self`` make such cycles
everywhere) are then destroyed ON THAT THREAD.  A ``QObject`` destroyed off
its own thread cannot unregister its running timers or posted events
("QObject::~QObject: Timers cannot be stopped from another thread"); the GUI
thread later delivers the timer event to the freed object -> access
violation, a hard crash with no Python traceback (seen after closing a
window, and in the test suite).

Fix (the usual PySide/PyQt remedy): automatic collection is switched off and
a GUI-thread timer runs the same threshold-based collections Python would
have run, so every Qt object freed by the collector is freed on the thread
that owns it.  Nothing else changes: memory freed by reference counting
(nearly everything, including image arrays) is freed at once as before.
"""
from __future__ import annotations

import gc
import threading
from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, QTimer

#: how often the GUI thread checks whether a collection is due
CHECK_INTERVAL_MS = 500


def collect_if_due() -> int:
    """GUI thread: run the collection Python's own thresholds ask for
    (generation 0, 1 or 2).  Returns the number of objects collected."""
    if threading.current_thread() is not threading.main_thread():
        return 0                                   # never off the GUI thread
    c0, c1, c2 = gc.get_count()
    t0, t1, t2 = gc.get_threshold()
    if c0 <= t0:
        return 0
    gen = 0
    if c1 > t1:
        gen = 1
        if c2 > t2:
            gen = 2
    return gc.collect(gen)


class _GuiThreadCollector(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_INTERVAL_MS)
        self.timer.timeout.connect(collect_if_due)
        self.timer.start()


_collector: Optional[_GuiThreadCollector] = None


def install() -> bool:
    """Switch automatic collection off and collect on the GUI thread instead.
    Idempotent; needs the QApplication (returns False without one).  Call
    from the GUI thread."""
    global _collector
    app = QCoreApplication.instance()
    if app is None or threading.current_thread() is not threading.main_thread():
        return False
    try:
        alive = _collector is not None and _collector.timer.isActive()
    except RuntimeError:                           # the previous app was destroyed
        alive = False
    if not alive:
        _collector = _GuiThreadCollector(app)
    gc.disable()
    return True


def is_installed() -> bool:
    try:
        return (_collector is not None and _collector.timer.isActive()
                and not gc.isenabled())
    except RuntimeError:
        return False


__all__ = ["install", "is_installed", "collect_if_due", "CHECK_INTERVAL_MS"]
