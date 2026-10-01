"""
TourController (UI-10, action-driven since batch 4 / D-39): drives the
guided tour over the AppShell.

* On start the Tutorial job is prepared (``data.tutorial``, off the GUI
  thread): job "Tutorial" › part "Sample part" › lot "Lot 1" with the three
  bundled sample images, reset when it was already set up or analysed.
* Each step connects its ``advance_on`` hook and moves on when the user does
  the action (AppState / page signals, never timers) -- there is no Next /
  Back.  ``advance()`` is the one way forward; ``skip()`` and ``finish()``
  close.  A step whose action is already done advances at once.
* Resolves each step's target widgets by objectName, switching to the step's
  page first; hidden/missing targets fall back (``TourStep.fallbacks``) and a
  step with nothing to show is skipped.
* Follows the target while the step is open (window resize, page slide,
  scrolling, layout changes) with a light 120 ms poll.
* "Don't show this on startup" lives in the app's local ui_state store
  (``AppState.ui_state["tour"]``) — never on the network.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QTimer, Signal
from PySide6.QtWidgets import QAbstractScrollArea, QScrollArea, QWidget

from ui.design.tokens import MOTION
from ui.tour.overlay import SPOT_PAD, TourOverlay
from ui.tour.steps import TourStep, default_steps
from ui.widgets._base import duration

FOLLOW_MS = 120


class TourController(QObject):
    """Start / advance / close the tour on ``shell`` (an AppShell-like window)."""

    started = Signal()
    step_changed = Signal(int)          # index into ``steps``
    finished = Signal(bool)             # True = completed, False = skipped
    tutorial_ready = Signal(object)     # the Tutorial job's record folder
    noted = Signal(str)                 # an action the tour watches for happened

    def __init__(self, shell, steps: Optional[Sequence[TourStep]] = None,
                 hint: bool = False, watchers=None) -> None:
        """``hint=True``: a one-off spotlight (e.g. the UX-02 analysis gate)
        using the tour's look -- no step counter, no "don't show" box, a
        single "Got it" button and no "Tour closed" message."""
        super().__init__(shell)
        self.shell = shell
        self.hint = bool(hint)
        self.steps: List[TourStep] = list(steps if steps is not None else default_steps())
        if watchers is None and steps is None and not hint:
            from ui.tour.steps import default_watchers
            watchers = default_watchers()
        self._watchers = tuple(watchers or ())
        self._watch_off: list = []
        #: actions done since the tour started (see ui.tour.steps.watch_actions)
        self.seen: set = set()
        self.overlay: Optional[TourOverlay] = None
        self.index = -1
        self._token = 0
        self._shown_rect = QRectF()
        self._off = None                    # disconnect of the open step's advance_on
        self.tutorial_record: Optional[Path] = None
        self.tutorial_failed = False        # bundled sample images missing
        self._prep_gen = 0
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

    def start(self, index: int = 0, prepare: bool = True) -> None:
        """Open the tour (replays even when "don't show" is set).  Starting
        the full tour from the beginning (``prepare``) first creates or
        resets the Tutorial job."""
        if not self.steps:
            return
        if self.overlay is None:
            self.overlay = TourOverlay(self.shell)
            ov = self.overlay
            ov.primary_requested.connect(self._on_primary)
            ov.skip_requested.connect(self.skip)
            ov.callout.dont_show.toggled.connect(self.set_dont_show)
            ov.fade_in()
            self._follow.start()
            for w in self._watchers:
                try:
                    self._watch_off.append(w(self.shell, self))
                except Exception:
                    pass
            self.started.emit()
        if prepare and not self.hint and index == 0:
            self.seen = set()
            self.prepare_tutorial()
        elif not self.hint and self.tutorial_record is not None:
            self._scope_to_tutorial()
        self._unbind()
        self.index = -1
        self._go(max(0, min(index, len(self.steps) - 1)), +1)

    def is_waiting_on(self, hook) -> bool:
        """The open step is the one whose ``advance_on`` is ``hook`` (or wraps it)."""
        s = self.current_step()
        if s is None or hook is None or s.advance_on is None:
            return False
        return s.advance_on is hook or getattr(s.advance_on, "inner", None) is hook

    def note(self, key: str) -> None:
        """An action the steps may ask for happened (kept for later steps)."""
        if not key:
            return
        self.seen.add(key)
        self.noted.emit(key)

    def advance(self) -> None:
        """The open step's action happened: on to the next step."""
        if not self.is_active():
            return
        if self.index >= len(self.steps) - 1:
            self.finish()
        else:
            self._go(self.index + 1, +1)

    def refresh(self) -> None:
        """Re-place the spotlight and re-read the open step's texts (e.g.
        the run step moves to the progress card while analysing)."""
        if self.is_active() and not self.steps[self.index].centred:
            self._present(self.index, +1, rebind=False)

    def skip(self) -> None:
        """Close early (the Tutorial job stays); a toast offers Undo."""
        if not self.is_active():
            return
        at = self.index
        self._close(False)
        toasts = getattr(self.shell, "toasts", None)
        if toasts is not None and at < len(self.steps) - 1 and not self.hint:
            try:
                toasts.show_toast("Tour closed", "Replay it any time from Help › Show tour.",
                                  "info", "Undo", lambda: self.start(at, prepare=False),
                                  timeout_ms=6000)
            except Exception:
                pass

    def finish(self) -> None:
        if self.is_active():
            self._close(True)

    def _on_primary(self) -> None:
        """Start tour (welcome) / Done (finish) / Got it (hint)."""
        step = self.current_step()
        if step is None:
            return
        if self.hint or step.kind == "finish" or self.index >= len(self.steps) - 1:
            self.finish()
        else:
            self.advance()                  # Start tour / Got it

    # ------------------------------------------------------------------ tutorial job
    def prepare_tutorial(self) -> None:
        """Create or reuse the Tutorial job, resetting it when it was set up
        or analysed before, then select it in Projects.  Off the GUI thread."""
        st = self.shell.state
        self._prep_gen += 1
        gen = self._prep_gen
        root = st.root
        self.tutorial_record, self.tutorial_failed = None, False
        if not self.hint:
            # round 3c: until the Tutorial job is ready, "all images" actions
            # touch nothing (never the operator's own loaded images)
            try:
                st.set_scope([])
            except Exception:
                pass
        try:
            from data.tutorial import find_tutorial_record
            rec = find_tutorial_record(root)
        except Exception:
            rec = None
        s = st.session
        if rec is not None and s is not None and any(_same(r.path, rec)
                                                     for r in (s.records or [])):
            if self._busy():
                self.tutorial_record = Path(rec)     # being analysed: leave it as it is
                self._scope_to_tutorial()
                self.tutorial_ready.emit(self.tutorial_record)
                return
            # a reset needs it out of the analyzer -- round 3: only the
            # Tutorial job's images, never the user's other loaded images
            st.drop_records([rec])
        try:
            operator = st.operator() or ""
        except Exception:
            operator = ""

        def work():
            from data.tutorial import ensure_tutorial_job
            return ensure_tutorial_job(root, operator=operator, reset=True)

        def done(res):
            if gen != self._prep_gen:
                return
            self.tutorial_record = Path(res["record"])
            if self.is_active():
                self._scope_to_tutorial()
            try:
                self.shell.projects.reload()
                self.shell.projects.select_node(self.tutorial_record)
            except Exception:
                pass
            self.tutorial_ready.emit(self.tutorial_record)
            self.refresh()

        def failed(msg):
            if gen != self._prep_gen:
                return
            self.tutorial_record = None
            self.tutorial_failed = True
            try:
                self.shell.state.set_scope(None)    # no Tutorial job: any session
            except Exception:
                pass
            toasts = getattr(self.shell, "toasts", None)
            if toasts is not None:
                from core.resources import REINSTALL_MESSAGE
                first = (msg or "").strip().splitlines()
                text = REINSTALL_MESSAGE if "reinstall" in (msg or "").lower() or not first \
                    else first[-1]
                toasts.show_toast("Tutorial job not available", text, "warning")

        from ui.workers import run_task
        run_task(work, on_done=done, on_error=failed)

    def _scope_to_tutorial(self) -> None:
        """Round 3c: while the tour runs, the wizard's "All images" steps,
        Analyze all and the step states act on the Tutorial job ONLY -- the
        operator's own loaded images are never touched."""
        if self.hint or self.tutorial_record is None:
            return
        try:
            self.shell.state.set_scope([self.tutorial_record])
        except Exception:
            pass

    def _show_scoped_image(self) -> None:
        """Round 3c: the tour's Analyze / Review steps act on the image shown
        -- make it a Tutorial image (never one of the operator's own)."""
        if self.hint:
            return
        try:
            st = self.shell.state
            if not st.scope_active():
                return
            mine = st.scoped_images()
            cur = st.current_image()
            if mine and (cur is None or not st.in_scope(cur)):
                st.set_current_image(mine[0].uid)
        except Exception:
            pass

    def _busy(self) -> bool:
        try:
            return not self.shell.analyze.queue.is_idle()
        except Exception:
            return False

    # ------------------------------------------------------------------ steps
    def _counted(self) -> List[int]:
        return [i for i, s in enumerate(self.steps) if not s.centred]

    def _unbind(self) -> None:
        off, self._off = self._off, None
        if off is not None:
            try:
                off()
            except Exception:
                pass

    def _go(self, index: int, direction: int) -> None:
        self._unbind()
        self._token += 1
        token = self._token
        if index < 0:
            index, direction = 0, +1
        if index >= len(self.steps):
            self.finish()
            return
        step = self.steps[index]
        switched = False
        self._show_scoped_image()
        if step.page == "analyze":
            try:                            # the wizard's steps reflect the data now
                self.shell.analyze.sync_wizard()
            except Exception:
                pass
        if step.prepare is not None:
            try:
                step.prepare(self.shell)
            except Exception:
                pass
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

    def _present(self, index: int, direction: int, rebind: bool = True) -> None:
        if self.overlay is None:
            return
        step = self.steps[index]
        rect = QRectF()
        if not step.centred:
            rect = self._resolve(step, scroll=True)
            if rect.isEmpty() and step.wait and rebind:
                pass                        # shown now; the spotlight lands when it appears
            elif rect.isEmpty():
                if not rebind:
                    return
                nxt = index + direction
                if 0 <= nxt < len(self.steps):
                    self._go(nxt, direction)
                else:
                    self.finish()
                return
        self.index = index
        self._shown_rect = QRectF(rect)
        self._shown_names = step.live_targets(self.shell)
        ov = self.overlay
        self._shown_areas = self._area_rects(step)
        ov.set_areas(self._shown_areas)
        target = rect.adjusted(-SPOT_PAD, -SPOT_PAD, SPOT_PAD, SPOT_PAD) \
            if not rect.isEmpty() else QRectF()
        ov.set_spot(target, animate=rebind)
        self._avoid_toasts(target)
        counted = self._counted()
        first, last = index == 0, index == len(self.steps) - 1
        if self.hint:
            step_text, dots = "", (0, 0)
        elif index in counted:
            n = counted.index(index)
            step_text, dots = f"Step {n + 1} of {len(counted)}", (n, len(counted))
        elif step.kind == "welcome":
            step_text, dots = "Guided tour · about 3 minutes", (0, 0)
        else:
            step_text, dots = "", (0, 0)
        cb = ov.callout.dont_show
        cb.blockSignals(True)
        cb.setChecked(self.dont_show())
        cb.blockSignals(False)
        ov.show_callout(step.text("title", self.shell), step.text("body", self.shell),
                        step_text, dots, step.centred, step.icon, first, last,
                        show_check=step.kind == "welcome" and not self.hint, target=target,
                        animate=rebind, primary=self._primary_text(step, first))
        b = ov.callout.btn_primary
        if self.hint:
            ov.callout.btn_skip.hide()
        ov.set_pulsing(step.kind == "click")
        if not rebind:
            return
        ov.setFocus()
        if not b.isHidden():                # never Skip: Enter must not end the tour
            b.setFocus()
        self.step_changed.emit(index)
        if step.advance_on is not None:
            try:
                self._off = step.advance_on(self.shell, self)
            except Exception:
                self._off = None
        if step.is_done(self.shell):
            # already done (another way, or before the step opened): move on
            token = self._token
            QTimer.singleShot(0, lambda: token == self._token and self.current_step() is step
                              and self.advance())

    def _primary_text(self, step: TourStep, first: bool) -> Optional[str]:
        """The callout's one button: Start tour / Done / Got it, or none
        (an action step -- doing the action moves on)."""
        if self.hint:
            return "Got it"
        if step.kind == "welcome" or (first and step.centred):
            return "Start tour"
        if step.kind == "finish" or step.centred:
            return "Done"
        if step.kind == "info":
            return "Got it"
        return None

    def _area_rects(self, step: TourStep) -> list:
        out = []
        for name in step.areas or ():
            r = self._union((name,), False)
            if not r.isEmpty():
                out.append(r)
        return out

    def resolve(self, step: TourStep) -> QRectF:
        """Spotlight rectangle (overlay coords) for ``step`` as things stand now."""
        return self._resolve(step, scroll=False)

    def _resolve(self, step: TourStep, scroll: bool) -> QRectF:
        for names in (step.live_targets(self.shell),) + tuple(step.fallbacks):
            r = self._union(names, scroll)
            if not r.isEmpty():
                return r
        return QRectF()

    def _union(self, names: Sequence[str], scroll: bool) -> QRectF:
        out = QRectF()
        for name in names or ():
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
        names = step.live_targets(self.shell)
        if step.targets_fn is not None and names != getattr(self, "_shown_names", None):
            self.refresh()                  # the live target changed: new spot + text
            return
        areas = self._area_rects(step)
        if areas != getattr(self, "_shown_areas", []):
            self._shown_areas = areas
            self.overlay.set_areas(areas)
            self.overlay.reposition_callout(self.overlay.spot())
        r = self.resolve(step)
        if r == self._shown_rect:
            return
        self._shown_rect = QRectF(r)
        target = r.adjusted(-SPOT_PAD, -SPOT_PAD, SPOT_PAD, SPOT_PAD) if not r.isEmpty() \
            else QRectF()
        self.overlay.set_spot(target, animate=False)
        self.overlay.reposition_callout(target)
        self._avoid_toasts(target)

    def _avoid_toasts(self, target: QRectF) -> None:
        """Toasts never cover the spotlit control (they move to the other corner)."""
        tm = getattr(self.shell, "toasts", None)
        host = getattr(tm, "_host", None)
        if tm is None or host is None or not hasattr(tm, "set_avoid"):
            return
        if target is None or target.isEmpty():
            tm.set_avoid(None)
            return
        r = target.toAlignedRect()
        tm.set_avoid(QRect(host.mapFrom(self.shell, r.topLeft()), r.size()))

    def _close(self, completed: bool) -> None:
        self._unbind()
        offs, self._watch_off = self._watch_off, []
        for off in offs:
            try:
                off()
            except Exception:
                pass
        self._avoid_toasts(QRectF())
        self._token += 1
        self._follow.stop()
        ov, self.overlay = self.overlay, None
        self.index = -1
        self._persist()
        if not self.hint:
            try:
                self.shell.state.set_scope(None)      # round 3c: tour scope off
            except Exception:
                pass
        if ov is not None:
            def gone(o=ov):
                try:
                    o.hide()
                    o.deleteLater()
                except RuntimeError:
                    pass
            ov.fade_out(gone)
        self.finished.emit(completed)


def _same(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


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
