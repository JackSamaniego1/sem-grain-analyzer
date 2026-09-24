"""
Analyze page (UI-04, DET-06, DET-07-lite, DET-09).

Layout
  left   : filmstrip — thumbnails with per-image status + inline progress
  centre : toolbar (view switch, zoom) · canvas · session summary StatCards
  right  : run card (Analyze all / current / Cancel + overall ProgressRing),
           grain filters (appears after the first result), detection mode cards,
           calibration (session scale + per-image override), scan area,
           excluded (black) regions, advanced parameters
States     : no session (empty state) · idle · running · results
Nothing blocks: analysis runs on a QThread, filtering/saving on the pool.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QSpinBox,
    QVBoxLayout, QWidget,
)

from core.grain_detector import DetectionParams
from ui.app_state import params_from_dict, params_to_dict
from ui.canvas import GrainCanvas
from ui.design import icons
from ui.design.tokens import SPACE
from ui.format import astm_g, fmt_int, fmt_px_per_um, smart_format
from ui.pages.common import MetricCard, Panel, SelectableCard, scroll
from ui.pages.filmstrip import Filmstrip
from ui.pages.filter_card import FilterCard, Reveal
from ui.widgets import (
    AnimatedButton, Badge, Card, CollapsibleSection, EmptyState, FadeStackedWidget,
    IconButton, KeyValueList, ProgressRing, SegmentedControl, label,
)
from ui.workers import AnalysisJob, AnalysisQueue

MODES = [
    ("auto", "Automatic", "target",
     "Picks threshold or boundary detection from the image — recommended"),
    ("boundary", "Boundary", "grains",
     "Dense grain mosaics separated by thin dark grooves"),
    ("threshold", "Threshold", "histogram",
     "Distinct grains or particles on a contrasting background"),
    ("sam_astm", "AI-assisted", "layers",
     "Segment-Anything model + ASTM E112 refinement — slowest"),
]


def sam_model_available() -> bool:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base = getattr(sys, "_MEIPASS", here)
    name = "sam_vit_b_01ec64.pth"
    return any(os.path.isfile(os.path.join(d, name)) for d in
               (os.path.join(base, "models"), os.path.join(here, "models"), here,
                os.path.join(here, "core")))


class ModeCard(SelectableCard):
    def __init__(self, key: str, title: str, icon: str, desc: str, available: bool = True,
                 parent=None) -> None:
        super().__init__(parent=parent)
        self.key = key
        self.available = available
        self.body_layout().setSpacing(SPACE.xs)
        top = QHBoxLayout()
        top.setSpacing(SPACE.sm)
        ic = label()
        ic.setPixmap(icons.pixmap(icon, 18))
        top.addWidget(ic)
        top.addWidget(label(title, "body_strong"), 1)
        if not available:
            top.addWidget(Badge("Not installed", "warning"))
        self.body_layout().addLayout(top)
        d = label(desc, "caption")
        d.setWordWrap(True)
        self.body_layout().addWidget(d)
        self.body_layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setContentsMargins(SPACE.md + 2, SPACE.sm + 2, SPACE.md, SPACE.sm + 2)
        tip = desc if available else ("The AI model file is missing from this installation. "
                                      "Reinstall or repair the application to enable it.")
        self.setToolTip(tip)
        self.setEnabled(available)


class ParamPanel(QWidget):
    """Detection parameters (mode cards + excluded regions + advanced)."""

    changed = Signal()
    show_excluded_regions = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.md)
        self._mode = "auto"

        self.sec_mode = CollapsibleSection("Detection mode", expanded=True)
        grid = QVBoxLayout()
        grid.setSpacing(SPACE.sm)
        grid.setContentsMargins(0, 0, 0, 0)
        self.mode_cards: Dict[str, ModeCard] = {}
        sam_ok = sam_model_available()
        for i, (key, title, ic, desc) in enumerate(MODES):
            c = ModeCard(key, title, ic, desc, available=(key != "sam_astm" or sam_ok))
            c.clicked.connect(lambda k=key: self.set_mode(k, emit=True))
            self.mode_cards[key] = c
            grid.addWidget(c)
        host = QWidget()
        host.setLayout(grid)
        self.sec_mode.add_widget(host)
        v.addWidget(self.sec_mode)

        # --- excluded regions (DET-09)
        self.sec_invalid = CollapsibleSection("Excluded (black) regions", expanded=False)
        f = QFormLayout()
        f.setVerticalSpacing(SPACE.sm)
        self.inv_thr = QSpinBox()
        self.inv_thr.setRange(0, 60)
        self.inv_thr.setToolTip("Pixels at or below this grey level (0–255) are candidates "
                                "for 'no specimen information' — info bars, voids, drop-outs. "
                                "0 switches the exclusion off.")
        self.inv_w = QSpinBox()
        self.inv_w.setRange(1, 99)
        self.inv_w.setSuffix(" px")
        self.inv_w.setToolTip("Dark structures thinner than this stay valid "
                              "(they are grain-boundary grooves).")
        self.inv_a = QSpinBox()
        self.inv_a.setRange(0, 1000000)
        self.inv_a.setSuffix(" px²")
        self.inv_a.setToolTip("Dark blobs smaller than this stay valid (small pits, triple points).")
        f.addRow("Black level ≤", self.inv_thr)
        f.addRow("Min width", self.inv_w)
        f.addRow("Min area", self.inv_a)
        self.show_invalid = QCheckBox("Show excluded regions on the image")
        self.show_invalid.setToolTip("Tint the areas that are not analysed (amber hatch)")
        self.show_invalid.toggled.connect(self.show_excluded_regions)
        ih = QWidget()
        ih.setLayout(f)
        self.sec_invalid.add_widget(ih)
        self.sec_invalid.add_widget(self.show_invalid)
        cap = label("Coverage and statistics are computed over the analysed (valid) area only.",
                    "caption")
        cap.setWordWrap(True)
        self.sec_invalid.add_widget(cap)
        v.addWidget(self.sec_invalid)

        # --- advanced
        self.sec_adv = CollapsibleSection("Advanced parameters", expanded=False)
        a = QFormLayout()
        a.setVerticalSpacing(SPACE.sm)
        self.blur = self._dspin(0, 10, 1, 0.5, "Gaussian blur (σ) to suppress noise before detection")
        self.edge = self._dspin(0.1, 3, 1, 0.1, "Boundary sensitivity. Higher finds more "
                                                "boundaries (may over-split); lower merges grains.")
        self.thr_off = self._dspin(-0.5, 0.5, 3, 0.02, "Threshold offset. Boundary mode: negative "
                                                       "splits more. Threshold mode: shifts Otsu.")
        self.min_sz = QSpinBox()
        self.min_sz.setRange(1, 50000)
        self.min_sz.setSuffix(" px²")
        self.min_sz.setToolTip("Regions smaller than this are not measured")
        self.max_sz = QSpinBox()
        self.max_sz.setRange(0, 10000000)
        self.max_sz.setSuffix(" px²")
        self.max_sz.setSpecialValueText("No limit")
        self.max_sz.setToolTip("Regions larger than this are not measured (0 = no limit)")
        self.ws_dist = QSpinBox()
        self.ws_dist.setRange(1, 100)
        self.ws_dist.setSuffix(" px")
        self.ws_dist.setToolTip("Minimum distance between grain centres when splitting "
                                "touching grains (watershed)")
        self.clahe = QCheckBox("CLAHE contrast boost")
        self.clahe.setToolTip("Local contrast enhancement so faint grooves become visible")
        self.clahe_clip = self._dspin(0.5, 8, 1, 0.5, "CLAHE strength (2 suits most images)")
        self.dark_grains = QCheckBox("Grains are darker than background")
        self.dark_grains.setToolTip("Threshold mode only")
        self.watershed = QCheckBox("Split touching grains (watershed)")
        self.watershed.setToolTip("Separate grains that touch")
        self.adaptive = QCheckBox("Adaptive threshold")
        self.adaptive.setToolTip("Local instead of global threshold (threshold mode only)")
        a.addRow("Blur (σ)", self.blur)
        a.addRow("Edge sensitivity", self.edge)
        a.addRow("Threshold offset", self.thr_off)
        a.addRow("Min grain area", self.min_sz)
        a.addRow("Max grain area", self.max_sz)
        a.addRow("Watershed distance", self.ws_dist)
        a.addRow("CLAHE strength", self.clahe_clip)
        ah = QWidget()
        ah.setLayout(a)
        self.sec_adv.add_widget(ah)
        for cb in (self.clahe, self.watershed, self.adaptive, self.dark_grains):
            self.sec_adv.add_widget(cb)
        self.reset_btn = AnimatedButton("Reset to defaults", "refresh", "ghost", "sm")
        self.reset_btn.setToolTip("Restore factory detection parameters (mode: Automatic)")
        self.reset_btn.clicked.connect(self.reset)
        self.sec_adv.add_widget(self.reset_btn)
        v.addWidget(self.sec_adv)

        for w in (self.inv_thr, self.inv_w, self.inv_a, self.min_sz, self.max_sz, self.ws_dist):
            w.valueChanged.connect(self._changed)
        for cb in (self.clahe, self.watershed, self.adaptive, self.dark_grains):
            cb.toggled.connect(self._changed)
        self._loading = False
        self.set_params(DetectionParams())

    def _dspin(self, lo, hi, dec, step, tip) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setSingleStep(step)
        s.setToolTip(tip)
        s.valueChanged.connect(self._changed)
        return s

    def _changed(self, *_a) -> None:
        if not self._loading:
            self.changed.emit()

    def set_mode(self, key: str, emit: bool = False) -> None:
        if key not in self.mode_cards or not self.mode_cards[key].available:
            key = "auto"
        self._mode = key
        for k, c in self.mode_cards.items():
            c.set_selected(k == key)
        if emit:
            self.changed.emit()

    def mode(self) -> str:
        return self._mode

    def reset(self) -> None:
        """DET-06: defaults come from DetectionParams (mode 'auto'), not 'sam_astm'."""
        self.set_params(DetectionParams())
        self.changed.emit()

    def set_params(self, p: DetectionParams) -> None:
        self._loading = True
        self.set_mode(p.detection_mode or "auto")
        self.blur.setValue(p.blur_sigma)
        self.edge.setValue(p.edge_sensitivity)
        self.thr_off.setValue(p.threshold_offset)
        self.min_sz.setValue(p.min_grain_size_px)
        self.max_sz.setValue(p.max_grain_size_px)
        self.ws_dist.setValue(p.watershed_min_dist)
        self.clahe.setChecked(p.use_clahe)
        self.clahe_clip.setValue(p.clahe_clip_limit)
        self.dark_grains.setChecked(p.dark_grains)
        self.watershed.setChecked(p.use_watershed)
        self.adaptive.setChecked(p.use_adaptive)
        self.inv_thr.setValue(int(getattr(p, "invalid_intensity_threshold", 12)))
        self.inv_w.setValue(int(getattr(p, "invalid_min_width_px", 9)))
        self.inv_a.setValue(int(getattr(p, "invalid_min_area_px", 400)))
        self._loading = False

    def get_params(self) -> DetectionParams:
        p = DetectionParams(
            blur_sigma=self.blur.value(), threshold_offset=self.thr_off.value(),
            min_grain_size_px=self.min_sz.value(), max_grain_size_px=self.max_sz.value(),
            watershed_min_dist=self.ws_dist.value(), dark_grains=self.dark_grains.isChecked(),
            use_watershed=self.watershed.isChecked(), edge_sensitivity=self.edge.value(),
            use_adaptive=self.adaptive.isChecked(), use_clahe=self.clahe.isChecked(),
            clahe_clip_limit=self.clahe_clip.value(), detection_mode=self._mode)
        for name, w in (("invalid_intensity_threshold", self.inv_thr),
                        ("invalid_min_width_px", self.inv_w), ("invalid_min_area_px", self.inv_a)):
            if hasattr(p, name):
                setattr(p, name, int(w.value()))
        return p


class AnalyzePage(QWidget):
    calibrate_requested = Signal()
    scan_area_requested = Signal()
    new_session_requested = Signal()
    open_projects_requested = Signal()
    review_requested = Signal()
    add_images_requested = Signal()
    busy_changed = Signal(bool)
    progress_changed = Signal(float, str)     # overall %, message (status bar)

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self.queue = AnalysisQueue(self)
        self._batch_total = 0
        self._batch_uids: List = []
        self._rec = "session"            # HIER-01: "lot" when images live in the lot
        self._scale_key = "Session scale"
        self._build()
        self._wire()
        self._relabel()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = FadeStackedWidget()
        outer.addWidget(self.stack)
        self.empty = EmptyState("analyze", "No session open",
                                "Start a new session (project › sample › lot) and add SEM images, "
                                "or open an existing session from Projects.",
                                "New session", "add")
        self.empty.action_triggered.connect(self.new_session_requested)
        b2 = AnimatedButton("Open from Projects", "projects", "ghost")
        b2.clicked.connect(self.open_projects_requested)
        self.empty.layout().insertWidget(self.empty.layout().count() - 1, b2, 0, Qt.AlignHCenter)
        self.stack.addWidget(self.empty)

        content = QWidget()
        h = QHBoxLayout(content)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        film_panel = Panel("right")
        fl = QVBoxLayout(film_panel)
        fl.setContentsMargins(0, 0, 0, 0)
        self.film = Filmstrip()
        self.film.setMinimumWidth(200)
        self.film.setMaximumWidth(240)
        fl.addWidget(self.film)
        h.addWidget(film_panel)

        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        cv.setSpacing(SPACE.sm)
        tb = QHBoxLayout()
        tb.setSpacing(SPACE.sm)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.img_title = label("", "h3")
        self.img_sub = label("", "caption")
        col.addWidget(self.img_title)
        col.addWidget(self.img_sub)
        tb.addLayout(col, 1)
        self.view_seg = SegmentedControl(["Original", "Overlay", "Excluded"])
        self.view_seg.setToolTip("Original image · detected grains · areas not analysed")
        self.view_seg.setFixedWidth(270)
        tb.addWidget(self.view_seg)
        self.btn_zo = IconButton("zoom_out", "Zoom out (−)")
        self.btn_zi = IconButton("zoom_in", "Zoom in (+)")
        self.btn_fit = IconButton("fit", "Fit to window (F)")
        self.btn_11 = IconButton("target", "Actual pixels, 1:1 (1)")
        for b in (self.btn_zo, self.btn_zi, self.btn_fit, self.btn_11):
            tb.addWidget(b)
        cv.addLayout(tb)
        self.canvas = GrainCanvas(placeholder="Select an image in the filmstrip")
        cv.addWidget(self.canvas, 1)
        stats = QHBoxLayout()
        stats.setSpacing(SPACE.md)
        self.st_images = MetricCard("Images analysed", 0, "", 0)
        self.st_grains = MetricCard("Grains (session)", 0, "", 0)
        self.st_diam = MetricCard("Mean diameter", 0, "µm", 2)
        self.st_g = MetricCard("ASTM grain size", 0, "G", 1)
        for c in (self.st_images, self.st_grains, self.st_diam, self.st_g):
            c.setMaximumHeight(96)
            stats.addWidget(c)
        cv.addLayout(stats)
        h.addWidget(centre, 1)

        side = Panel("left")
        side.setMinimumWidth(380)
        side.setMaximumWidth(420)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(SPACE.lg, SPACE.lg, SPACE.lg, SPACE.lg)
        iv.setSpacing(SPACE.md)

        run = Card()
        rl = QHBoxLayout()
        rl.setSpacing(SPACE.lg)
        self.ring = ProgressRing(68)
        self.ring.set_label("—")
        self.ring.set_caption("ready")
        rl.addWidget(self.ring)
        rc = QVBoxLayout()
        rc.setSpacing(2)
        self.run_title = label("Ready to analyse", "h3")
        self.run_sub = label("", "caption")
        self.run_sub.setWordWrap(True)
        rc.addWidget(self.run_title)
        rc.addWidget(self.run_sub)
        rl.addLayout(rc, 1)
        run.body_layout().addLayout(rl)
        self.btn_all = AnimatedButton("Analyze all", "run", "primary", "lg")
        self.btn_all.setToolTip("Analyse every image of the session (F5)")
        self.btn_cur = AnimatedButton("Analyze current", "run", "secondary")
        self.btn_cur.setToolTip("Re-analyse only the selected image (Ctrl+F5)")
        self.btn_cancel = AnimatedButton("Cancel", "stop", "danger")
        self.btn_cancel.setToolTip("Stop after the image being processed (Esc)")
        self.btn_cancel.hide()
        run.body_layout().addWidget(self.btn_all)
        br = QHBoxLayout()
        br.addWidget(self.btn_cur, 1)
        br.addWidget(self.btn_cancel, 1)
        run.body_layout().addLayout(br)
        iv.addWidget(run)

        self.filters = FilterCard()
        self.filters_host = Reveal(self.filters)
        iv.addWidget(self.filters_host)

        self.params = ParamPanel()
        iv.addWidget(self.params)
        # calibration + scan area sections inserted after mode
        self.sec_cal = CollapsibleSection("Calibration", expanded=True)
        self.cal_badge = Badge("Not calibrated", "warning", dot=True)
        self.sec_cal.header_trailing().addWidget(self.cal_badge)
        self.cal_kv = KeyValueList(mono_keys=("Session scale", "This image"))
        self.sec_cal.add_widget(self.cal_kv)
        # INN-05: scale read from the image file's own SEM metadata
        self.meta_row = QWidget()
        mr = QHBoxLayout(self.meta_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.setSpacing(SPACE.sm)
        self.meta_lbl = label("", "caption")
        self.meta_lbl.setWordWrap(True)
        self.btn_meta_cal = AnimatedButton("Use", "calibrate", "ghost", "sm")
        self.btn_meta_cal.setToolTip("Use the pixel size stored in this image file by the "
                                     "microscope as this image's scale")
        mr.addWidget(self.meta_lbl, 1)
        mr.addWidget(self.btn_meta_cal, 0, Qt.AlignTop)
        self.meta_row.hide()
        self.sec_cal.add_widget(self.meta_row)
        self.btn_cal = AnimatedButton("Set scale bar…", "calibrate", "secondary")
        self.btn_cal.setToolTip("Click the two ends of the scale bar and enter its length (Ctrl+K)")
        self.sec_cal.add_widget(self.btn_cal)
        self.cal_override = QCheckBox("Use a different scale for this image")
        self.cal_override.setToolTip("Per-image calibration (e.g. a different magnification)")
        self.cal_spin = QDoubleSpinBox()
        self.cal_spin.setRange(0.0, 100000.0)
        self.cal_spin.setDecimals(4)
        self.cal_spin.setSuffix(" px/µm")
        self.cal_spin.setToolTip("Pixels per micrometre for this image only")
        self.cal_spin.setKeyboardTracking(False)
        self.cal_spin.setEnabled(False)
        self.sec_cal.add_widget(self.cal_override)
        self.sec_cal.add_widget(self.cal_spin)
        self.btn_cal_reset = AnimatedButton("Reset to session scale", "undo", "ghost", "sm")
        self.btn_cal_reset.setToolTip("Drop this image's own scale — it uses the session scale again")
        self.btn_cal_reset.hide()
        self.sec_cal.add_widget(self.btn_cal_reset)
        self.params.layout().insertWidget(1, self.sec_cal)

        self.sec_scan = CollapsibleSection("Scan area", expanded=False)
        self.scan_lbl = label("Full image", "caption")
        self.scan_lbl.setWordWrap(True)
        self.sec_scan.add_widget(self.scan_lbl)
        # DET-05: SEM data bar found in the frame (always left out of the analysis)
        self.ib_row = QWidget()
        ir = QHBoxLayout(self.ib_row)
        ir.setContentsMargins(0, 0, 0, 0)
        ir.setSpacing(SPACE.sm)
        self.ib_chip = Badge("Info bar excluded", "info", dot=True)
        self.ib_chip.setToolTip("The microscope's data bar (text and scale bar) was found in "
                                "this image.\nIt is never analysed — shown hatched on the image.")
        self.btn_ib_scan = AnimatedButton("Use as scan area", "scan_area", "ghost", "sm")
        self.btn_ib_scan.setToolTip("Set the scan area to the micrograph above the info bar "
                                    "(border grains at its edge are then excluded too)")
        ir.addWidget(self.ib_chip)
        ir.addStretch(1)
        ir.addWidget(self.btn_ib_scan)
        self.ib_row.hide()
        self.sec_scan.add_widget(self.ib_row)
        sr = QHBoxLayout()
        self.btn_scan = AnimatedButton("Set scan area…", "scan_area", "secondary")
        self.btn_scan.setToolTip("Draw the rectangle to analyse, e.g. to leave out the info bar (Ctrl+R)")
        self.btn_scan_clear = AnimatedButton("Full image", None, "ghost")
        self.btn_scan_clear.setToolTip("Analyse the whole image")
        sr.addWidget(self.btn_scan)
        sr.addWidget(self.btn_scan_clear)
        sh = QWidget()
        sh.setLayout(sr)
        self.sec_scan.add_widget(sh)
        self.scan_this = QCheckBox("Only for this image")
        self.scan_this.setToolTip("Give only the selected image its own scan area")
        self.sec_scan.add_widget(self.scan_this)
        self.btn_scan_reset = AnimatedButton("Reset to session scan area", "undo", "ghost", "sm")
        self.btn_scan_reset.setToolTip("Drop this image's own scan area — it uses the session's "
                                       "again")
        self.btn_scan_reset.hide()
        self.sec_scan.add_widget(self.btn_scan_reset)
        self.params.layout().insertWidget(2, self.sec_scan)
        iv.addStretch(1)
        sv.addWidget(scroll(inner))
        h.addWidget(side)
        self.stack.addWidget(content)

    def _wire(self) -> None:
        st = self.state
        st.session_opened.connect(self._on_session)
        st.session_closed.connect(self._on_session)
        st.images_changed.connect(self._on_images)
        st.image_updated.connect(self._on_image_updated)
        st.result_edited.connect(self._on_result_edited)
        st.current_image_changed.connect(self._on_current)
        st.calibration_changed.connect(self._refresh_calibration)
        st.filters_changed.connect(self._refresh_filters)
        st.filtering_changed.connect(
            lambda uid, busy: uid == st.current_uid and self.filters.set_busy(busy))
        self.film.current_changed.connect(st.set_current_image)
        self.film.files_dropped.connect(st.add_images)
        self.film.add_requested.connect(self.add_images_requested)
        self.view_seg.current_changed.connect(
            lambda i: self.canvas.set_view(("original", "overlay", "excluded")[i]))
        self.canvas.view_changed.connect(self._sync_view_seg)
        self.canvas.delete_requested.connect(
            lambda ids: st.delete_grains(st.current_uid, ids))
        self.btn_zo.clicked.connect(lambda: self.canvas.zoom_by(0.8))
        self.btn_zi.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self.btn_fit.clicked.connect(self.canvas.fit)
        self.btn_11.clicked.connect(self.canvas.actual_size)
        self.btn_all.clicked.connect(self.analyze_all)
        self.btn_cur.clicked.connect(self.analyze_current)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cal.clicked.connect(self.calibrate_requested)
        self.btn_scan.clicked.connect(self.scan_area_requested)
        self.btn_scan_clear.clicked.connect(self._clear_scan)
        self.cal_override.toggled.connect(self._on_cal_override)
        self.btn_cal_reset.clicked.connect(self._reset_cal)
        self.btn_scan_reset.clicked.connect(self._reset_scan)
        self.cal_spin.valueChanged.connect(self._on_cal_spin)
        self.btn_meta_cal.clicked.connect(self._use_meta_cal)
        self.btn_ib_scan.clicked.connect(self._use_info_bar_scan)
        st.info_bar_ready.connect(lambda uid: uid == st.current_uid and self._refresh_info_bar())
        st.sem_metadata_ready.connect(
            lambda uid: uid == st.current_uid and self._refresh_calibration())
        st.profile_changed.connect(self._relabel)
        self.params.changed.connect(lambda: st.set_params(self.params.get_params()))
        self.params.show_excluded_regions.connect(self.canvas.set_show_excluded_regions)
        self.filters.options_changed.connect(self._on_filter_options)
        self.filters.apply_all_requested.connect(self._apply_filters_all)
        self.filters.show_excluded_toggled.connect(self.canvas.set_show_excluded_grains)
        self.queue.job_started.connect(lambda uid: st.set_image_status(uid, "running", 0, "Starting"))
        self.queue.job_progress.connect(self._on_job_progress)
        self.queue.job_finished.connect(self._on_job_finished)
        self.queue.job_failed.connect(self._on_job_failed)
        self.queue.overall_progress.connect(self._on_overall)
        self.queue.queue_finished.connect(self._on_queue_finished)

    # ------------------------------------------------------------------ state sync
    def _on_session(self) -> None:
        s = self.state.session
        self.stack.set_current_index(1 if s is not None else 0)
        if s is not None:
            self.params.set_params(params_from_dict(s.params))
        self._on_images()
        self._refresh_calibration()
        self._refresh_filters()
        self._refresh_summary()
        self._set_idle()

    def _on_images(self) -> None:
        imgs = self.state.images()
        self.film.set_images(imgs)
        self.film.set_current(self.state.current_uid)
        self._refresh_summary()
        self._on_current(self.state.current_uid)

    def _on_image_updated(self, uid) -> None:
        self.film.update_item(uid)
        if uid == self.state.current_uid:
            im = self.state.current_image()
            if im is not None and (self.canvas.result() is not im.result
                                   or self.canvas.excluded() != im.excluded):
                self._show_result()
            elif im is not None:
                self._update_title(im)
        self._refresh_summary()

    def _on_result_edited(self, uid) -> None:
        im = self.state.current_image()
        if uid == self.state.current_uid and im is not None and (
                self.canvas.result() is not im.result or self.canvas.excluded() != im.excluded):
            self._show_result()

    def _on_current(self, uid) -> None:
        self.film.set_current(uid)
        im = self.state.current_image()
        if im is None:
            self.canvas.set_image(None)
            self.img_title.setText("")
            self.img_sub.setText("")
            self.filters_host.reveal(False)
            return
        self.canvas.set_image(im.image_bgr, im.result, raw=im.raw, excluded=im.excluded)
        self.canvas.set_view("overlay" if im.result is not None else "original")
        self.canvas.set_scan_rect(self.state.scan_for(im))
        self._update_title(im)
        self._refresh_calibration()
        self._refresh_filters()
        self._refresh_info_bar()

    def _show_result(self) -> None:
        im = self.state.current_image()
        if im is None:
            return
        had = self.canvas.result() is not None
        self.canvas.set_result(im.result, raw=im.raw, excluded=im.excluded)
        if im.result is not None and not had:
            self.canvas.set_view("overlay")
        self._update_title(im)
        self._refresh_filters()
        self._refresh_info_bar()

    def _sync_view_seg(self, view: str) -> None:
        idx = {"original": 0, "overlay": 1, "excluded": 2}.get(view)
        if idx is not None and idx != self.view_seg.current_index():
            self.view_seg.blockSignals(True)
            self.view_seg.set_current_index(idx)
            self.view_seg.blockSignals(False)

    # ------------------------------------------------------------------ HIER-01 labels
    def _relabel(self) -> None:
        """Use the workspace's own words (e.g. "lot" instead of "session")."""
        from ui import hierarchy_ui as hui
        p = self.state.profile
        rec = hui.record_word(p)
        Rec = hui.cap_first(rec)
        self._rec = rec
        self._scale_key = f"{Rec} scale"
        if hui.lot_mode(p):
            lot = hui.kind_label(p, "lot")
            self.empty.set_texts(f"No {rec} open",
                                 f"Create a {hui.level_chain(p)} and add SEM images — they are "
                                 f"stored in the {rec} folder — or open a {rec} from Projects.",
                                 f"New {lot}")
        else:
            self.empty.set_texts("No session open",
                                 f"Start a new session ({hui.level_chain(p).lower()}) and add "
                                 "SEM images, or open an existing session from Projects.",
                                 "New session")
        self.st_grains.set_label(f"Grains ({rec})")
        self.cal_kv.set_mono_keys((self._scale_key, "This image"))
        self.btn_cal_reset.setText(f"Reset to {rec} scale")
        self.btn_cal_reset.setToolTip(f"Drop this image's own scale — it uses the {rec} "
                                      "scale again")
        self.btn_scan_reset.setText(f"Reset to {rec} scan area")
        self.btn_scan_reset.setToolTip(f"Drop this image's own scan area — it uses the "
                                       f"{rec}'s again")
        self.btn_all.setToolTip(f"Analyse every image of the {rec} (F5)")
        if self.state.session is not None:
            self._refresh_calibration()
            self._set_idle()

    # ------------------------------------------------------------------ DET-05 / INN-05
    def _refresh_info_bar(self) -> None:
        im = self.state.current_image()
        info = self.state.info_bar_for(im)
        if im is not None and info is None and im.info_bar is None:
            self.state.probe_info_bar(im.uid)
        self.canvas.set_info_bar_rect(info.get("bar_rect") if info else None)
        self.ib_row.setVisible(bool(info))
        if info:
            ar = tuple(info.get("analysis_rect") or ())
            cur = self.state.scan_for(im)
            self.btn_ib_scan.setEnabled(bool(ar) and (cur is None or tuple(cur) != ar))
            if cur is None:
                self.scan_lbl.setText("Full image — the info bar is left out automatically")
            conf = float(info.get("confidence", 0.0) or 0.0)
            self.ib_chip.set_text("Info bar excluded" + (" (check)" if conf < 0.7 else ""))
            self.ib_chip.set_kind("info" if conf >= 0.7 else "warning")

    def _use_info_bar_scan(self) -> None:
        im = self.state.current_image()
        if im is None:
            return
        this_only = self.scan_this.isChecked()
        undo = self.state.use_info_bar_as_scan_area(im.uid, this_only)
        if undo is None:
            return
        prev = undo[0]
        uid = im.uid if this_only else None
        self._refresh_info_bar()
        analysed = any(x.result is not None for x in self.state.images())
        self._toast_action("Scan area set above the info bar",
                           ("This image" if this_only else "All images")
                           + (" — re-analyse to apply it to existing results." if analysed
                              else "."),
                           "success", "Undo",
                           lambda: (self.state.set_scan_rect(prev, uid), self._refresh_info_bar()))

    def _use_meta_cal(self) -> None:
        im = self.state.current_image()
        if im is None or not im.cal_suggestion:
            return
        prev = im.px_override
        px = float(im.cal_suggestion[0])
        self.state.set_calibration(px, im.uid)

        def undo():
            if prev > 0:
                self.state.set_calibration(prev, im.uid)
            else:
                self.state.reset_image_calibration(im.uid)
        self._toast_action("Scale from image metadata", f"{px:.4f} px/µm for {im.filename}",
                           "success", "Undo", undo)

    def _refresh_meta_row(self, im) -> None:
        sug = im.cal_suggestion if im is not None else None
        if not sug:
            self.meta_row.hide()
            return
        px, src, conf = float(sug[0]), str(sug[1]), str(sug[2])
        eff = self.state.px_for(im)
        using = eff > 0 and abs(eff - px) / px < 1e-3
        conf_txt = {"high": "", "medium": " — please check", "low": " — low confidence"}.get(
            conf, "")
        self.meta_lbl.setText(f"Image metadata ({src}): {px:.4g} px/µm{conf_txt}"
                              + ("  ✓ in use" if using else ""))
        self.meta_lbl.setToolTip("Pixel size written into the image file by the microscope "
                                 f"software ({src}, {conf} confidence). Read locally — nothing "
                                 "leaves this PC.")
        self.btn_meta_cal.setVisible(not using)
        self.meta_row.show()

    def _toast_action(self, title, body, sev, action, fn) -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev, action, fn)

    def _update_title(self, im) -> None:
        self.img_title.setText(im.display_name)
        self.img_title.setToolTip(im.tooltip())
        parts = []
        if im.image_bgr is not None:
            h, w = im.image_bgr.shape[:2]
            parts.append(f"{w} × {h} px")
        if im.result is not None:
            parts.append(f"{fmt_int(im.result.grain_count)} grains")
            if im.excluded:
                parts.append(f"{len(im.excluded)} excluded")
        elif im.status == "error":
            parts.append(im.message.splitlines()[0] if im.message else "Error")
        self.img_sub.setText("  ·  ".join(parts))

    def _refresh_summary(self) -> None:
        imgs = self.state.images()
        done = [im for im in imgs if im.result is not None]
        self.st_images.set_metric(len(done), f"of {len(imgs)}", 0)
        self.st_grains.set_metric(sum(im.result.grain_count for im in done), "", 0)
        diams = [im.result.mean_diameter_um for im in done
                 if im.result.has_calibration and im.result.mean_diameter_um]
        self.st_diam.set_metric(sum(diams) / len(diams) if diams else None,
                                "µm" if diams else "uncalibrated", 2)
        gs = [g for g in (astm_g(im.result) for im in done) if g is not None]
        self.st_g.set_metric(sum(gs) / len(gs) if gs else None, "G" if gs else "n/a", 1)

    def _refresh_calibration(self) -> None:
        s = self.state.session
        im = self.state.current_image()
        if s is None:
            return
        rows = [(self._scale_key, fmt_px_per_um(s.px_per_um))]
        if im is not None and im.px_override > 0:
            rows.append(("This image", fmt_px_per_um(im.px_override)))
        self.cal_kv.set_items(rows)
        eff = self.state.px_for(im) if im is not None else s.px_per_um
        self.cal_badge.set_text(f"{eff:.4g} px/µm" if eff > 0 else "Not calibrated")
        self.cal_badge.set_kind("success" if eff > 0 else "warning")
        self.cal_override.blockSignals(True)
        self.cal_override.setChecked(bool(im is not None and im.px_override > 0))
        self.cal_override.blockSignals(False)
        self.cal_spin.blockSignals(True)
        self.cal_spin.setEnabled(self.cal_override.isChecked())
        self.cal_spin.setValue(im.px_override if (im is not None and im.px_override > 0) else s.px_per_um)
        self.cal_spin.blockSignals(False)
        rect = self.state.scan_for(im) if im is not None else s.scan_rect
        if rect:
            x, y, w, h = rect
            scope = "this image" if (im is not None and im.scan_rect) else "all images"
            self.scan_lbl.setText(f"{w} × {h} px at ({x}, {y}) — {scope}")
        else:
            self.scan_lbl.setText("Full image (nothing excluded)")
        self.canvas.set_scan_rect(rect)
        self.btn_cal_reset.setVisible(im is not None and im.px_override > 0)
        self._refresh_meta_row(im)
        self.btn_scan_reset.setVisible(im is not None and im.scan_rect is not None)

    def _refresh_filters(self) -> None:
        im = self.state.current_image()
        if im is None or self.state.session is None:
            self.filters_host.reveal(False)
            return
        has = im.raw is not None
        self.filters_host.reveal(has)
        if has:
            self.filters.set_state(self.state.filter_options(im.uid), im.counts,
                                   self.state.px_for(im), self.state.has_override(im.uid),
                                   im.result.grain_count if im.result is not None else None)

    # ------------------------------------------------------------------ actions
    def _on_filter_options(self, opts, scope: str) -> None:
        uid = self.state.current_uid if scope == "image" else None
        self.state.set_filter_options(opts, uid)

    def _apply_filters_all(self, opts) -> None:
        self.state.apply_filters_to_all(opts)
        self._toast("Filters applied to all images",
                    f"Every image of the {self._rec} now uses the same grain filters.",
                    "success")

    def _on_cal_override(self, on: bool) -> None:
        im = self.state.current_image()
        self.cal_spin.setEnabled(on)
        if im is None:
            return
        if on:
            self.state.set_calibration(self.cal_spin.value() or self.state.session.px_per_um, im.uid)
        else:
            self.state.set_calibration(0.0, im.uid)

    def _on_cal_spin(self, v: float) -> None:
        im = self.state.current_image()
        if im is not None and self.cal_override.isChecked():
            self.state.set_calibration(v, im.uid)

    def _clear_scan(self) -> None:
        im = self.state.current_image()
        if im is not None and self.scan_this.isChecked() and im.image_bgr is not None:
            h, w = im.image_bgr.shape[:2]
            self.state.set_scan_rect((0, 0, w, h), im.uid)   # this image: whole frame
        else:
            self.state.set_scan_rect(None, None)

    def _reset_cal(self) -> None:
        im = self.state.current_image()
        if im is not None:
            self.state.reset_image_calibration(im.uid)

    def _reset_scan(self) -> None:
        im = self.state.current_image()
        if im is not None:
            self.state.reset_image_scan_rect(im.uid)

    def is_busy(self) -> bool:
        return self.queue.is_running()

    def analyze_all(self) -> None:
        self._start([im for im in self.state.images() if im.image_bgr is not None])

    def analyze_current(self) -> None:
        im = self.state.current_image()
        if im is not None and im.image_bgr is not None:
            self._start([im])

    def _start(self, images) -> None:
        if self.state.session is None or not images:
            self._toast("Nothing to analyse", f"Add images to the {self._rec} first.",
                        "warning")
            return
        if self.queue.is_running():
            self._toast("Analysis already running", "Wait for it to finish or press Cancel.", "info")
            return
        params = self.params.get_params()
        self.state.set_params(params)
        jobs = []
        for im in images:
            jobs.append(AnalysisJob(im.uid, im.image_bgr, self.state.px_for(im), params,
                                    self.state.scan_for(im)))
            self.state.set_image_status(im.uid, "queued")
        self._batch_total = len(jobs)
        self._batch_uids = [j.uid for j in jobs]
        self.btn_all.set_loading(True)
        self.btn_cur.setEnabled(False)
        self.btn_cancel.show()
        self.ring.set_tone("accent")
        self.ring.set_label(None)
        self.ring.set_value(0, animate=False)
        self.run_title.setText(f"Analysing {len(jobs)} image{'s' if len(jobs) != 1 else ''}")
        self.busy_changed.emit(True)
        self.queue.start(jobs)

    def cancel(self) -> None:
        if not self.queue.is_running():
            return
        cur = self.queue.current_uid()
        for uid in self.queue.pending_uids() + ([cur] if cur is not None else []):
            im = self.state.session.image(uid) if self.state.session else None
            if im is not None:
                self.state.set_image_status(uid, "done" if im.result is not None else "pending")
        self.queue.cancel()
        self.run_title.setText("Cancelling…")
        self.run_sub.setText("The image being processed will finish in the background.")

    def _on_job_progress(self, uid, pct: int, msg: str) -> None:
        self.state.set_image_status(uid, "running", pct, msg)
        im = self.state.session.image(uid) if self.state.session else None
        name = im.filename if im else ""
        i = self._batch_uids.index(uid) + 1 if uid in self._batch_uids else 0
        self.run_sub.setText(f"{i} of {self._batch_total} · {name}\n{msg}")

    def _on_job_finished(self, uid, raw) -> None:
        self.state.set_result(uid, raw)

    def _on_job_failed(self, uid, msg: str) -> None:
        self.state.set_image_status(uid, "error", 0, msg)
        im = self.state.session.image(uid) if self.state.session else None
        self._toast("Analysis failed", f"{im.filename if im else ''}: {msg.splitlines()[0]}", "danger")

    def _on_overall(self, pct: float) -> None:
        self.ring.set_value(pct)
        done = int(round(pct / 100 * self._batch_total))
        self.ring.set_caption(f"{min(done, self._batch_total)} of {self._batch_total}")
        self.progress_changed.emit(pct, self.run_sub.text().split("\n")[0])

    def _on_queue_finished(self, cancelled: bool) -> None:
        self.btn_all.set_loading(False)
        self.btn_cur.setEnabled(True)
        self.btn_cancel.hide()
        self.busy_changed.emit(False)
        self.progress_changed.emit(-1, "")
        imgs = [self.state.session.image(u) for u in self._batch_uids] if self.state.session else []
        ok = [im for im in imgs if im is not None and im.status in ("done", "running")
              and im.raw is not None]
        grains = sum((im.result or im.raw).grain_count for im in ok)
        if cancelled:
            self.ring.set_tone("warning")
            self.ring.set_label("—")
            self.ring.set_caption("cancelled")
            self.run_title.setText("Analysis cancelled")
            self.run_sub.setText(f"{len(ok)} of {self._batch_total} images finished.")
            self._toast("Analysis cancelled", f"{len(ok)} of {self._batch_total} images finished.",
                        "warning")
            return
        failed = [im for im in imgs if im is not None and im.status == "error"]
        self.ring.set_value(100)
        self.ring.set_tone("danger" if failed else "success")
        self.ring.set_label("✓" if not failed else "!")
        self.ring.set_caption("done")
        self.run_title.setText("Analysis complete")
        self.run_sub.setText(f"{len(ok)} image{'s' if len(ok) != 1 else ''} · "
                             f"{fmt_int(grains)} grains · saved to the {self._rec} "
                             "automatically")
        if self.toasts is not None:
            self.toasts.show_toast("Analysis complete",
                                   f"{len(ok)} image{'s' if len(ok) != 1 else ''} · "
                                   f"{fmt_int(grains)} grains"
                                   + (f" · {len(failed)} failed" if failed else ""),
                                   "warning" if failed else "success", "Review results",
                                   lambda: self.review_requested.emit())

    def _set_idle(self) -> None:
        if self.queue.is_running():
            return
        self.ring.set_tone("accent")
        self.ring.set_value(0, animate=False)
        self.ring.set_label("—")
        self.ring.set_caption("ready")
        n = len(self.state.images())
        done = sum(1 for im in self.state.images() if im.result is not None)
        self.run_title.setText("Ready to analyse" if n else "Add images to begin")
        self.run_sub.setText(f"{n} image{'s' if n != 1 else ''} in this {self._rec}"
                             + (f" · {done} already analysed" if done else ""))

    def _toast(self, title, body="", sev="info") -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev)
