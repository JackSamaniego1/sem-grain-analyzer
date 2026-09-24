"""
Guided-tour step definitions (UI-10) — data only, no widgets.

Each step points at one or more widgets found by ``objectName``.  A step with
several ``targets`` spotlights their united rectangle (e.g. the six result
cards).  ``fallbacks`` are tried in order when the targets are hidden (for
example the Analyze controls before any session is open) — usually the page's
navigation-rail item.  A step whose targets and fallbacks are all missing is
skipped by the controller.

``tag_anchors(shell)`` gives the shell's widgets their stable tour object
names; page code does not need to know about the tour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple, Union

Text = Union[str, Callable[[object], str]]

# kinds: "welcome"/"finish" = centred card, "point" = explain, "click" = explain
# + pulse ring inviting a click on the highlighted control
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

    @property
    def centred(self) -> bool:
        return self.kind in ("welcome", "finish") or not self.targets

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


def _labels(shell) -> dict:
    """The user's own level names (HIER-01 profile), e.g. Job / Part / Lot."""
    try:
        from ui import hierarchy_ui as hui
        prof = shell.state.profile
        return {k: hui.kind_label(prof, k) for k in ("project", "sample", "lot")}
    except Exception:
        return {"project": "Project", "sample": "Sample", "lot": "Lot"}


def _record(shell) -> str:
    """"session", or the lot label when images live in the lot (HIER-01)."""
    try:
        from ui import hierarchy_ui as hui
        return hui.record_word(shell.state.profile)
    except Exception:
        return "session"


def _new_session_title(shell) -> str:
    return f"Start a new {_record(shell)}"


def _new_session_body(shell) -> str:
    L = _labels(shell)
    try:
        menu = shell.act_new.text().replace("&", "").rstrip("…. ")
    except Exception:
        menu = "New session"
    return (f"Press Ctrl+N (File › {menu}) to open a short wizard. It asks which "
            f"{L['project']} › {L['sample']} › {L['lot']} the images belong to and who the "
            "operator is. The highlighted button does the next step for the item selected "
            "in the tree.")


def _run_body(shell) -> str:
    return (f"Analyze all processes every image in the {_record(shell)}. The ring shows "
            "progress image by image, and you can keep working while it runs. A notice "
            "tells you when it is done.")


def _reports_body(shell) -> str:
    r = _record(shell)
    return (f"Export the {r}'s report to Excel or PowerPoint. Files are saved in the {r}'s "
            "exports folder; the Reports page lets you choose what the report contains.")


def _projects_body(shell) -> str:
    L = _labels(shell)
    return (f"Your work is filed as {L['project']} › {L['sample']} › {L['lot']}. Every "
            "analysis, image and report is kept here, on this computer.")


def _sam_body(shell) -> str:
    text = ("AI-assisted uses the Segment-Anything model with ASTM E112 refinement. It is "
            "the most accurate on difficult micrographs, and the slowest. Minimum and "
            "maximum grain size are under Advanced parameters.")
    try:
        if not shell.analyze.params.mode_cards["sam_astm"].available:
            text += (" The AI model is not installed on this computer; reinstall the Grain "
                     "Analyzer from the full installer to add it.")
    except Exception:
        pass
    return text


def default_steps() -> Tuple[TourStep, ...]:
    """The basic AI-assisted (SAM) analysis walk-through."""
    return (
        TourStep("welcome", "Welcome to the Grain Analyzer",
                 "Measure grain size on SEM images, from a single micrograph to a full "
                 "ASTM E112 report. This short tour walks through one basic analysis and "
                 "takes about a minute.",
                 kind="welcome", icon="grains"),
        TourStep("projects", "Projects", _projects_body, page="projects",
                 targets=("tourProjectsTree",), fallbacks=((rail_anchor("projects"),),)),
        TourStep("new_session", _new_session_title, _new_session_body, kind="click",
                 page="projects", targets=("tourNewSession",),
                 fallbacks=((rail_anchor("projects"),),)),
        TourStep("add_images", "Add SEM images",
                 "Pick images in the wizard, or add more at any time with the + button "
                 "above the image strip (Ctrl+O). TIFF, PNG and JPEG are supported.",
                 kind="click", page="analyze", targets=("tourAddImages",),
                 fallbacks=(("tourAnalyzeEmpty",), (rail_anchor("analyze"),))),
        TourStep("scale", "Set the scale",
                 "The scale is read from the image's own SEM data when it is there. "
                 "Otherwise use Set scale bar and click the bar's two ends. The SEM info "
                 "bar at the bottom is left out of the analysis automatically.",
                 page="analyze", targets=("tourCalibration",),
                 fallbacks=((rail_anchor("analyze"),),)),
        TourStep("sam", "Choose AI-assisted detection", _sam_body,
                 kind="click", page="analyze", targets=("tourSamMode",),
                 fallbacks=(("tourDetectionMode",), (rail_anchor("analyze"),))),
        TourStep("run", "Run the analysis", _run_body,
                 kind="click", page="analyze", targets=("tourRunRing", "tourAnalyzeAll"),
                 fallbacks=((rail_anchor("analyze"),),)),
        TourStep("review", "Review the grains",
                 "Detected grains are outlined on the image. Use the filters to hide "
                 "edge or low-contrast grains, click a bad grain and press Delete to "
                 "remove it. Ctrl+Z undoes any edit.",
                 page="review", targets=("tourReviewCanvas",),
                 fallbacks=((rail_anchor("review"),),)),
        TourStep("results", "Size statistics",
                 "Grain count, mean area and diameter, and the ASTM E112 grain size number "
                 "G for the selected image. G needs a calibrated scale.",
                 page="review", targets=tuple(f"tourStat_{k}" for k in
                                              ("count", "area", "diam", "cov", "inv", "g")),
                 fallbacks=((rail_anchor("review"),),)),
        TourStep("reports", "Export the report", _reports_body,
                 kind="click", page="reports", targets=("tourExportXlsx", "tourExportPptx",
                                                        "tourExportBoth"),
                 fallbacks=((rail_anchor("reports"),),)),
        TourStep("finish", "You're ready",
                 "That's one complete analysis. You can replay this tour any time from "
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
    tag("tourCalibration", lambda: a.sec_cal)
    tag("tourDetectionMode", lambda: a.params.sec_mode)
    tag("tourSamMode", lambda: a.params.mode_cards["sam_astm"])
    tag("tourRunRing", lambda: a.ring)
    tag("tourAnalyzeAll", lambda: a.btn_all)
    r = shell.review
    tag("tourReviewCanvas", lambda: r.canvas)
    for k, attr in (("count", "c_count"), ("area", "c_area"), ("diam", "c_diam"),
                    ("cov", "c_cov"), ("inv", "c_inv"), ("g", "c_g")):
        tag(f"tourStat_{k}", lambda at=attr: getattr(r, at))
    rp = shell.reports
    tag("tourExportXlsx", lambda: rp.btn_xlsx)
    tag("tourExportPptx", lambda: rp.btn_pptx)
    tag("tourExportBoth", lambda: rp.btn_both)


__all__ = ["TourStep", "KINDS", "default_steps", "tag_anchors", "rail_anchor"]
