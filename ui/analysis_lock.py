"""
UPDATE 4 item 7 — ONE gate for changes made while images are being analysed.

``AppState.analysis_lock`` is driven by the Analyze page's ``busy_changed``
signal.  Every method that changes results or analysis settings asks
:meth:`AnalysisLock.guard` first; viewing (pan, zoom, display mode, overlay
opacity, switching image, hover, selection, opening Review) never does.

* While a run is active the first edit shows one dialog per run: "Wait"
  (default, nothing changes) or "Continue anyway" (holds until the run ends),
  plus "Don't warn again" (``AppState.warn_edit_during_analysis``; can be
  switched back on in Settings).
* Always blocked, whatever the answer: editing an image that is queued or
  being analysed (a short notice explains why), and session-level actions
  (open / close a lot or session, start another analysis).  For those the
  operator may stop the analysis; the action then runs once it has stopped.

Headless tests replace :attr:`AnalysisLock.prompt` with a function returning
``(answer, dont_warn)``.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

_log = logging.getLogger(__name__)

WAIT, CONTINUE, STOP = "wait", "continue", "stop"

EDIT_TITLE = "Images are being analysed"
EDIT_TEXT = (
    "Images are being analysed right now.\n\n"
    "{action} while an analysis is running can be unreliable on low-performance PCs - "
    "the computer may slow down a lot or stop responding for a while.\n\n"
    "Wait until the analysis has finished, or continue anyway.")
BUSY_TITLE = "This image is being analysed"
BUSY_TEXT = ("{action} is not possible on an image that is waiting for analysis or being "
             "analysed. It becomes available as soon as that image is finished.")
SESSION_TITLE = "Analysis is running"
SESSION_TEXT = ("{action} is not possible while images are being analysed.\n\n"
                "Stop the analysis now (images already finished are kept), or keep "
                "analysing and try again when it has finished.")
#: tooltip of controls that are locked for the run
LOCKED_TIP = "Not available while images are being analysed - wait for the analysis to finish."

Answer = Tuple[str, bool]


def build_prompt(kind: str, action: str, parent=None):
    """The question box, not shown yet: ``(box, {answer: button}, checkbox)``.
    ``kind``: "edit" | "session"."""
    from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox
    parent = parent or QApplication.activeWindow()
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    if kind == "session":
        box.setWindowTitle(SESSION_TITLE)
        box.setText(SESSION_TEXT.format(action=action))
        keep = box.addButton("Keep analysing", QMessageBox.RejectRole)
        stop = box.addButton("Stop analysis", QMessageBox.AcceptRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        keep.setToolTip("The analysis continues; nothing else changes")
        stop.setToolTip("Stop the analysis, then continue with this action")
        return box, {WAIT: keep, STOP: stop}, None
    box.setWindowTitle(EDIT_TITLE)
    box.setText(EDIT_TEXT.format(action=action))
    wait = box.addButton("Wait", QMessageBox.RejectRole)
    go = box.addButton("Continue anyway", QMessageBox.AcceptRole)
    wait.setToolTip("Do nothing now; make the change after the analysis has finished")
    go.setToolTip("Make the change now (not asked again during this analysis)")
    box.setDefaultButton(wait)
    box.setEscapeButton(wait)
    cb = QCheckBox("Don't warn again")
    cb.setToolTip("Can be switched back on in Settings")
    box.setCheckBox(cb)
    return box, {WAIT: wait, CONTINUE: go}, cb


def headless() -> bool:
    """No screen (offscreen test runs, headless renders): nobody could
    answer a modal dialog, so the safe default answer is used instead."""
    from PySide6.QtGui import QGuiApplication
    return (QGuiApplication.platformName() or "").lower() in ("offscreen", "minimal")


def _default_prompt(kind: str, action: str, parent=None) -> Answer:
    """Modal question (GUI thread); the default button is the safe one."""
    if headless():
        return WAIT, False
    box, buttons, cb = build_prompt(kind, action, parent)
    box.exec()
    clicked = box.clickedButton()
    answer = next((a for a, b in buttons.items() if b is clicked), WAIT)
    return answer, bool(cb is not None and cb.isChecked())


def show_locked(w, locked: bool, why: str = LOCKED_TIP) -> None:
    """Tooltip of a widget / QAction that is locked for the run (the caller
    decides enabled state; the original tooltip comes back on unlock)."""
    if w is None:
        return
    orig = w.property("_unlocked_tip")
    if locked:
        if orig is None:
            w.setProperty("_unlocked_tip", w.toolTip() or "")
        w.setToolTip(why)
    elif orig is not None:
        w.setToolTip(str(orig))
        w.setProperty("_unlocked_tip", None)


class AnalysisLock(QObject):
    """See module docstring."""

    lock_changed = Signal(bool)        # True while an analysis run is active
    blocked = Signal(str, str)         # action, reason: "busy" | "session" | "declined"

    def __init__(self, state) -> None:
        super().__init__(state)
        self.state = state
        self._active = False
        self._continue = False             # "Continue anyway" for this run
        self._busy_fn: Optional[Callable[[], Iterable]] = None
        self._stop_fn: Optional[Callable[[], None]] = None
        self._after: List[Callable[[], None]] = []
        #: ``prompt(kind, action) -> (answer, dont_warn)``; None = dialog
        self.prompt: Optional[Callable[[str, str], Answer]] = None
        self.asked = 0                     # dialogs shown (tests)

    # ------------------------------------------------------------ wiring
    def bind(self, busy_uids: Optional[Callable[[], Iterable]] = None,
             stop: Optional[Callable[[], None]] = None) -> None:
        """The Analyze page tells the lock which images are queued / running
        and how to stop the run."""
        if busy_uids is not None:
            self._busy_fn = busy_uids
        if stop is not None:
            self._stop_fn = stop

    def set_active(self, on: bool) -> None:
        on = bool(on)
        if on == self._active:
            return
        self._active = on
        self._continue = False
        self.lock_changed.emit(on)
        if not on and self._after:
            # after the page has finished handling the end of the run
            QTimer.singleShot(0, self, self._run_after)

    def _run_after(self) -> None:
        if self._active:
            return                             # a new run started meanwhile
        todo, self._after = self._after, []
        for fn in todo:
            try:
                fn()
            except RuntimeError:               # receiver destroyed meanwhile
                pass

    # ------------------------------------------------------------ queries
    def is_active(self) -> bool:
        return self._active

    def busy_uids(self) -> Set:
        if not self._active or self._busy_fn is None:
            return set()
        try:
            return {u for u in self._busy_fn() if u is not None}
        except RuntimeError:
            return set()

    def is_busy(self, uid) -> bool:
        return uid is not None and uid in self.busy_uids()

    def continued(self) -> bool:
        return self._active and self._continue

    def cancel_pending(self) -> None:
        """Forget actions waiting for the run to end (the window closes)."""
        self._after.clear()

    def after_idle(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` once the current run has ended (now when idle)."""
        if not self._active:
            fn()
        else:
            self._after.append(fn)

    # ------------------------------------------------------------ gates
    def guard(self, action: str, uids: Optional[Iterable] = None) -> bool:
        """May ``action`` (plain words, e.g. "Remove grains") change data now?
        ``uids`` = the images it changes (None = none in particular)."""
        if not self._active:
            return True
        hit = [u for u in (uids or ()) if u is not None and u in self.busy_uids()]
        if hit:
            self.state.message.emit(BUSY_TITLE, BUSY_TEXT.format(action=action), "info")
            self.blocked.emit(action, "busy")
            return False
        if self._continue or not self.state.warn_edit_during_analysis:
            return True
        answer, dont_warn = self._ask("edit", action)
        if dont_warn:
            self.state.set_warn_edit_during_analysis(False)
        if answer == CONTINUE:
            self._continue = True
            return True
        self.blocked.emit(action, "declined")
        return False

    def guard_session(self, action: str, retry: Optional[Callable[[], None]] = None) -> bool:
        """Session-level actions: always blocked while a run is active.  The
        operator may stop the analysis; ``retry`` then runs once it has
        stopped."""
        if not self._active:
            return True
        answer, _ = self._ask("session", action)
        if answer == STOP:
            if retry is not None:
                self._after.append(retry)
            if self._stop_fn is not None:
                self._stop_fn()
        self.blocked.emit(action, "session")
        return False

    def _ask(self, kind: str, action: str) -> Answer:
        self.asked += 1
        fn = self.prompt or _default_prompt
        try:
            out = fn(kind, action)
        except Exception:                       # a broken dialog must not edit
            _log.exception("analysis-lock prompt failed")
            return WAIT, False
        if isinstance(out, str):
            return out, False
        return str(out[0]), bool(out[1])


__all__ = ["AnalysisLock", "show_locked", "build_prompt", "headless", "WAIT", "CONTINUE", "STOP", "LOCKED_TIP",
           "EDIT_TEXT", "BUSY_TEXT", "SESSION_TEXT"]
