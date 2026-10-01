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
``areas`` are work areas shown clear and clickable too (e.g. the image while
a Review tool is spotlit) -- the callout keeps off them.

Kinds: "welcome" / "finish" (centred; Start tour / Done), "click" (an action
advances it), "info" (a short "look at this" with a single "Got it" button;
there is never a Back).  Actions further on the tour are noted from the
start (``watch_actions`` -> ``TourController.seen``), so doing things out of
order never dead-ends a step.

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
KINDS = ("welcome", "point", "click", "info", "finish")


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
    #: objectNames of work areas left clear and clickable (no ring)
    areas: Tuple[str, ...] = ()
    #: ``shell -> None`` run before the step is shown (e.g. build the report)
    prepare: Optional[Callable[[object], None]] = None
    #: target not there yet (still building): wait for it instead of skipping
    wait: bool = False

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
    return "Analyzing…" if _running(shell) else "Run the analysis"


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
    return (f"Last step: click PowerPoint to export the {L['lot'].lower()}'s report as a "
            "slide deck — grain-size statistics, ASTM E112 G, charts and every image. It is "
            f"saved in the {L['lot'].lower()}'s exports folder on this computer (File › Export "
            "report to PowerPoint does the same).")


# ---------------------------------------------------------------------- steps
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


def _length_needed(shell) -> bool:
    """Step 2 found the bar but could not read its label: the length row
    is showing (fallback; a label read successfully is applied at once)."""
    try:
        row = shell.analyze.scale_row
        return ("scale" in getattr(shell.analyze, "_tried", ()) and row.isVisible()
                and not _step(shell, "scale"))
    except Exception:
        return False


def _scale_title(shell) -> str:
    return "Enter the scale-bar length" if _length_needed(shell) else "Set the scale bar"


def _scale_body(shell) -> str:
    if _length_needed(shell):
        return ("The scale bar was found, but its label could not be read. Type the length "
                "printed next to the bar, pick the unit and press Apply.")
    return ("Click All images. The app finds the scale bar in each image's info bar and reads "
            "its label. Then look at the strip under the image: it shows the length that "
            "was read, e.g. “Scale bar: 10 µm · 160 px → 16 px/µm”, marked “Read from "
            "image”. If a reading is ever wrong, correct it with Edit….")


def _scale_targets(shell) -> Tuple[str, ...]:
    return ("scaleLengthRow",) if _length_needed(shell) else ("scale_all",)


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
                tour.refresh()              # label not read: point at the length row
        QTimer.singleShot(0, later)
    return connect_all([(s.setup_finished, check), (s.calibration_changed, check),
                        (s.setup_changed, check)])


# ---------------------------------------------------------------------- actions seen
#: what the user did since the tour started (``TourController.seen``), noted
#: by :func:`watch_actions` -- steps further on are done if their action
#: already happened out of order
ACTIONS = ("review_page", "select", "merge", "split", "add", "delete", "undo", "opacity",
           "view", "filter", "toggle", "order", "edit", "xlsx", "pptx")


def _classify(cmd) -> Optional[str]:
    name = type(cmd).__name__
    if name == "ExcludeGrainsCommand":
        return "delete"
    if name == "GrainGeometryCommand":
        t = (cmd.text() or "").lower()
        return "merge" if t.startswith("merge") else "split" if t.startswith("split") \
            else "add" if t.startswith("add") else None
    return None


def watch_actions(shell, tour) -> Disconnect:
    """Bound for the whole tour: notes every action a step can ask for."""
    st, rv, rp = shell.state, shell.review, shell.reports
    stack = st.undo_stack
    last = [stack.index()]

    def on_index(i: int) -> None:
        prev, last[0] = last[0], i
        try:
            if i > prev:
                kind = _classify(stack.command(i - 1))
                if kind:
                    tour.note(kind)
            elif i < prev:
                undone = stack.command(i)
                if type(undone).__name__ in ("ExcludeGrainsCommand", "RestoreGrainsCommand",
                                             "GrainGeometryCommand"):
                    tour.note("undo")
        except Exception:
            pass

    def on_page(*_a) -> None:
        if shell.current_page() == "review":
            tour.note("review_page")

    def on_edited(what: str) -> None:
        if what in ("include", "section"):
            tour.note("toggle")
        elif what == "order":
            tour.note("order")
        elif what not in ("profile",):
            tour.note("edit")

    def on_exported(paths) -> None:
        for p in paths or []:
            low = str(p).lower()
            if low.endswith(".xlsx"):
                tour.note("xlsx")
            elif low.endswith(".pptx"):
                tour.note("pptx")
    return connect_all([
        (stack.indexChanged, on_index),
        (shell.stack.currentChanged, on_page),
        (rv.canvas.selection_changed, lambda ids: ids and tour.note("select")),
        (rv.canvas.add_requested, lambda _pts: tour.note("add_try")),
        (rv.canvas.overlay_opacity_edited, lambda _v, _f: tour.note("opacity")),
        (rv.view_seg.current_changed, lambda *_a: tour.note("view")),
        (rv.filters.options_changed, lambda *_a: tour.note("filter")),
        (rv.filters.show_excluded_toggled, lambda *_a: tour.note("filter")),
        (rp.report_edited, on_edited),
        (rp.exported, on_exported),
    ])


def default_watchers():
    return (watch_actions,)


def seen(*keys: str):
    """``advance_on`` / ``done`` pair for an action noted by watch_actions."""
    def hook(shell, tour) -> Disconnect:
        def on(key: str) -> None:
            if key in keys and tour.is_waiting_on(hook):
                tour.advance()
        return connect_all([(tour.noted, on)])

    def done(shell) -> bool:
        s = getattr(getattr(shell, "tour", None), "seen", set())
        return any(k in s for k in keys)
    return hook, done


def ensure_report(shell) -> None:
    """The Reports page needs a built report: build it from the results
    when there is none yet (the steps wait while it builds)."""
    rp = shell.reports
    try:
        from ui.pages import report_builder as rb
        if rp.model is None and not rp.is_busy() and rb.analysed_count(shell.state):
            rp.build_from_session()
    except Exception:
        pass


def _act(key, title, body, targets, *keys, page=None, areas=(), fallbacks=(),
         kind="click", report=False):
    hook, done = seen(*keys)
    return TourStep(key, title, body, kind=kind, page=page, targets=tuple(targets),
                    areas=tuple(areas), fallbacks=tuple(fallbacks), advance_on=hook,
                    done=done if kind == "click" else None,
                    prepare=ensure_report if report else None, wait=report)


def _info(key, title, body, targets, page=None, areas=(), fallbacks=()):
    """A "look at this" step: one "Got it" button (no Back)."""
    return TourStep(key, title, body, kind="info", page=page, targets=tuple(targets),
                    areas=tuple(areas), fallbacks=tuple(fallbacks),
                    prepare=ensure_report if page == "reports" else None,
                    wait=page == "reports")


CANVAS = ("tourReviewCanvas",)


def _review_steps() -> Tuple[TourStep, ...]:
    rv = "review"
    return (
        _act("go_review", "Open the Review page",
             "The analysis is done. Click Review in the navigation rail (Ctrl+3) to check the "
             "grains and correct any the detector got wrong.",
             (rail_anchor("review"),), "review_page"),
        _act("select", "Select a grain",
             "Click any grain on the image (Select tool, V). It is highlighted and its row is "
             "selected in the Grains table on the right; hover a grain to see its area and "
             "diameter. Ctrl+click adds more grains.",
             ("tourToolSelect",), "select", page=rv, areas=CANVAS),
        _act("merge", "Merge two grains",
             "Select two touching grains (click one, Ctrl+click its neighbor), then press M or "
             "click Merge. Use it when one grain was split in two. Any merge counts.",
             ("tourToolMerge",), "merge", page=rv, areas=CANVAS),
        _act("split", "Cut a grain in two",
             "Press C (or click the Cut tool), then draw a line right across a grain. Use it "
             "when two grains were found as one. Any cut counts.",
             ("tourToolSplit",), "split", page=rv, areas=CANVAS),
        _act("add", "Add a missed grain",
             "Press A (or click Add grain), then draw around a grain the detector missed — the "
             "outline closes itself like a lasso. Where every grain is already found, the app "
             "says so and nothing changes; trying it is enough here.",
             ("tourToolAdd",), "add", "add_try", page=rv, areas=CANVAS),
        _act("delete", "Remove a grain",
             "Select a grain and press Delete (or click Remove) to leave it out of the "
             "results — for example a pore or a scratch. Any grain will do.",
             ("tourToolDelete",), "delete", page=rv, areas=CANVAS),
        _act("undo", "Undo an edit",
             "Press Ctrl+Z (or click Undo) to take the last edit back. Every merge, cut, add "
             "and removal can be undone; Ctrl+Y redoes it.",
             ("tourToolUndo",), "undo", page=rv, areas=CANVAS),
        _act("opacity", "Overlay opacity",
             "Drag the opacity control on the image to fade the grain outlines in and out — "
             "handy to judge a boundary against the micrograph underneath.",
             ("tourOpacityPill",), "opacity", page=rv, areas=CANVAS,
             fallbacks=(("tourReviewView",),)),
        _act("view", "Original and overlay",
             "Switch the view: Original shows the micrograph alone, Overlay the detected "
             "grains, Mask the grain map and Excluded what is left out. Try Original, then go "
             "back to Overlay.",
             ("tourReviewView",), "view", page=rv, areas=CANVAS),
        _act("filter", "Grain filters",
             "Filters leave out grains you do not want counted — cut by the edge of the scan "
             "area, too small, or too dark. Change one (for example untick a filter); the "
             "counts and statistics update at once and the left-out grains show grayed.",
             ("tourReviewFilters",), "filter", page=rv),
    )


def _report_steps() -> Tuple[TourStep, ...]:
    rp = "reports"
    return (
        _act("outline", "The report outline",
             "Every sheet and slide of the report is listed here. Untick a section to leave it "
             "out of the export (tick it again to bring it back). Sections tagged Excel only "
             "are in the workbook but not in the PowerPoint.",
             ("tourReportOutline",), "toggle", page=rp, report=True),
        _act("reorder", "Reorder the report",
             "Change the order of the image pages: select an image under Images in the "
             "outline and move it with these arrows (Alt+Up / Alt+Down), or drag it. Text "
             "sections you add (Add text section) move the same way.",
             ("tourReportUp", "tourReportDown"), "order", page=rp,
             areas=("tourReportOutline",), report=True),
        _act("inspector", "Titles and notes",
             "The inspector edits what is selected: the report title, organization and "
             "operator, or a section's title and notes. Change the report title (or any "
             "field) — the preview follows.",
             ("tourReportInspector",), "edit", page=rp, report=True),
        _info("preview", "Live preview",
              "The middle shows the selected sheet or slide as it will be exported. Click an "
              "image in the outline to see its page with the grain overlay and statistics.",
              ("tourReportPreview",), page=rp),
        _info("compare", "Comparing lots",
              "To compare lots, select several lots in Projects (Compare lots) or load them "
              "together. The report then adds lot summary and lot comparison sections (against "
              "the baseline lot) to the outline. The PowerPoint gets lot charts for each part; "
              "the lot comparison tables are in the Excel workbook only.",
              ("tourReportOutline",), page=rp),
        _act("excel", "Export to Excel",
             "Click Excel to write the workbook: an Overview sheet, summary charts, one sheet "
             "per image and the raw data. It is saved in the lot's exports folder.",
             ("tourExportXlsx",), "xlsx", page=rp, report=True),
    )


def default_steps() -> Tuple[TourStep, ...]:
    """The action-driven walk-through of one analysis of the Tutorial job."""
    st = lambda sh: sh.state  # noqa: E731
    pptx_hook, _pptx_done = seen("pptx")
    return (
        TourStep("welcome", "Welcome to the Grain Analyzer",
                 "Measure grain size on SEM images, from a single micrograph to a full "
                 "ASTM E112 report. This tour runs one real analysis on three sample "
                 "images, then shows the review and report tools; each step moves on by "
                 "itself when you do what it shows.",
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
                 areas=("tourSetupTile",),
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
    ) + _review_steps() + _report_steps() + (
        TourStep("export", "Export to PowerPoint", _export_body, kind="click", page="reports",
                 targets=("tourExportPptx",), advance_on=pptx_hook, done=_pptx_done,
                 prepare=ensure_report,
                 wait=True),
        TourStep("finish", "You're ready",
                 "That's one complete analysis, reviewed and reported. The Tutorial job stays "
                 "in Projects — delete it like any other when you no longer need it. Replay "
                 "this tour any time from Help › Show tour, and press ? for keyboard shortcuts.",
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
    tag("tourReviewView", lambda: r.view_seg)
    tag("tourToolSelect", lambda: r.btn_tool_select)
    tag("tourToolMerge", lambda: r.btn_merge)
    tag("tourToolSplit", lambda: r.btn_tool_split)
    tag("tourToolAdd", lambda: r.btn_tool_add)
    tag("tourToolDelete", lambda: r.btn_del)
    tag("tourToolUndo", lambda: r.btn_undo)
    tag("tourOpacityPill", lambda: r.canvas.opacity_pill)
    tag("tourReviewFilters", lambda: r.filters_host)
    for k, attr in (("count", "c_count"), ("area", "c_area"), ("diam", "c_diam"),
                    ("cov", "c_cov"), ("inv", "c_inv"), ("g", "c_g")):
        tag(f"tourStat_{k}", lambda at=attr: getattr(r, at))
    rp = shell.reports
    tag("tourExportXlsx", lambda: rp.btn_xlsx)
    tag("tourExportPptx", lambda: rp.btn_pptx)
    tag("tourExportBoth", lambda: rp.btn_both)
    tag("tourReportOutline", lambda: rp.outline)
    tag("tourReportUp", lambda: rp.btn_up)
    tag("tourReportDown", lambda: rp.btn_down)
    tag("tourReportInspector", lambda: rp.inspector)
    tag("tourReportPreview", lambda: rp.preview_stack)


__all__ = ["TourStep", "KINDS", "default_steps", "default_watchers", "tag_anchors",
           "rail_anchor", "when", "connect_all", "tutorial_open", "seen", "watch_actions",
           "ACTIONS"]
