"""
Guided-tour step definitions (UI-10, rebuilt in batch 4 / D-39) -- data only.

The tour walks one real analysis of the bundled Tutorial job.  Each action
step spotlights exactly one control and **advances by itself** when that
action happens (``TourStep.advance_on``) -- there are no Next / Back
buttons.  Advancement listens to AppState / page signals, never timers, so
doing the action another way (keyboard, menu) advances too.

``advance_on(shell, tour) -> disconnect`` connects whatever signals the step
needs and calls ``tour.advance()`` when the step's condition holds (or
``tour.refresh()`` to re-place the spotlight / text).  ``done(shell)`` is the
same condition as a predicate: a step that is already satisfied when it
opens advances at once.

A step's ``targets`` are objectNames (spotlight = their union);
``fallbacks`` are tried when they are hidden; ``targets_fn(shell)`` overrides
``targets`` while the step is open (e.g. the progress card during a run).

``tag_anchors(shell)`` gives the shell's widgets their stable tour object
names; page code does not need to know about the tour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

Text = Union[str, Callable[[object], str]]
Disconnect = Callable[[], None]

# kinds: "welcome"/"finish" = centred card, "point" = explain, "click" = explain
# + pulse ring inviting the action on the highlighted control
KINDS = ("welcome", "point", "click", "finish")


@dataclass(frozen=True)
class TourStep:
    """One tour step."""
    key: str
    title: Text
    body: Text
    kind: str = "point"
    page: Optional[str] = None                  # shell page to show first
    targets: Tuple[str, ...] = ()               # objectNames, spotlight = union
    fallbacks: Tuple[Tuple[str, ...], ...] = field(default_factory=tuple)
    icon: Optional[str] = None
    #: ``(shell, tour) -> disconnect``: connects the signals that complete the step
    advance_on: Optional[Callable[[object, object], Disconnect]] = None
    #: ``shell -> bool``: the step's action is already done (advance at once)
    done: Optional[Callable[[object], bool]] = None
    #: ``shell -> objectNames``: live targets (overrides ``targets``)
    targets_fn: Optional[Callable[[object], Tuple[str, ...]]] = None

    @property
    def centred(self) -> bool:
        return self.kind in ("welcome", "finish") or not (self.targets or self.targets_fn)

    def live_targets(self, ctx) -> Tuple[str, ...]:
        if self.targets_fn is not None:
            try:
                t = tuple(self.targets_fn(ctx) or ())
                if t:
                    return t
            except Exception:
                pass
        return self.targets

    def is_done(self, ctx) -> bool:
        if self.done is None:
            return False
        try:
            return bool(self.done(ctx))
        except Exception:
            return False

    def text(self, which: str, ctx) -> str:
        v = getattr(self, which)
        if callable(v):
            try:
                return v(ctx)
            except Exception:
                return ""
        return v


def rail_anchor(page: str) -> str:
    return f"tourRail_{page}"


# ---------------------------------------------------------------------- signal helpers
def connect_all(pairs: List[Tuple[object, Callable]]) -> Disconnect:
    """Connect ``(signal, slot)`` pairs; returns one disconnect for all."""
    done = []
    for sig, slot in pairs:
        try:
            sig.connect(slot)
            done.append((sig, slot))
        except (RuntimeError, TypeError, AttributeError):
            pass

    def off() -> None:
        for sig, slot in done:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        done.clear()
    return off


def when(signals: Callable[[object], list], predicate: Callable[[object], bool]):
    """``advance_on`` factory: on any of ``signals(shell)`` advance once
    ``predicate(shell)`` holds (checked after the event loop settles, so the
    page has applied the change first)."""
    def hook(shell, tour) -> Disconnect:
        from PySide6.QtCore import QTimer

        def check(*_a) -> None:
            QTimer.singleShot(0, lambda: tour.is_waiting_on(hook) and _safe(predicate, shell)
                              and tour.advance())
        hook_off = connect_all([(s, check) for s in signals(shell)])
        return hook_off
    return hook


def _safe(fn, *a) -> bool:
    try:
        return bool(fn(*a))
    except Exception:
        return False


# ---------------------------------------------------------------------- predicates
def _labels(shell) -> dict:
    """The user's own level names (HIER-01 profile), e.g. Job / Part / Lot."""
    try:
        from ui import hierarchy_ui as hui
        prof = shell.state.profile
        return {k: hui.kind_label(prof, k) for k in ("project", "sample", "lot")}
    except Exception:
        return {"project": "Project", "sample": "Sample", "lot": "Lot"}


def _lot_mode(shell) -> bool:
    try:
        from ui import hierarchy_ui as hui
        return hui.lot_mode(shell.state.profile)
    except Exception:
        return True


def tutorial_record(shell) -> Optional[Path]:
    rec = getattr(getattr(shell, "tour", None), "tutorial_record", None)
    return Path(rec) if rec else None


def tutorial_open(shell) -> bool:
    s = shell.state.session
    rec = tutorial_record(shell)
    if s is None:
        return False
    if rec is None:
        # still being prepared: wait; not available (assets missing): any session
        return bool(getattr(getattr(shell, "tour", None), "tutorial_failed", False))
    try:
        return Path(s.path).resolve() == rec.resolve()
    except OSError:
        return False


def _step(shell, key: str) -> bool:
    return tutorial_open(shell) and bool(shell.analyze.step_done().get(key))


def _running(shell) -> bool:
    try:
        return shell.analyze.queue.is_running()
    except Exception:
        return False


def _run_done(shell) -> bool:
    return _step(shell, "run") and not _running(shell)


def _grain_selected(shell) -> bool:
    try:
        return bool(shell.review.canvas.selected())
    except Exception:
        return False


# ---------------------------------------------------------------------- texts
def _open_title(shell) -> str:
    L = _labels(shell)
    return f"Open the Tutorial {L['project'].lower()}"


def _open_body(shell) -> str:
    L = _labels(shell)
    where = f"{L['project']} “Tutorial” › {L['sample']} “Sample part” › {L['lot']} “Lot 1”"
    if _lot_mode(shell):
        return (f"We made a sample {L['project'].lower()} for you with three SEM images: "
                f"{where}. It is selected here — click Open {L['lot']} to load its images "
                "into Analyze.")
    return (f"We made a sample {L['project'].lower()} for you with three SEM images: "
            f"{where}. Open its “Tutorial” session to load the images into Analyze.")


def _run_title(shell) -> str:
    return "Analysing…" if _running(shell) else "Run the analysis"


def _run_body(shell) -> str:
    if _running(shell):
        return ("The ring shows how far the analysis is, as a percentage of all three "
                "images; the line beside it names the image being measured. You can keep "
                "working while it runs. The tour moves on when every image is done.")
    return ("Click Analyze all to measure every image in the lot. The progress card below "
            "shows the percentage complete while it runs.")


def _run_targets(shell) -> Tuple[str, ...]:
    return ("wizard_progress",) if _running(shell) else ("run_all",)


def _mode_body(shell) -> str:
    text = ("Pick how grains are found. Boundary traces the dark grain boundaries and is "
            "the quickest for these sample images. AI-Assisted is the most accurate on "
            "difficult micrographs and the slowest. Choosing a mode completes this step.")
    try:
        if not shell.analyze.params.mode_cards["sam_astm"].available:
            text += (" The AI model is not installed on this computer; reinstall the Grain "
                     "Analyzer from the full installer to add it.")
    except Exception:
        pass
    return text


def _export_body(shell) -> str:
    L = _labels(shell)
    return (f"Click PowerPoint to export the {L['lot'].lower()}'s report: grain-size "
            "statistics, ASTM E112 G, charts and every image. It is saved in the "
            f"{L['lot'].lower()}'s exports folder on this computer (File › Export report to "
            "PowerPoint does the same); the tour finishes when the file is written.")


# ---------------------------------------------------------------------- steps
def _pptx_written(shell, tour) -> Disconnect:
    def on(paths) -> None:
        if any(str(p).lower().endswith(".pptx") for p in (paths or [])) \
                and tour.is_waiting_on(_pptx_written):
            tour.advance()
    off = connect_all([(shell.reports.exported, on)])
    # the export buttons need a built report: build it from the results now
    rp = shell.reports
    try:
        from ui.pages import report_builder as rb
        if rp.model is None and not rp.is_busy() and rb.analysed_count(shell.state):
            rp.build_from_session()
    except Exception:
        pass
    return off


def _run_hook(shell, tour) -> Disconnect:
    from PySide6.QtCore import QTimer
    a = shell.analyze

    def busy(_on=None) -> None:
        if tour.is_waiting_on(_run_hook):
            tour.refresh()

    def finished(*_a) -> None:
        QTimer.singleShot(0, lambda: tour.is_waiting_on(_run_hook) and _run_done(shell)
                          and tour.advance())
    return connect_all([(a.busy_changed, busy), (a.queue.queue_finished, finished),
                        (shell.state.image_updated, finished)])


def _confirm_length(shell) -> bool:
    """Step 2 found the bar but the label it read needs the operator's OK
    (no metadata to cross-check it): the length row is showing."""
    try:
        row = shell.analyze.scale_row
        return row.isVisible() and not _step(shell, "scale")
    except Exception:
        return False


def _scale_title(shell) -> str:
    return "Check the scale-bar length" if _confirm_length(shell) else "Set the scale bar"


def _scale_body(shell) -> str:
    if _confirm_length(shell):
        return ("The scale bar was found and its label read as 10 µm. A reading that the "
                "image file cannot confirm always waits for you: compare it with the label "
                "on the image, then press Apply. It is used for every image with the same "
                "scale bar.")
    return ("Click All images. The app finds the scale bar in the info bar and reads its "
            "label, so every measurement is in micrometres.")


def _scale_targets(shell) -> Tuple[str, ...]:
    return ("scaleLengthRow",) if _confirm_length(shell) else ("scale_all",)


def _scale_hook(shell, tour) -> Disconnect:
    from PySide6.QtCore import QTimer
    s = shell.state

    def check(*_a) -> None:
        def later() -> None:
            if not tour.is_waiting_on(_scale_hook):
                return
            if _step(shell, "scale"):
                tour.advance()
            else:
                tour.refresh()              # the length row appeared: point at it
        QTimer.singleShot(0, later)
    return connect_all([(s.setup_finished, check), (s.calibration_changed, check),
                        (s.setup_changed, check)])


def default_steps() -> Tuple[TourStep, ...]:
    """The action-driven walk-through of one analysis of the Tutorial job."""
    st = lambda sh: sh.state  # noqa: E731
    return (
        TourStep("welcome", "Welcome to the Grain Analyzer",
                 "Measure grain size on SEM images, from a single micrograph to a full "
                 "ASTM E112 report. This tour runs one real analysis on three sample "
                 "images; each step moves on by itself when you do what it shows.",
                 kind="welcome", icon="grains"),
        TourStep("open_job", _open_title, _open_body, kind="click", page="projects",
                 targets=("tourNewSession",),
                 fallbacks=(("tourProjectsTree",), (rail_anchor("projects"),)),
                 advance_on=when(lambda sh: [st(sh).session_opened], tutorial_open),
                 done=tutorial_open),
        TourStep("scan", "Set the scan area",
                 "Click All images. The app finds the microscope's info bar at the bottom of "
                 "each image and leaves it out of the area that is measured.",
                 kind="click", page="analyze", targets=("scan_all",),
                 fallbacks=(("wizard_step_scan",),),
                 advance_on=when(lambda sh: [st(sh).setup_finished, st(sh).setup_changed],
                                 lambda sh: _step(sh, "scan")),
                 done=lambda sh: _step(sh, "scan")),
        TourStep("scale", _scale_title, _scale_body,
                 kind="click", page="analyze", targets=("scale_all",),
                 targets_fn=_scale_targets, fallbacks=(("wizard_step_scale",),),
                 advance_on=_scale_hook, done=lambda sh: _step(sh, "scale")),
        TourStep("mode", "Choose a detection mode", _mode_body,
                 kind="click", page="analyze", targets=("wizard_step_mode",),
                 advance_on=when(lambda sh: [sh.analyze.params.changed,
                                             sh.analyze.params.mode_changed],
                                 lambda sh: _step(sh, "mode")),
                 done=lambda sh: _step(sh, "mode")),
        TourStep("run", _run_title, _run_body, kind="click", page="analyze",
                 targets=("run_all",), targets_fn=_run_targets,
                 fallbacks=(("wizard_step_run",),),
                 advance_on=_run_hook, done=_run_done),
        TourStep("review", "Review the grains",
                 "Detected grains are outlined on the image. Click any grain to select it — "
                 "its measurements appear, and Delete removes a bad one (Ctrl+Z undoes).",
                 kind="click", page="review", targets=("tourReviewCanvas",),
                 advance_on=when(lambda sh: [sh.review.canvas.selection_changed],
                                 _grain_selected),
                 done=_grain_selected),
        TourStep("export", "Export the report", _export_body, kind="click", page="reports",
                 targets=("tourExportPptx",), advance_on=_pptx_written),
        TourStep("finish", "You're ready",
                 "That's one complete analysis. The Tutorial job stays in Projects — delete it "
                 "like any other when you no longer need it. Replay this tour any time from "
                 "Help › Show tour, and press ? for keyboard shortcuts.",
                 kind="finish", icon="success"),
    )


def tag_anchors(shell) -> None:
    """Give the shell's tour targets stable objectNames (missing ones are ignored)."""
    def tag(name: str, getter: Callable[[], object]) -> None:
        try:
            w = getter()
        except Exception:
            return
        if w is not None:
            w.setObjectName(name)

    for key in ("projects", "analyze", "review", "reports", "settings"):
        tag(rail_anchor(key), lambda k=key: shell.rail.item(k))
    tag("tourProjectsTree", lambda: shell.projects.tree)
    tag("tourNewSession", lambda: shell.projects.btn_primary)
    a = shell.analyze
    tag("tourAnalyzeEmpty", lambda: a.empty.action_button)
    tag("tourAddImages", lambda: a.film.add_btn)
    # batch 4: the Analyze wizard's own objectNames (wizard_step_*, run_all, ...)
    # are targeted directly and never renamed here
    tag("tourSetupTile", lambda: a.setup_tile)
    tag("tourSamMode", lambda: a.params.mode_cards["sam_astm"])
    tag("tourRunRing", lambda: a.ring)
    r = shell.review
    tag("tourReviewCanvas", lambda: r.canvas)
    for k, attr in (("count", "c_count"), ("area", "c_area"), ("diam", "c_diam"),
                    ("cov", "c_cov"), ("inv", "c_inv"), ("g", "c_g")):
        tag(f"tourStat_{k}", lambda at=attr: getattr(r, at))
    rp = shell.reports
    tag("tourExportXlsx", lambda: rp.btn_xlsx)
    tag("tourExportPptx", lambda: rp.btn_pptx)
    tag("tourExportBoth", lambda: rp.btn_both)


__all__ = ["TourStep", "KINDS", "default_steps", "tag_anchors", "rail_anchor", "when",
           "connect_all", "tutorial_open"]
