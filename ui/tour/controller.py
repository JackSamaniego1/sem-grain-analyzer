"""
TourController (UI-10): drives the guided tour over the AppShell.

* Resolves each step's target widgets by objectName, switching to the step's
  page first; hidden/missing targets fall back (``TourStep.fallbacks``) and a
  step with nothing to show is skipped in the direction of travel.
* Follows the target while the step is open (window resize, page slide,
  scrolling, layout changes) with a light 120 ms poll.
* "Don't show this on startup" lives in the app's local ui_state store
  (``AppState.ui_state["tour"]``) — never on the network.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QTimer, Signal
from PySide6.QtWidgets import QAbstractScrollArea, QScrollArea, QWidget

from ui.design.tokens import MOTION
from ui.tour.overlay import SPOT_PAD, TourOverlay
from ui.tour.steps import TourStep, default_steps
from ui.widgets._base import duration

FOLLOW_MS = 120


class TourController(QObject):
    """Start / navigate / close the tour on ``shell`` (an AppShell-like window)."""

    started = Signal()
    step_changed = Signal(int)          # index into ``steps``
    finished = Signal(bool)             # True = completed, False = skipped

    def __init__(self, shell, steps: Optional[Sequence[TourStep]] = None) -> None:
        super().__init__(shell)
        self.shell = shell
        self.steps: List[TourStep] = list(steps if steps is not None else default_steps())
        self.overlay: Optional[TourOverlay] = None
        self.index = -1
        self._token = 0
        self._shown_rect = QRectF()
        self._follow = QTimer(self)
        self._follow.setInterval(FOLLOW_MS)
        self._follow.timeout.connect(self._track)

    # ------------------------------------------------------------------ settings
    def _store(self) -> dict:
        ui = getattr(self.shell.state, "ui_state", None)
        if ui is None:
            return {}
        d = ui.get("tour")
        if not isinstance(d, dict):
            d = {}
            ui["tour"] = d
        return d

    def dont_show(self) -> bool:
        return bool(self._store().get("dont_show", False))

    def set_dont_show(self, on: bool) -> None:
        self._store()["dont_show"] = bool(on)
        self._persist()

    def _persist(self) -> None:
        try:
            self.shell.state.persist_ui_state()
        except Exception:
            pass

    def should_autostart(self) -> bool:
        return not self.dont_show()

    # ------------------------------------------------------------------ public API
    def is_active(self) -> bool:
        return self.overlay is not None and self.index >= 0

    def current_step(self) -> Optional[TourStep]:
        return self.steps[self.index] if self.is_active() else None

    def start(self, index: int = 0) -> None:
        """Open the tour (replays even when "don't show" is set)."""
        if not self.steps:
            return
        if self.overlay is None:
            self.overlay = TourOverlay(self.shell)
            ov = self.overlay
            ov.next_requested.connect(self.next)
            ov.back_requested.connect(self.back)
            ov.skip_requested.connect(self.skip)
            ov.callout.dont_show.toggled.connect(self.set_dont_show)
            ov.fade_in()
            self._follow.start()
            self.started.emit()
        self.index = -1
        self._go(max(0, min(index, len(self.steps) - 1)), +1)

    def next(self) -> None:
        if not self.is_active():
            return
        if self.index >= len(self.steps) - 1:
            self.finish()
        else:
            self._go(self.index + 1, +1)

    def back(self) -> None:
        if self.is_active() and self.index > 0:
            self._go(self.index - 1, -1)

    def skip(self) -> None:
        """Close early; a toast offers Undo (resume where the user left)."""
        if not self.is_active():
            return
        at = self.index
        self._close(False)
        toasts = getattr(self.shell, "toasts", None)
        if toasts is not None and at < len(self.steps) - 1:
            try:
                toasts.show_toast("Tour closed", "Replay it any time from Help › Show tour.",
                                  "info", "Undo", lambda: self.start(at), timeout_ms=6000)
            except Exception:
                pass

    def finish(self) -> None:
        if self.is_active():
            self._close(True)

    # ------------------------------------------------------------------ steps
    def _counted(self) -> List[int]:
        return [i for i, s in enumerate(self.steps) if not s.centred]

    def _go(self, index: int, direction: int) -> None:
        self._token += 1
        token = self._token
        if index < 0:
            index, direction = 0, +1
        if index >= len(self.steps):
            self.finish()
            return
        step = self.steps[index]
        switched = False
        if step.page and hasattr(self.shell, "go"):
            try:
                before = self.shell.current_page()
                self.shell.go(step.page)
                switched = before != step.page
            except Exception:
                pass
        if switched and duration(MOTION.base) > 0:
            if self.overlay is not None:
                self.overlay.hide_callout()
            # let the page transition settle before measuring the target
            QTimer.singleShot(duration(MOTION.base) + 30,
                              lambda: token == self._token and self._present(index, direction))
        else:
            self._present(index, direction)

    def _present(self, index: int, direction: int) -> None:
        if self.overlay is None:
            return
        step = self.steps[index]
        rect = QRectF()
        if not step.centred:
            rect = self._resolve(step, scroll=True)
            if rect.isEmpty():
                nxt = index + direction
                if 0 <= nxt < len(self.steps):
                    self._go(nxt, direction)
                elif direction < 0:
                    self._go(index + 1, +1)
                else:
                    self.finish()
                return
        self.index = index
        self._shown_rect = QRectF(rect)
        ov = self.overlay
        target = rect.adjusted(-SPOT_PAD, -SPOT_PAD, SPOT_PAD, SPOT_PAD) \
            if not rect.isEmpty() else QRectF()
        ov.set_spot(target)
        counted = self._counted()
        first, last = index == 0, index == len(self.steps) - 1
        if index in counted:
            n = counted.index(index)
            step_text, dots = f"Step {n + 1} of {len(counted)}", (n, len(counted))
        elif step.kind == "welcome":
            step_text, dots = "Guided tour · about 1 minute", (0, 0)
        else:
            step_text, dots = "", (0, 0)
        cb = ov.callout.dont_show
        cb.blockSignals(True)
        cb.setChecked(self.dont_show())
        cb.blockSignals(False)
        ov.show_callout(step.text("title", self.shell), step.text("body", self.shell),
                        step_text, dots, step.centred, step.icon, first, last,
                        show_check=first or last, target=target)
        ov.set_pulsing(step.kind == "click")
        ov.setFocus()
        ov.callout.btn_next.setFocus()
        self.step_changed.emit(index)

    def resolve(self, step: TourStep) -> QRectF:
        """Spotlight rectangle (overlay coords) for ``step`` as things stand now."""
        return self._resolve(step, scroll=False)

    def _resolve(self, step: TourStep, scroll: bool) -> QRectF:
        for names in (step.targets,) + tuple(step.fallbacks):
            r = self._union(names, scroll)
            if not r.isEmpty():
                return r
        return QRectF()

    def _union(self, names: Sequence[str], scroll: bool) -> QRectF:
        out = QRectF()
        for name in names:
            w = self.shell.findChild(QWidget, name)
            if w is None or not w.isVisible() or w.window() is not self.shell:
                continue
            if scroll:
                _ensure_visible(w)
            r = self._visible_rect(w)
            if not r.isEmpty():
                out = r if out.isEmpty() else out.united(r)
        return out

    def _visible_rect(self, w: QWidget) -> QRectF:
        """``w``'s rectangle in shell coords, clipped by every ancestor."""
        shell = self.shell
        r = QRect(w.mapTo(shell, QPoint(0, 0)), w.size())
        p = w.parentWidget()
        while p is not None and p is not shell:
            r = r.intersected(QRect(p.mapTo(shell, QPoint(0, 0)), p.size()))
            p = p.parentWidget()
        return QRectF(r) if r.width() > 2 and r.height() > 2 else QRectF()

    def _track(self) -> None:
        """Follow the target (resize, page slide, scrolling, layout changes)."""
        if not self.is_active() or self.overlay.is_animating():
            return
        step = self.steps[self.index]
        if step.centred:
            self.overlay.reposition_callout(QRectF())
            return
        r = self.resolve(step)
        if r == self._shown_rect:
            return
        self._shown_rect = QRectF(r)
        target = r.adjusted(-SPOT_PAD, -SPOT_PAD, SPOT_PAD, SPOT_PAD) if not r.isEmpty() \
            else QRectF()
        self.overlay.set_spot(target, animate=False)
        self.overlay.reposition_callout(target)

    def _close(self, completed: bool) -> None:
        self._token += 1
        self._follow.stop()
        ov, self.overlay = self.overlay, None
        self.index = -1
        self._persist()
        if ov is not None:
            def gone(o=ov):
                try:
                    o.hide()
                    o.deleteLater()
                except RuntimeError:
                    pass
            ov.fade_out(gone)
        self.finished.emit(completed)


def _ensure_visible(w: QWidget) -> None:
    """Scroll any enclosing scroll area so ``w`` is in view."""
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea):
            try:
                p.ensureWidgetVisible(w, 16, 24)
            except Exception:
                pass
        elif isinstance(p, QAbstractScrollArea):
            pass
        p = p.parentWidget()


__all__ = ["TourController"]
