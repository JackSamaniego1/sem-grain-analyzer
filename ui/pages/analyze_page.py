"""
Analyze page (UI-04, DET-06, DET-07-lite, DET-09; v3.0.1 UX-01..06, 08, 09).

Layout
  left   : image tree -- Job > Part > Lot (> Session) > images, with counts,
           analysis and scale status per group; right-click removes an image
           from the analyzer (never from the lot), "Add back" restores it
  centre : title · [Image | Results table] · view switch · zoom
           Image  : canvas + "Scan area & scale" tile (auto-find for all
                    images, current image's scan area / scale with source and
                    Edit, scale-bar length entry)
           Table  : one row per image with Job / Part / Lot, group + sort
           summary StatCards underneath
  right  : run card (Analyze all / current / Cancel + ProgressRing), then
           1 Detection mode (AI-assisted first, default) · 2 Calibration ·
           3 Scan area · 4 Grain filters (after the first result) ·
           5 Excluded (black) regions · 6 Advanced parameters (hidden while
           AI-assisted is selected) · Overlay opacity
States     : no session (empty state) · loading (folders stream in) · idle ·
             needs setup (gate) · running · results
Nothing blocks: analysis runs on a QThread; loading, auto-find, filtering
and saving run on the thread pool.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from PySide6.QtCore import SIGNAL, QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QSizePolicy, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from core.grain_detector import DetectionParams
from ui.app_state import params_from_dict
from ui.calibration_dialog import LENGTH_UNITS, _to_um, set_length_value, split_length_um
from ui.canvas import GrainCanvas
from ui.canvas.edit_actions import GrainEditController
from ui.design import icons
from ui.design.tokens import SPACE
from ui.ai_probe import device_probe, gpu_tooltip
from ui.detection_modes import AI_DEVICE_DESC, AI_DEVICE_TITLES, AI_DEVICES, AI_MODE, \
    FALLBACK_MODE, MODES, default_mode, normalize_ai_device, normalize_mode, sam_model_available
from ui.format import astm_g, fmt_int, fmt_px_per_um
from ui.pages.common import CardGrid, MetricCard, Panel, SelectableCard, scroll
from ui.pages.filter_card import FilterCard, Reveal
from ui.pages.image_tree import ImageTree
from ui.pages.results_table import ResultsTable
from ui.widgets import (
    AnimatedButton, Badge, Card, CollapsibleSection, EmptyState, FadeStackedWidget,
    IconButton, KeyValueList, ProgressRing, SegmentedControl, label, pulse_attention,
)
from ui.widgets.layout import ResponsiveToolbar, group as tool_group
from ui.workers import AnalysisJob, AnalysisQueue

# UX-02: shown (spotlighted like the guided tour) when Analyze is pressed
# before every image has a confirmed scan area and scale
GATE_TEXT = ("This must be set. Check the scan regions and magnifications on each image "
             "and edit any that are wrong before starting analysis.")
GATE_TITLE = "Scan area and scale needed"

_SOURCE = {"auto": ("Auto", "info"), "metadata": ("Metadata", "info"),
           "manual": ("Manual", "success"), "all": ("All images", "neutral"),
           "saved": ("Saved", "neutral"), "profile": ("Profile", "info"),
           "": ("", "neutral")}

__all__ = ["AnalyzePage", "ParamPanel", "SetupTile", "ModeCard", "GATE_TEXT", "MODES",
           "sam_model_available"]


#: UX-09: coalescing delay for per-image summary refreshes.
SUMMARY_DEBOUNCE_MS = 100
#: UPDATE 4 item 10b: the (slow) AI device probe starts after the window shows
DEVICE_PROBE_DELAY_MS = 1500

AI_MISSING_TIP = ("The AI model file is missing from this installation. "
                  "Reinstall or repair the application to enable it.")


class ModeCard(SelectableCard):
    def __init__(self, key: str, title: str, icon: str, desc: str, available: bool = True,
                 parent=None, device: str = "") -> None:
        super().__init__(parent=parent)
        self.key = key
        self.device = device                 # "gpu" | "cpu" for the AI-assisted entries
        self.available = available
        self.setAccessibleName(title)
        self.body_layout().setSpacing(SPACE.xs)
        top = QHBoxLayout()
        top.setSpacing(SPACE.sm)
        ic = label()
        ic.setPixmap(icons.pixmap(icon, 18))
        top.addWidget(ic)
        top.addWidget(label(title, "body_strong"), 1)
        self.badge = Badge("", "neutral")
        self.badge.hide()
        top.addWidget(self.badge)
        if not available:
            self.set_badge("Not installed", "warning")
        elif key == AI_MODE and not device:
            self.set_badge("Default", "accent")
        self.body_layout().addLayout(top)
        d = label(desc, "caption")
        d.setWordWrap(True)
        self.body_layout().addWidget(d)
        self.body_layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setContentsMargins(SPACE.md + 2, SPACE.sm + 2, SPACE.md, SPACE.sm + 2)
        self.setToolTip(desc if available else AI_MISSING_TIP)
        self.setEnabled(available)

    def set_badge(self, text: str, kind: str = "neutral") -> None:
        self.badge.set_text(text)
        self.badge.set_kind(kind)
        self.badge.setVisible(bool(text))


class AiModeGroup(QWidget):
    """UPDATE 4 item 10b: the AI-assisted entry as two cards, "AI-Assisted
    (GPU)" and "AI-Assisted (CPU)".  Behaves like one ModeCard for the panel
    (``available`` / ``set_selected``); the chosen device is highlighted."""

    def __init__(self, available: bool, parent=None) -> None:
        super().__init__(parent)
        self.key = AI_MODE
        self.available = available
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.sm)
        self.cards: Dict[str, ModeCard] = {}
        for dev in AI_DEVICES:
            c = ModeCard(AI_MODE, AI_DEVICE_TITLES[dev], f"device_{dev}", AI_DEVICE_DESC[dev],
                         available=available, device=dev)
            c.setObjectName(f"aiMode_{dev}")
            self.cards[dev] = c
            v.addWidget(c)
        self._device = "cpu"
        self._selected = False
        if not available:
            self.setToolTip(AI_MISSING_TIP)
        self.setEnabled(available)

    def set_device(self, device: str) -> None:
        self._device = device if device in self.cards else "cpu"
        self.set_selected(self._selected)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        for dev, c in self.cards.items():
            c.set_selected(self._selected and dev == self._device)

    def is_selected(self) -> bool:
        return self._selected


class ParamPanel(QWidget):
    """Detection mode, excluded regions and advanced parameters.  The page
    inserts calibration, scan area and grain filters between them."""

    changed = Signal()
    mode_changed = Signal(str)
    device_changed = Signal(str)          # UPDATE 4 item 10b: "gpu" | "cpu" picked by the user
    show_excluded_regions = Signal(bool)

    def __init__(self, parent=None, probe=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.md)
        self._mode = default_mode()
        self._device_pref = ""            # "gpu" | "cpu" | "" (fresh install)
        self._probe = probe if probe is not None else device_probe()
        self._closing = False

        self.sec_mode = CollapsibleSection("Detection mode", expanded=True)
        grid = QVBoxLayout()
        grid.setSpacing(SPACE.sm)
        grid.setContentsMargins(0, 0, 0, 0)
        self.mode_cards: Dict[str, QWidget] = {}
        self.device_cards: Dict[str, ModeCard] = {}
        sam_ok = sam_model_available()
        for key, title, ic, desc in MODES:
            if key == AI_MODE:
                c = AiModeGroup(available=sam_ok)
                for dev, card in c.cards.items():
                    card.clicked.connect(lambda d=dev: self.choose_device(d))
                self.device_cards = c.cards
            else:
                c = ModeCard(key, title, ic, desc, available=True)
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

        # --- advanced (hidden while AI-assisted is selected, UX-01)
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
        self.reset_btn.setToolTip("Restore the factory detection parameters "
                                  "(mode: AI-assisted when installed)")
        self.reset_btn.clicked.connect(self.reset)
        self.sec_adv.add_widget(self.reset_btn)
        v.addWidget(self.sec_adv)

        for w in (self.inv_thr, self.inv_w, self.inv_a, self.min_sz, self.max_sz, self.ws_dist):
            w.valueChanged.connect(self._changed)
        for cb in (self.clahe, self.watershed, self.adaptive, self.dark_grains):
            cb.toggled.connect(self._changed)
        self._loading = False
        self.set_params(DetectionParams(detection_mode=default_mode()))
        # UPDATE 4 item 10b: which device can run the AI model is probed in
        # the background (PyTorch loads slowly); the GPU entry updates when
        # the answer arrives.  Started after the window has painted.
        self._probe.ready.connect(self._on_device_info)
        self._apply_device_state()
        self._last_sam_device = self.sam_device()
        # owned timer: dies with the panel and is stopped when the window closes
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._start_device_probe)
        if sam_ok and self._probe.info() is None:
            self._probe_timer.start(DEVICE_PROBE_DELAY_MS)

    def _start_device_probe(self) -> None:
        """Delayed start-up GPU check; skipped once the app is closing."""
        from PySide6.QtCore import QCoreApplication
        from ui.workers import is_shutting_down
        if self._closing or is_shutting_down() or QCoreApplication.closingDown():
            return
        self._probe.start()

    def stop_background(self) -> None:
        """The main window is closing: never start the device check now."""
        self._closing = True
        self._probe_timer.stop()

    # ------------------------------------------------------------ AI device (item 10b)
    def _device_info(self):
        return self._probe.info()

    def device(self) -> str:
        """The highlighted AI device: "gpu" | "cpu".  A saved "gpu" on a PC
        whose graphics card cannot be used shows (and runs) "cpu"."""
        info, pref = self._device_info(), self._device_pref
        if info is None:                          # still checking
            return "gpu" if pref == "gpu" else "cpu"
        if not getattr(info, "gpu_available", False):
            return "cpu"
        return pref or "gpu"                      # fresh install: GPU when usable

    def sam_device(self) -> str:
        """Device handed to the detector (DetectionParams.sam_device).  While
        the probe is still running a GPU choice is sent as "auto" so the
        detector falls back to the CPU instead of failing."""
        if self._device_info() is None and self._device_pref == "gpu":
            return "auto"
        return self.device()

    def device_preference(self) -> str:
        return self._device_pref

    def set_device_preference(self, pref) -> None:
        """Saved choice from the settings ("gpu" / "cpu" / "" = none yet)."""
        self._device_pref = normalize_ai_device(pref)
        self._apply_device_state()

    def choose_device(self, dev: str) -> None:
        """The user clicked "AI-Assisted (GPU)" or "(CPU)"."""
        dev = normalize_ai_device(dev) or "cpu"
        card = self.device_cards.get(dev)
        if card is None or not card.isEnabled():
            return
        self._device_pref = dev
        self._apply_device_state()
        self.device_changed.emit(dev)
        self.set_mode(AI_MODE, emit=True)

    def recheck_device(self) -> None:
        """Re-read the (cached) device answer in the background, e.g. after
        the graphics card reported an error during a run."""
        if sam_model_available():
            self._probe.start(again=True)

    def _on_device_info(self, _info) -> None:
        before = getattr(self, "_last_sam_device", None)
        self._apply_device_state()
        now = self.sam_device()
        self._last_sam_device = now
        if now != before:
            # e.g. cpu -> gpu on a fresh install once the check says the card
            # works: the session's saved parameters must follow
            self._changed()

    def _apply_device_state(self) -> None:
        grp = self.mode_cards.get(AI_MODE)
        if not isinstance(grp, AiModeGroup) or not sam_model_available():
            return
        info = self._device_info()
        gpu, cpu = self.device_cards["gpu"], self.device_cards["cpu"]
        if info is not None and not getattr(info, "torch_available", True):
            # the AI component itself is missing: both entries unavailable
            reason = getattr(info, "gpu_reason", "") or AI_MISSING_TIP
            for c in (gpu, cpu):
                c.set_badge("Not installed", "warning")
                c.setToolTip(reason)
                c.setEnabled(False)
            grp.available = False
            grp.setToolTip(reason)
            if self._mode == AI_MODE:
                self.set_mode(FALLBACK_MODE, emit=True)
            return
        gpu_ok = info is not None and bool(getattr(info, "gpu_available", False))
        gpu.setEnabled(info is None or gpu_ok)
        gpu.setToolTip(gpu_tooltip(info))
        if info is None:
            gpu.set_badge("Checking…", "neutral")
        elif gpu_ok:
            gpu.set_badge("Default", "accent")
        else:
            gpu.set_badge("Not available", "neutral")
        cpu.set_badge("Default" if info is not None and not gpu_ok else "", "accent")
        cpu.setToolTip(AI_DEVICE_DESC["cpu"] + ". Works on every PC.")
        grp.set_device(self.device())

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
        key = normalize_mode(key)
        if key not in self.mode_cards or not self.mode_cards[key].available:
            key = normalize_mode("")
            if key not in self.mode_cards or not self.mode_cards[key].available:
                key = FALLBACK_MODE
        self._mode = key
        for k, c in self.mode_cards.items():
            c.set_selected(k == key)
        self.sec_adv.setVisible(key != AI_MODE)
        self.mode_changed.emit(key)
        if emit:
            self.changed.emit()

    def mode(self) -> str:
        return self._mode

    def reset(self) -> None:
        """Factory parameters with the default mode (AI-assisted if installed)."""
        self.set_params(DetectionParams(detection_mode=default_mode()))
        self.changed.emit()

    def set_params(self, p: DetectionParams) -> None:
        self._loading = True
        self.set_mode(p.detection_mode)
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
        if hasattr(p, "sam_device"):                 # UPDATE 4 item 10b
            p.sam_device = self.sam_device()
        for name, w in (("invalid_intensity_threshold", self.inv_thr),
                        ("invalid_min_width_px", self.inv_w), ("invalid_min_area_px", self.inv_a)):
            if hasattr(p, name):
                setattr(p, name, int(w.value()))
        return p


ANALYZE_VIEWS = ("original", "overlay", "excluded")     # order of the view segments


#: a deferred "enter the length" pulse is dropped if the row has not shown by then
ATTENTION_DEFER_MS = 4000


class SetupTile(Card):
    """UX-02: scan area + scale of the images, shown under the canvas.

    Row 1: readiness of every image + "Auto-find scan area & scale bar
    (all images)".  Row 2: the current image's scan area and scale with
    where they came from and Edit.  Row 3 (when a scale bar was found but
    its length is unknown): type the bar's length and pick its unit
    (nm / µm / mm) in the dropdown beside it."""

    auto_find_requested = Signal()
    edit_scan_requested = Signal()
    edit_scale_requested = Signal()
    bar_length_entered = Signal(float, bool)       # µm, also same-bar images

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self.setObjectName("setupTile")
        self.layout().setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        self.layout().setSpacing(SPACE.sm)
        b = self.body_layout()
        b.setSpacing(SPACE.sm)
        top = QHBoxLayout()
        top.setSpacing(SPACE.sm)
        ic = label()
        ic.setPixmap(icons.pixmap("scan_area", 18))
        top.addWidget(ic)
        top.addWidget(label("Scan area & scale", "h3"))
        self.status = Badge("Not checked", "warning", dot=True)
        self.status.setToolTip("Every image needs a confirmed scan area and scale "
                               "(magnification) before it is analysed")
        top.addWidget(self.status)
        self.progress = label("", "caption")
        top.addWidget(self.progress, 1)
        self.btn_auto = AnimatedButton("Auto-find scan area & scale bar (all images)",
                                       "mdi6.auto-fix", "secondary", "sm")
        self.btn_auto.setToolTip("Find the SEM info bar (left out of the scan area) and the "
                                 "scale from the file's metadata or the scale bar, on every "
                                 "image in the analyzer. Values you set by hand are kept.")
        self.btn_auto.clicked.connect(self.auto_find_requested)
        top.addWidget(self.btn_auto)
        b.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(SPACE.xl)
        self.scan_col, self.scan_val, self.scan_src, self.btn_scan = self._column(
            "SCAN AREA", "Edit the analysed rectangle of this image (Ctrl+R)")
        self.scale_col, self.scale_val, self.scale_src, self.btn_scale = self._column(
            "SCALE", "Measure the scale bar of this image (Ctrl+K)")
        self.btn_scan.clicked.connect(self.edit_scan_requested)
        self.btn_scale.clicked.connect(self.edit_scale_requested)
        row.addLayout(self.scan_col, 1)
        row.addLayout(self.scale_col, 1)
        b.addLayout(row)

        # UPDATE 4 item 11: how the image was taken (read-only, filled on load)
        self.details_row = QWidget()
        self.details_row.setObjectName("imageDetails")
        dcol = QVBoxLayout(self.details_row)
        dcol.setContentsMargins(0, 0, 0, 0)
        dcol.setSpacing(2)
        dh = QHBoxLayout()
        dh.setContentsMargins(0, 0, 0, 0)
        dh.setSpacing(SPACE.sm)
        dh.addWidget(label("IMAGE DETAILS", "overline"))
        self.details_src = Badge("", "neutral")
        self.details_src.setToolTip("Where these values came from")
        dh.addWidget(self.details_src)
        self.details_check = Badge("Please check", "warning", dot=True)
        self.details_check.setToolTip("Read from the image automatically. Compare with the "
                                      "image's data bar.")
        self.details_check.hide()
        dh.addWidget(self.details_check)
        self.details_val = label("", "body")
        self.details_val.setObjectName("imageDetailsValue")
        self.details_val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.details_val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dh.addWidget(self.details_val, 1)
        dcol.addLayout(dh)
        self.details_hint = label("", "caption")
        self.details_hint.setObjectName("imageDetailsHint")
        self.details_hint.setWordWrap(True)
        self.details_hint.hide()
        dcol.addWidget(self.details_hint)
        b.addWidget(self.details_row)

        self.bar_row = QWidget()
        bcol = QVBoxLayout(self.bar_row)
        bcol.setContentsMargins(0, 0, 0, 0)
        bcol.setSpacing(SPACE.xs)
        br = QHBoxLayout()
        br.setContentsMargins(0, 0, 0, 0)
        br.setSpacing(SPACE.sm)
        bcol.addLayout(br)
        self.bar_lbl = label("", "caption")
        br.addWidget(self.bar_lbl)
        # UPDATE 4 item 6: the number and its unit are separate controls
        self.bar_len = QDoubleSpinBox()
        self.bar_len.setObjectName("barLengthValue")
        self.bar_len.setRange(0.0, 100000.0)
        self.bar_len.setDecimals(3)
        self.bar_len.setSpecialValueText("length?")
        self.bar_len.setToolTip("The number printed next to the scale bar in the info bar "
                                "(choose its unit on the right)")
        self.bar_len.setAccessibleName("Scale-bar length")
        self.bar_len.setMinimumWidth(96)
        br.addWidget(self.bar_len)
        self.bar_unit = QComboBox()
        self.bar_unit.setObjectName("barLengthUnit")
        self.bar_unit.addItems(list(LENGTH_UNITS))
        self.bar_unit.setCurrentText("µm")
        self.bar_unit.setToolTip("Unit printed on the scale-bar label")
        self.bar_unit.setAccessibleName("Scale-bar length unit")
        br.addWidget(self.bar_unit)
        self.bar_same = QCheckBox("Also images with the same scale bar")
        self.bar_same.setChecked(True)
        self.bar_same.setToolTip("Use this length for every image whose scale bar has the same "
                                 "length in pixels (same magnification) and no scale from its "
                                 "metadata or set by hand")
        br.addWidget(self.bar_same)
        br.addStretch(1)
        self.btn_bar = AnimatedButton("Apply", "check", "primary", "sm")
        self.btn_bar.setToolTip("Scale = scale-bar pixels ÷ the length you entered")
        self.btn_bar.clicked.connect(self._apply_bar_length)
        self.bar_len.lineEdit().returnPressed.connect(self._apply_bar_length)
        br.addWidget(self.btn_bar)
        # UPDATE 4 item 4: what was read from the label, and whether it needs a look
        hl = QHBoxLayout()
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(SPACE.sm)
        self.bar_check = Badge("Please check", "warning", dot=True)
        self.bar_check.setToolTip("The length was read from the image automatically. "
                                  "Compare it with the label next to the scale bar.")
        self.bar_check.hide()
        hl.addWidget(self.bar_check, 0, Qt.AlignTop)
        self.bar_hint = label("", "caption")
        self.bar_hint.setObjectName("barReadHint")
        self.bar_hint.setWordWrap(True)
        self.bar_hint.hide()
        hl.addWidget(self.bar_hint, 1)
        bcol.addLayout(hl)
        self.bar_row.hide()
        b.addWidget(self.bar_row)
        # a length the operator is typing is never replaced by a refresh or a
        # label read for the same image (UPDATE 4 item 4)
        self._uid = None
        self._typed_uid = None
        self.bar_len.valueChanged.connect(self._on_typed)
        self.bar_unit.currentIndexChanged.connect(self._on_typed)
        self._attention_pending = False       # follow-up b: pulse once the row shows
        self._attention_timer = QTimer(self)
        self._attention_timer.setSingleShot(True)
        self._attention_timer.setInterval(0)
        self._attention_timer.timeout.connect(self._pulse_if_pending)
        self._attention_expiry = QTimer(self)
        self._attention_expiry.setSingleShot(True)
        self._attention_expiry.setInterval(ATTENTION_DEFER_MS)
        self._attention_expiry.timeout.connect(self.cancel_attention)
        self.bar_row.installEventFilter(self)

    # UPDATE 4 item 6 ----------------------------------------------------
    def bar_length_um(self) -> float:
        """Entered scale-bar length converted to µm (0 = not entered)."""
        return _to_um(float(self.bar_len.value()), self.bar_unit.currentText())

    def set_bar_length_um(self, length_um: float) -> None:
        """Show a length (µm) as number + natural unit; 0 clears the number
        and keeps the unit the user last chose."""
        self.bar_len.blockSignals(True)
        self.bar_unit.blockSignals(True)
        if length_um and length_um > 0:
            value, unit = split_length_um(length_um)
            self.bar_unit.setCurrentText(unit)
            set_length_value(self.bar_len, value)      # tiny lengths never round to 0
        else:
            set_length_value(self.bar_len, 0.0)
        self.bar_len.blockSignals(False)
        self.bar_unit.blockSignals(False)

    def set_bar_length_text(self, text: str) -> bool:
        """Accept "500 nm" / "2.5 µm" / "1 mm" / "20" (µm) and split it into
        the number box and the unit dropdown.  False if it cannot be read."""
        m = re.fullmatch(r"\s*([0-9]*[.,]?[0-9]+)\s*(nm|µm|μm|um|mm)?\s*", text or "")
        if not m:
            return False
        value = float(m.group(1).replace(",", "."))
        unit = {"um": "µm", "μm": "µm"}.get(m.group(2) or "µm", m.group(2) or "µm")
        self.set_bar_length_um(_to_um(value, unit))
        return True

    def _apply_bar_length(self) -> None:
        um = self.bar_length_um()
        if um > 0:
            self._typed_uid = None                 # now it is the image's value
            self.bar_length_entered.emit(um, self.bar_same.isChecked())

    def _on_typed(self, *_a) -> None:
        # programmatic fills block signals, so this is the operator
        self._typed_uid = self._uid

    def is_typing(self) -> bool:
        """The operator has typed a length for the shown image that is not
        applied yet."""
        return self._typed_uid is not None and self._typed_uid == self._uid

    def set_bar_length(self, value: float, unit: str) -> None:
        """Fill number + unit exactly as printed on the label ("100 nm")."""
        if unit not in LENGTH_UNITS or value <= 0:
            self.set_bar_length_um(_to_um(value, unit) if unit in LENGTH_UNITS else 0.0)
            return
        self.bar_len.blockSignals(True)
        self.bar_unit.blockSignals(True)
        self.bar_unit.setCurrentText(unit)
        set_length_value(self.bar_len, value)
        self.bar_len.blockSignals(False)
        self.bar_unit.blockSignals(False)

    def _show_reading(self, im, px: float) -> None:
        """UPDATE 4 item 4: fill the length from the label read off the image
        (unless the operator is typing) and say plainly when it needs a
        look.  Returns nothing; sets the hint, the badge and the box."""
        rd = getattr(im, "bar_read", None) or {}
        read_um = float(rd.get("um") or 0.0)
        shown = f"{float(rd.get('value') or 0):g} {rd.get('unit', '')}".strip()
        typing = self.is_typing()
        if not typing:
            if im.bar_um > 0:
                if read_um > 0 and abs(im.bar_um - read_um) <= 0.01 * read_um:
                    self.set_bar_length(float(rd.get("value") or 0), str(rd.get("unit", "")))
                else:
                    self.set_bar_length_um(im.bar_um)
            elif read_um > 0:
                self.set_bar_length(float(rd.get("value") or 0), str(rd.get("unit", "")))
            else:
                self.set_bar_length_um(0.0)
        hint, check = "", False
        if rd.get("status") == "engine_missing":
            hint = ("The label could not be read: the text-reading part of the program is "
                    "missing. Please reinstall SEM Grain Analyzer. Type the length instead.")
        elif read_um > 0 and im.scale_source == "metadata" and rd.get("meta_ok") is False:
            check = True
            hint = (f"The label reads {shown}, but the scale stored in the image file "
                    "differs. The file's scale is in use; press Apply to use the label.")
        elif read_um > 0 and im.bar_um > 0 and abs(im.bar_um - read_um) > 0.01 * read_um:
            check = True
            hint = (f"The label reads {shown}, but a different length is in use for this "
                    "image. Correct it if needed, then press Apply.")
        elif read_um > 0 and im.bar_um <= 0 and px <= 0:
            check = True
            hint = (f"Read “{shown}” from the label; it could not be double-checked. "
                    "Compare it with the image, then press Apply.")
        elif read_um > 0 and im.bar_um > 0 and im.scale_source == "auto":
            hint = f"Read from the scale-bar label ({shown})."
        self.bar_check.setVisible(check)
        self.bar_hint.setText(hint)
        self.bar_hint.setProperty("tone", "warning" if (check or rd.get("status") ==
                                                        "engine_missing") else None)
        self.bar_hint.style().unpolish(self.bar_hint)
        self.bar_hint.style().polish(self.bar_hint)
        self.bar_hint.setVisible(bool(hint))

    def reading_conflict(self, im) -> bool:
        """The label disagrees with the scale from the file's metadata."""
        rd = getattr(im, "bar_read", None) or {}
        return (float(rd.get("um") or 0) > 0 and im.scale_source == "metadata"
                and rd.get("meta_ok") is False)

    def draw_attention_to_length(self) -> bool:
        """Pulse the length box and focus it (after Auto-find) when the
        scale-bar row is showing.  If the row is meant to show but its page is
        not on screen yet, the pulse waits until the row appears.  True if it
        pulsed or will pulse; False when the row is hidden (nothing to enter)."""
        if self.bar_row.isHidden():
            self.cancel_attention()
            return False
        if not self.bar_row.isVisible():
            self._attention_pending = True        # shown later -> eventFilter
            self._attention_expiry.start()        # ... but only soon after Auto-find
            return True
        self.cancel_attention()
        return pulse_attention(self.bar_len) is not None

    def attention_pending(self) -> bool:
        return self._attention_pending

    def cancel_attention(self) -> None:
        """Drop a deferred pulse (image or scale changed, or it expired)."""
        self._attention_pending = False
        self._attention_expiry.stop()
        self._attention_timer.stop()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.bar_row and ev.type() == QEvent.Show and self._attention_pending:
            # after the page transition has laid the row out; the timer is
            # owned by the tile, so it dies with it
            self._attention_timer.start()
        elif obj is self.bar_row and ev.type() == QEvent.Hide and self.bar_row.isHidden():
            self.cancel_attention()               # row itself hidden: nothing to enter
        return super().eventFilter(obj, ev)

    def _pulse_if_pending(self) -> None:
        if self._attention_pending and self.bar_row.isVisible():
            self.cancel_attention()
            pulse_attention(self.bar_len)

    def _column(self, title: str, tip: str):
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(SPACE.sm)
        head.addWidget(label(title, "overline"))
        src = Badge("", "neutral")
        src.setToolTip("Where this value came from")
        head.addWidget(src)
        head.addStretch(1)
        btn = AnimatedButton("Edit…", "edit", "ghost", "sm")
        btn.setToolTip(tip)
        head.addWidget(btn)
        col.addLayout(head)
        val = label("", "body")
        val.setWordWrap(True)
        val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(val)
        return col, val, src, btn

    @staticmethod
    def _set_src(badge: Badge, key: str) -> None:
        text, kind = _SOURCE.get(key, (key.capitalize(), "neutral"))
        badge.set_text(text)
        badge.set_kind(kind)
        badge.setVisible(bool(text))

    _DETAIL_SRC = {"metadata": ("From file", "info"), "info_bar": ("Data bar", "info"),
                   "both": ("File + data bar", "info"), "": ("", "neutral")}

    def show_details(self, state, im) -> None:
        """UPDATE 4 item 11: instrument · magnification · kV · WD · detector
        of the shown image, with "Please check" like the scale-bar label."""
        from ui import image_details as idt
        info = getattr(im, "image_info", None) if im is not None else None
        svc = getattr(state, "image_details", None)
        reading = im is not None and svc is not None and svc.is_reading(im.uid)
        text = idt.summary_text(info)
        hint, tone = "", None
        if im is None or getattr(im, "loading", False):
            text = "—"
        elif not text:
            if reading:
                text = "Reading…"
            elif info is not None and info.ocr_status == "engine_missing":
                text = "—"
                hint = ("The data bar could not be read: the text-reading part of the program "
                        "is missing. Please reinstall SEM Grain Analyzer.")
                tone = "warning"
            elif info is not None and info.ocr_status == "not_run":
                text = "Not stored in the image file — Auto-find also reads the data bar"
            else:
                text = "Not found in the image file or its data bar"
        elif reading:
            text += "  ·  reading the data bar…"
        check = idt.check_text(info)
        if check:
            hint, tone = check, "warning"
        key = idt.source_key(info) if idt.summary_text(info) else ""
        s_text, s_kind = self._DETAIL_SRC.get(key, ("", "neutral"))
        self.details_src.set_text(s_text)
        self.details_src.set_kind(s_kind)
        self.details_src.setVisible(bool(s_text))
        self.details_check.setVisible(bool(check))
        self.details_val.setText(text)
        tip = idt.tooltip_text(info)
        self.details_val.setToolTip(tip or "Instrument, magnification, accelerating voltage, "
                                           "working distance and detector of this image")
        self.details_hint.setText(hint)
        self.details_hint.setProperty("tone", tone)
        self.details_hint.style().unpolish(self.details_hint)
        self.details_hint.style().polish(self.details_hint)
        self.details_hint.setVisible(bool(hint))

    def refresh(self, state, im) -> None:
        """Show the readiness of every image and the current image's values."""
        self.show_details(state, im)
        imgs = [x for x in state.images() if not x.loading and x.readable]
        n = len(imgs)
        ready = sum(1 for x in imgs if state.setup_ready(x))
        loading = sum(1 for x in state.images() if x.loading)
        if state.is_setting_up():
            pass
        elif loading:
            self.status.set_text(f"Loading {loading} image{'s' if loading != 1 else ''}…")
            self.status.set_kind("neutral")
        elif n and ready == n:
            self.status.set_text(f"All {n} image{'s' if n != 1 else ''} ready")
            self.status.set_kind("success")
        else:
            self.status.set_text(f"{n - ready} of {n} image{'s' if n != 1 else ''} "
                                 "need checking" if n else "No images")
            self.status.set_kind("warning" if n else "neutral")
        self.btn_auto.setEnabled(bool(n) and not state.is_setting_up())
        doc = state.session
        uid = getattr(im, "uid", None)
        if uid != self._uid:                       # another image: typing is not carried over
            self._uid = uid
            self._typed_uid = None
        if im is None or doc is None:
            self.scan_val.setText("—")
            self.scale_val.setText("—")
            self._set_src(self.scan_src, "")
            self._set_src(self.scale_src, "")
            self.bar_row.hide()
            return
        if im.loading or not im.readable or not im.shape:
            self.scan_val.setText("Loading…" if im.loading else "Image not readable")
            self.scale_val.setText("—")
            self._set_src(self.scan_src, "")
            self._set_src(self.scale_src, "")
            self.bar_row.hide()
            return
        # scan area
        rect = state.scan_for(im)
        H, W = im.shape[:2]
        if rect is None:
            self.scan_val.setText("Not set — run Auto-find or Edit")
            self.scan_val.setProperty("tone", "warning")
            self._set_src(self.scan_src, "")
        else:
            x, y, w, h = rect
            info = state.info_bar_for(im)
            ar = tuple(info.get("analysis_rect") or ()) if info else ()
            what = ("info bar left out" if ar and tuple(rect) == ar else
                    "full image" if (x, y, w, h) == (0, 0, W, H) else f"at ({x}, {y})")
            self.scan_val.setText(f"{w} × {h} px — {what}")
            self.scan_val.setProperty("tone", None)
            self._set_src(self.scan_src, im.scan_source or ("all" if im.scan_rect is None
                                                            else "saved"))
        # scale
        px = state.px_for(im)
        if px <= 0:
            self.scale_val.setText(
                f"Not set — scale bar found ({im.bar_px:.0f} px); enter its length"
                if im.bar_px > 0 else "Not set — no scale bar or metadata found; use Edit")
            self.scale_val.setProperty("tone", "warning")
            self._set_src(self.scale_src, "")
        else:
            bar = (f"  ·  bar {im.bar_px:.0f} px = {im.bar_um:g} µm"
                   if im.bar_px > 0 and im.bar_um > 0 else "")
            self.scale_val.setText(f"{px:.4g} px/µm  ({1.0 / px:.4g} µm/px){bar}")
            self.scale_val.setProperty("tone", None)
            self._set_src(self.scale_src, im.scale_source or (
                "all" if im.px_override <= 0 else "saved"))
        for w in (self.scan_val, self.scale_val):
            w.style().unpolish(w)
            w.style().polish(w)
        show_bar = im.bar_px > 0 and (px <= 0 or im.scale_source == "auto"
                                      or self.reading_conflict(im))
        if show_bar:
            self.bar_lbl.setText(f"Scale bar found: {im.bar_px:.0f} px. Its length:")
            self._show_reading(im, px)
        self.bar_row.setVisible(show_bar)

    def set_progress(self, done: int, total: int) -> None:
        running = total > 0 and done < total
        self.btn_auto.set_loading(running)
        self.progress.setText(f"Checking {done} of {total} images…" if running else "")
        if running:
            self.status.set_text("Checking…")
            self.status.set_kind("info")


class AnalyzePage(QWidget):
    calibrate_requested = Signal()
    scan_area_requested = Signal()
    new_session_requested = Signal()
    open_projects_requested = Signal()
    review_requested = Signal()
    report_requested = Signal(object, str)      # UX-13: lot scope | None, action
    add_images_requested = Signal()
    busy_changed = Signal(bool)
    progress_changed = Signal(float, str)     # overall %, message (status bar)
    setup_required = Signal(str, str)         # UX-02: title, text (shell spotlights the tile)

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self.queue = AnalysisQueue(self)
        self._batch_total = 0
        self._batch_uids: List = []
        self._device_notes: List = []        # UPDATE 4 item 10b: (uid, note)
        self._run_btn: Optional[AnimatedButton] = None
        self._view_pref = "overlay"      # UPDATE 4 item 14: user's display mode
        self._rec = "session"           # HIER-01: "lot" when images live in the lot
        self._scale_key = "Session scale"
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_setup_all)
        # UX-09: per-image updates (a 200-image load streams in) coalesce into
        # one O(n) summary pass instead of one per image (O(n^2) overall).
        self._summary_timer = QTimer(self)
        self._summary_timer.setSingleShot(True)
        self._summary_timer.setInterval(SUMMARY_DEBOUNCE_MS)
        self._summary_timer.timeout.connect(self._refresh_summary)
        self._build()
        self._wire()
        self._relabel()
        # UPDATE 4 item 5: Resolution Profiles card + summary under the image
        from ui.pages.resolution_profiles_card import install_resolution_profiles
        install_resolution_profiles(self)

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
        self.film = ImageTree(self.state, checkable=True)
        self.film.setMinimumWidth(260)
        self.film.setMaximumWidth(320)
        fl.addWidget(self.film)
        h.addWidget(film_panel)

        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        cv.setSpacing(SPACE.sm)
        lead = QWidget()
        col = QVBoxLayout(lead)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.img_title = label("", "h3")
        self.img_sub = label("", "caption")
        for w in (self.img_title, self.img_sub):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            col.addWidget(w)
        self.centre_seg = SegmentedControl(["Image", "Results table"])
        self.centre_seg.setToolTip("The selected image, or a table of every image in the "
                                   "analyzer (group and sort by job, part or lot)")
        self.centre_seg.setFixedWidth(210)
        self.view_seg = SegmentedControl(["Original", "Overlay", "Excluded"])
        self.view_seg.setToolTip("Original image · detected grains · areas not analysed")
        self.view_seg.setFixedWidth(270)
        self.btn_zo = IconButton("zoom_out", "Zoom out (−)")
        self.btn_zi = IconButton("zoom_in", "Zoom in (+)")
        self.btn_fit = IconButton("fit", "Fit to window (F)")
        self.btn_11 = IconButton("target", "Actual pixels, 1:1 (1)")
        # FIX-09: groups wrap onto a second row instead of overlapping
        self.toolbar = ResponsiveToolbar(lead, [
            tool_group(self.centre_seg),
            tool_group(self.view_seg),
            tool_group(self.btn_zo, self.btn_zi, self.btn_fit, self.btn_11)])
        cv.addWidget(self.toolbar)

        self.centre_stack = FadeStackedWidget()
        img_page = QWidget()
        ip = QVBoxLayout(img_page)
        ip.setContentsMargins(0, 0, 0, 0)
        ip.setSpacing(SPACE.sm)
        self.canvas = GrainCanvas(placeholder="Select an image in the list")
        self.canvas.enable_opacity_control()        # UPDATE 4 item 9
        ip.addWidget(self.canvas, 1)
        self.setup_tile = SetupTile()
        ip.addWidget(self.setup_tile)
        self.centre_stack.addWidget(img_page)
        self.table = ResultsTable(self.state)
        self.centre_stack.addWidget(self.table)
        cv.addWidget(self.centre_stack, 1)

        self.st_images = MetricCard("Images analysed", 0, "", 0)
        self.st_grains = MetricCard("Grains (session)", 0, "", 0)
        self.st_diam = MetricCard("Mean diameter", 0, "µm", 2)
        self.st_g = MetricCard("ASTM grain size", 0, "G", 1)
        for c in (self.st_images, self.st_grains, self.st_diam, self.st_g):
            c.setMaximumHeight(96)
        # FIX-09: 4 across when there is room, else 2 x 2 (labels never clip)
        self.stats_grid = CardGrid(min_col=168, max_cols=4, spacing=SPACE.md)
        self.stats_grid.adopt([self.st_images, self.st_grains, self.st_diam, self.st_g])
        cv.addWidget(self.stats_grid)
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
        self.btn_all.setToolTip("Analyse every image in the analyzer (F5)")
        self.btn_cur = AnimatedButton("Analyze current", "run", "secondary")
        self.btn_cur.setToolTip("Re-analyse only the selected image (Ctrl+F5)")
        self.btn_cancel = AnimatedButton("Cancel", "stop", "danger")
        self.btn_cancel.setToolTip("Stop the analysis right away (Esc)")
        self.btn_cancel.hide()
        run.body_layout().addWidget(self.btn_all)
        br = QHBoxLayout()
        br.addWidget(self.btn_cur, 1)
        br.addWidget(self.btn_cancel, 1)
        run.body_layout().addLayout(br)
        self.btn_sel = AnimatedButton("Analyze selected", "run", "secondary")
        self.btn_sel.setToolTip("Analyze only the images ticked in the image list")
        self.btn_sel.hide()          # shown when 2 or more images are ticked
        run.body_layout().addWidget(self.btn_sel)
        iv.addWidget(run)

        # 1 detection mode · 5 excluded regions · 6 advanced (ParamPanel)
        self.params = ParamPanel()
        iv.addWidget(self.params)

        # 2 calibration
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
        self.btn_cal.setToolTip("Click the two ends of the scale bar and enter its length; "
                                "apply it to this image only or to all images (Ctrl+K)")
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

        # 3 scan area
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
        self.btn_scan_clear.setToolTip("Analyse the whole frame (this image, or every image)")
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

        # 4 grain filters (revealed with the first result)
        self.filters = FilterCard()
        self.filters_host = Reveal(self.filters)
        self.params.layout().insertWidget(3, self.filters_host)

        # overlay opacity (UX-05) -- also used for report images
        self.sec_overlay = CollapsibleSection("Overlay", expanded=True)
        orow = QHBoxLayout()
        orow.setSpacing(SPACE.sm)
        orow.addWidget(label("Opacity", tone="secondary"))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setSingleStep(5)
        self.opacity.setPageStep(10)
        self.opacity.setToolTip("How strongly the detected grains are drawn over the image. "
                                "Report images use the same setting.")
        self.opacity.setAccessibleName("Overlay opacity")
        orow.addWidget(self.opacity, 1)
        self.opacity_val = label("100 %", "caption")
        self.opacity_val.setMinimumWidth(40)
        self.opacity_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        orow.addWidget(self.opacity_val)
        oh = QWidget()
        oh.setLayout(orow)
        self.sec_overlay.add_widget(oh)
        self.params.layout().addWidget(self.sec_overlay)

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
        st.calibration_changed.connect(self._refresh_timer.start)
        st.filters_changed.connect(self._refresh_filters)
        st.filtering_changed.connect(
            lambda uid, busy: uid == st.current_uid and self.filters.set_busy(busy))
        st.setup_changed.connect(self._refresh_timer.start)
        st.setup_progress.connect(self._on_setup_progress)
        st.setup_finished.connect(self._on_setup_finished)
        st.records_loading.connect(self._on_records_loading)
        st.records_loaded.connect(self._on_records_loaded)
        st.overlay_opacity_changed.connect(self._sync_opacity)
        self.film.current_changed.connect(st.set_current_image)
        self.film.files_dropped.connect(st.add_images)
        # a deferred "enter the length" pulse is stale once the image or scale changes
        st.current_image_changed.connect(lambda _u: self.setup_tile.cancel_attention())
        st.calibration_changed.connect(self.setup_tile.cancel_attention)
        self.film.add_requested.connect(self.add_images_requested)
        self.film.remove_requested.connect(self.remove_images)
        self.film.restore_requested.connect(self.restore_images)
        self.centre_seg.current_changed.connect(self._on_centre_view)
        self.table.open_image.connect(self._open_from_table)
        self.table.report_requested.connect(self.report_requested)
        self.view_seg.current_changed.connect(self._on_view_seg)
        self.canvas.view_changed.connect(self._sync_view_seg)
        self.canvas.delete_requested.connect(
            lambda ids: st.delete_grains(st.current_uid, ids))
        # lasso (L) / merge (M) / cut (C) work here too (UI-05 / INN-04)
        self.edits = GrainEditController(self.canvas, st, self.toasts, self)
        self.btn_zo.clicked.connect(lambda: self.canvas.zoom_by(0.8))
        self.btn_zi.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self.btn_fit.clicked.connect(self.canvas.fit)
        self.btn_11.clicked.connect(self.canvas.actual_size)
        self.btn_all.clicked.connect(self.analyze_all)
        self.btn_cur.clicked.connect(self.analyze_current)
        self.btn_sel.clicked.connect(self.analyze_selected)
        self.film.checked_changed.connect(self._on_checked_changed)
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
        self.setup_tile.auto_find_requested.connect(self.auto_find)
        self.setup_tile.edit_scan_requested.connect(self.scan_area_requested)
        self.setup_tile.edit_scale_requested.connect(self.calibrate_requested)
        self.setup_tile.bar_length_entered.connect(self._on_bar_length)
        st.info_bar_ready.connect(lambda uid: uid == st.current_uid and self._refresh_info_bar())
        st.sem_metadata_ready.connect(
            lambda uid: uid == st.current_uid and self._refresh_calibration())
        st.image_details.ready.connect(self._on_image_details)
        st.profile_changed.connect(self._relabel)
        st.profile_changed.connect(self.table.relabel)
        self.params.changed.connect(lambda: st.set_params(self.params.get_params()))
        # UPDATE 4 item 10b: the AI device is remembered per PC, not per session
        self.params.set_device_preference(st.ai_device_preference)
        self.params.device_changed.connect(st.set_ai_device_preference)
        self.params.show_excluded_regions.connect(self.canvas.set_show_excluded_regions)
        self.filters.options_changed.connect(self._on_filter_options)
        self.filters.apply_all_requested.connect(self._apply_filters_all)
        self.filters.apply_image_requested.connect(self._apply_filters_image)
        self.filters.show_excluded_toggled.connect(self.canvas.set_show_excluded_grains)
        self.opacity.valueChanged.connect(self._on_opacity)
        self.opacity.sliderReleased.connect(self.state.persist_ui_state)
        self.canvas.overlay_opacity_edited.connect(self._on_canvas_opacity)
        self.queue.job_started.connect(lambda uid: st.set_image_status(uid, "running", 0, "Starting"))
        self.queue.job_progress.connect(self._on_job_progress)
        self.queue.job_finished.connect(self._on_job_finished)
        self.queue.job_failed.connect(self._on_job_failed)
        self.queue.overall_progress.connect(self._on_overall)
        self.queue.queue_finished.connect(self._on_queue_finished)
        # UPDATE 4 item 7: the one analysis gate (AppState.analysis_lock)
        st.analysis_lock.bind(busy_uids=self.busy_uids, stop=self.cancel)
        self.busy_changed.connect(st.analysis_lock.set_active)
        self._sync_opacity(st.overlay_opacity)

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
        self.table.mark_dirty()
        self._refresh_summary()
        self._on_current(self.state.current_uid)
        self._set_idle()

    def _on_image_details(self, uid) -> None:
        """UPDATE 4 item 11: the shown image's details arrived."""
        if uid == self.state.current_uid:
            s = self.state.session
            self.setup_tile.show_details(self.state, s.image(uid) if s is not None else None)

    def _on_image_updated(self, uid) -> None:
        self.film.update_item(uid)
        self.table.mark_dirty()
        if uid == self.state.current_uid:
            im = self.state.current_image()
            if im is not None and (self.canvas.result() is not im.result
                                   or self.canvas.raw() is not im.raw
                                   or self.canvas.excluded() != im.excluded):
                self._show_result()
            elif im is not None:
                self._update_title(im)
            self.setup_tile.refresh(self.state, im)
        if not self._summary_timer.isActive():       # throttle, never starve
            self._summary_timer.start()

    def _on_result_edited(self, uid) -> None:
        im = self.state.current_image()
        if uid == self.state.current_uid and im is not None and (
                self.canvas.result() is not im.result or self.canvas.raw() is not im.raw
                or self.canvas.excluded() != im.excluded):
            self._show_result()

    def _on_current(self, uid) -> None:
        self.film.set_current(uid)
        im = self.state.current_image()
        self.setup_tile.refresh(self.state, im)
        if im is None:
            self.canvas.set_image(None)
            self.img_title.setText("")
            self.img_sub.setText("")
            self.filters_host.reveal(False)
            return
        if im.image_bgr is None and im.readable:
            # evicted from the pixel cache: read it off the GUI thread
            self.canvas.set_placeholder("Loading image…")
            self.canvas.set_image(None)
            self.state.request_pixels(
                im.uid, lambda arr, uid=uid: arr is not None
                and self.state.current_uid == uid and self._on_current(uid))
        else:
            self.canvas.set_placeholder("Select an image in the list")
            self.state.touch_pixels(im)
            self.canvas.set_image(im.image_bgr, im.result, raw=im.raw, excluded=im.excluded)
        self.canvas.set_view(self._view_for(im))
        self.canvas.set_scan_rect(self.state.scan_for(im))
        self._update_title(im)
        self._refresh_calibration()
        self._refresh_filters()
        self._refresh_info_bar()

    def _show_result(self) -> None:
        im = self.state.current_image()
        if im is None:
            return
        self.canvas.set_result(im.result, raw=im.raw, excluded=im.excluded)
        self.canvas.set_view(self._view_for(im))
        self._update_title(im)
        self._refresh_filters()
        self._refresh_info_bar()

    # UPDATE 4 item 14: the display mode the user picked survives image
    # switches; an image without results shows "original" for itself only.
    def _on_view_seg(self, i: int) -> None:
        self._view_pref = ANALYZE_VIEWS[i]
        self.canvas.set_view(self._view_pref)

    def _view_for(self, im) -> str:
        return self._view_pref if im is not None and im.result is not None else "original"

    def _sync_view_seg(self, view: str) -> None:
        idx = {"original": 0, "overlay": 1, "excluded": 2}.get(view)
        if idx is not None and idx != self.view_seg.current_index():
            self.view_seg.blockSignals(True)
            self.view_seg.set_current_index(idx)
            self.view_seg.blockSignals(False)

    def _on_centre_view(self, i: int) -> None:
        self.centre_stack.set_current_index(i)
        for w in (self.view_seg, self.btn_zo, self.btn_zi, self.btn_fit, self.btn_11):
            w.setEnabled(i == 0)
        if i == 1:
            self.table.mark_dirty()

    def show_image_view(self) -> None:
        if self.centre_seg.current_index() != 0:
            self.centre_seg.set_current_index(0)
            self._on_centre_view(0)

    def show_table_view(self) -> None:
        if self.centre_seg.current_index() != 1:
            self.centre_seg.set_current_index(1)
            self._on_centre_view(1)

    def _open_from_table(self, uid) -> None:
        self.state.set_current_image(uid)
        self.show_image_view()

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
                                 f"stored in the {rec} folder — or load images from Projects.",
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
        self.btn_all.setToolTip("Analyse every image in the analyzer (F5)")
        if self.state.session is not None:
            self._refresh_calibration()
            self._set_idle()

    # ------------------------------------------------------------------ DET-05 / INN-05
    def _refresh_info_bar(self) -> None:
        im = self.state.current_image()
        info = self.state.info_bar_for(im)
        if im is not None and info is None and im.info_bar is None and not im.loading:
            self.state.probe_info_bar(im.uid)
        self.canvas.set_info_bar_rect(info.get("bar_rect") if info else None)
        self.ib_row.setVisible(bool(info))
        if info:
            ar = tuple(info.get("analysis_rect") or ())
            cur = self.state.scan_for(im)
            self.btn_ib_scan.setEnabled(bool(ar) and (cur is None or tuple(cur) != ar))
            if cur is None:
                self.scan_lbl.setText("Not set — the info bar is left out automatically once "
                                      "you run Auto-find")
            conf = float(info.get("confidence", 0.0) or 0.0)
            self.ib_chip.set_text("Info bar excluded" + (" (check)" if conf < 0.7 else ""))
            self.ib_chip.set_kind("info" if conf >= 0.7 else "warning")
        self.setup_tile.refresh(self.state, im)

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
        im.scale_source = "metadata"

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
        doc = self.state.session
        if doc is not None and doc.multi:
            rec = doc.record_for(im)
            if rec is not None:
                from ui.pages.results_table import level_ids
                ids = level_ids(rec)
                parts.append(" › ".join(x for x in (ids.get("project"), ids.get("sample"),
                                                     ids.get("lot")) if x))
        if im.loading:
            parts.append("Loading…")
        elif im.shape:
            h, w = im.shape[:2]
            parts.append(f"{w} × {h} px")
        if im.result is not None:
            parts.append(f"{fmt_int(im.result.grain_count)} grains")
            if im.excluded:
                parts.append(f"{len(im.excluded)} excluded")
        elif im.status == "error":
            parts.append(im.message.splitlines()[0] if im.message else "Error")
        self.img_sub.setText("  ·  ".join(parts))

    def _refresh_summary(self) -> None:
        self._summary_timer.stop()
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
            self.scan_lbl.setText("Not set — run Auto-find under the image, or set it here")
        self.canvas.set_scan_rect(rect)
        self.btn_cal_reset.setVisible(im is not None and im.px_override > 0)
        self._refresh_meta_row(im)
        self.btn_scan_reset.setVisible(im is not None and im.scan_rect is not None)
        self.setup_tile.refresh(self.state, im)

    def _refresh_setup_all(self) -> None:
        self.film.refresh_all()
        self.table.mark_dirty()
        self.setup_tile.refresh(self.state, self.state.current_image())

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

    # ------------------------------------------------------------------ UX-05 overlay opacity
    def _on_opacity(self, v: int) -> None:
        self.opacity_val.setText(f"{v} %")
        self.canvas.set_overlay_opacity(v / 100.0)
        self.state.set_overlay_opacity(v / 100.0, persist=not self.opacity.isSliderDown())

    def _on_canvas_opacity(self, v: float, final: bool) -> None:
        """UPDATE 4 item 9: the on-image opacity pill (live while dragging,
        written to the settings once it settles)."""
        self.state.set_overlay_opacity(v, persist=False)
        if final:
            self.state.persist_ui_state()

    def _sync_opacity(self, v: float) -> None:
        iv = int(round(float(v) * 100))
        if self.opacity.value() != iv:
            self.opacity.blockSignals(True)
            self.opacity.setValue(iv)
            self.opacity.blockSignals(False)
        self.opacity_val.setText(f"{iv} %")
        self.canvas.set_overlay_opacity(float(v))

    # ------------------------------------------------------------------ actions
    def _on_filter_options(self, opts, scope: str) -> None:
        uid = self.state.current_uid if scope == "image" else None
        self.state.set_filter_options(opts, uid)

    def _apply_filters_all(self, opts) -> None:
        self.state.apply_filters_to_all(opts)
        self._toast("Filters applied to all images",
                    "Every image in the analyzer now uses the same grain filters.",
                    "success")

    def _apply_filters_image(self, opts) -> None:
        self.state.set_filter_options(opts, self.state.current_uid)
        self._toast("Filters applied", "This image uses its own grain filters.", "success")

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
        if im is not None and self.scan_this.isChecked() and im.shape:
            h, w = im.shape[:2]
            self.state.set_scan_rect((0, 0, w, h), im.uid)   # this image: whole frame
        else:
            snap = self.state.set_scan_rect_all(None)        # every image: its whole frame
            self._toast_action("Full image for all images",
                               "Every image is analysed over its whole frame.", "success",
                               "Undo", lambda: self.state.restore_scans(snap))

    def _reset_cal(self) -> None:
        im = self.state.current_image()
        if im is not None:
            self.state.reset_image_calibration(im.uid)

    def _reset_scan(self) -> None:
        im = self.state.current_image()
        if im is not None:
            self.state.reset_image_scan_rect(im.uid)

    # ------------------------------------------------------------------ UX-02 setup
    def auto_find(self) -> int:
        """Auto-find scan area & scale bar on every image in the analyzer."""
        if self.state.session is None:
            return 0
        if self.state.is_loading():
            self._toast("Images are still loading",
                        "Auto-find starts once every image is loaded.", "info")
            return 0
        n = self.state.auto_setup()
        if not n and not self.state.is_setting_up():
            self._toast("Nothing to check", f"Add images to the {self._rec} first.", "info")
        return n

    def _on_setup_progress(self, done: int, total: int) -> None:
        self.setup_tile.set_progress(done, total)
        if total and done < total:
            self.progress_changed.emit(100.0 * done / total,
                                       f"Finding scan areas and scale bars — {done} of {total}")

    def _on_setup_finished(self, st: dict) -> None:
        self.setup_tile.set_progress(0, 0)
        self.progress_changed.emit(-1, "")
        self._refresh_setup_all()
        self._refresh_info_bar()
        n = int(st.get("total", 0))
        need = [im for im in self.state.images() if not self.state.setup_ready(im)
                and not im.loading and im.readable]
        parts = []
        if st.get("info_bar"):
            parts.append(f"info bar left out on {st['info_bar']}")
        if st.get("scale_meta"):
            parts.append(f"scale from metadata on {st['scale_meta']}")
        if st.get("scale_bar"):
            parts.append(f"scale from the scale bar on {st['scale_bar']}")
        if st.get("label_read"):                    # UPDATE 4 item 4
            parts.append(f"scale-bar label read on {st['label_read']}")
        if st.get("label_check"):
            k = int(st["label_check"])
            parts.append(f"{k} scale-bar reading{'s' if k != 1 else ''} to check")
        body = (("; ".join(parts) + ". ") if parts else "") + (
            f"{len(need)} image{'s' if len(need) != 1 else ''} still need a scale — enter the "
            "scale-bar length under the image or use Edit." if need else
            "Check each image before starting analysis.")
        self._toast(f"Checked {n} image{'s' if n != 1 else ''}", body,
                    "warning" if need else "success")
        if need and self.state.current_image() not in need:
            self.state.set_current_image(need[0].uid)
        self.setup_tile.draw_attention_to_length()      # UPDATE 4 item 6

    def _on_bar_length(self, um: float, same: bool) -> None:
        im = self.state.current_image()
        if im is None:
            return
        snap = self.state.set_bar_length(im.uid, um, same)
        if snap:
            n = len(snap)
            self._toast_action("Scale set", f"{im.bar_px:.0f} px = {um:g} µm → "
                               f"{im.bar_px / um:.4g} px/µm on {n} image{'s' if n != 1 else ''}.",
                               "success", "Undo", lambda: self.state.restore_scales(snap))

    def check_setup(self, images) -> bool:
        """UX-02 gate: True when every image may be analysed; otherwise
        select the first image that is not ready, show the tile and ask
        the shell to spotlight it with the gate text."""
        if self.state.is_loading() or any(im.loading for im in images):
            self._toast("Images are still loading",
                        "Analysis can start once every image is loaded.", "info")
            return False
        bad = [im for im in images if self.state.setup_issues(im)]
        if not bad:
            return True
        self.show_image_view()
        if self.state.current_image() not in bad:
            self.state.set_current_image(bad[0].uid)
        self.setup_tile.refresh(self.state, self.state.current_image())
        self.film.refresh_all()
        self.setup_required.emit(GATE_TITLE, GATE_TEXT)
        if not self.receivers(SIGNAL("setup_required(QString,QString)")):
            self._toast(GATE_TITLE, GATE_TEXT, "warning")
        return False

    # ------------------------------------------------------------------ UX-06 remove / put back
    def remove_images(self, uids) -> int:
        uids = list(uids)
        if self.queue.is_running():
            busy = set(self.queue.pending_uids()) | {self.queue.current_uid()}
            if busy & set(uids):
                self._toast("Analysis is running",
                            "Wait for it to finish (or Cancel) before removing images.", "info")
                return 0
        n = self.state.remove_images(uids)
        if n:
            self._toast_action(f"Removed {n} image{'s' if n != 1 else ''} from the analyzer",
                               f"The files stay in the {self._rec}. Use “Add back” in the image "
                               "list to return them.", "info", "Undo",
                               lambda u=uids: self.state.restore_images(uids=u))
        return n

    def restore_images(self, record_paths=None) -> int:
        n = self.state.restore_images(record_paths)
        if n:
            self._toast("Images added back", f"{n} image{'s' if n != 1 else ''} are in the "
                        "analyzer again.", "success")
        return n

    # ------------------------------------------------------------------ UX-09 loading
    def _on_records_loading(self, done: int, total: int) -> None:
        if self.queue.is_running() or not total:
            return
        if done < total:
            self.ring.set_tone("accent")
            self.ring.set_label(None)
            self.ring.set_value(100.0 * done / total, animate=False)
            self.ring.set_caption("loading")
            self.run_title.setText("Loading images…")
            self.run_sub.setText(f"{done} of {total} folders loaded · "
                                 f"{len(self.state.images())} images")
            self.progress_changed.emit(100.0 * done / total, f"Loading images — {done} of "
                                                             f"{total} folders")
            self.btn_all.setEnabled(False)
            self.btn_cur.setEnabled(False)
            self.btn_sel.setEnabled(False)

    def _on_records_loaded(self) -> None:
        self.btn_all.setEnabled(True)
        self.btn_cur.setEnabled(True)
        self.btn_sel.setEnabled(True)
        self.progress_changed.emit(-1, "")
        self._set_idle()
        self._refresh_setup_all()
        doc = self.state.session
        if doc is not None:
            self._toast("Images loaded", f"{len(doc.images)} images from "
                        f"{len(doc.records)} folders. Run Auto-find, check each image's scan "
                        "area and scale, then Analyze all.", "success")

    # ------------------------------------------------------------------ analysis
    def is_busy(self) -> bool:
        return self.queue.is_running()

    def busy_uids(self) -> set:
        """UPDATE 4 item 7: images queued or being analysed right now."""
        if not self.queue.is_running():
            return set()
        cur = self.queue.current_uid()
        return set(self.queue.pending_uids()) | ({cur} if cur is not None else set())

    def analyze_all(self) -> None:
        imgs = [im for im in self.state.images() if im.readable or im.loading]
        self._start(imgs, self.btn_all)

    def analyze_current(self) -> None:
        im = self.state.current_image()
        if im is not None and (im.readable or im.loading):
            self._start([im], self.btn_cur)

    def _on_checked_changed(self, uids=None) -> None:
        n = len(self.film.checked_uids())
        self.btn_sel.setText(f"Analyze selected ({n})")
        # never hide the button that is driving a run; re-checked when the run ends
        self.btn_sel.setVisible(n >= 2 or self._run_btn is self.btn_sel)

    def analyze_selected(self) -> None:
        """Run the ticked images through the same batch path as Analyze all."""
        ticked = set(self.film.checked_uids())
        imgs = [im for im in self.state.images()
                if im.uid in ticked and (im.readable or im.loading)]
        self._start(imgs, self.btn_sel)

    def _start(self, images, button: Optional[AnimatedButton] = None) -> None:
        if self.state.session is None or not images:
            self._toast("Nothing to analyse", f"Add images to the {self._rec} first.",
                        "warning")
            return
        if self.queue.is_running():
            self._toast("Analysis already running", "Wait for it to finish or press Cancel.", "info")
            return
        if not self.check_setup(images):
            return
        params = self.params.get_params()
        self.state.set_params(params)
        jobs = []
        for im in images:
            # the queue reads each image's pixels itself and drops them after
            jobs.append(AnalysisJob(im.uid, None if im.path else im.image_bgr,
                                    self.state.px_for(im), params, self.state.scan_for(im),
                                    path=im.path))
            self.state.set_image_status(im.uid, "queued")
        self._batch_total = len(jobs)
        self._batch_uids = [j.uid for j in jobs]
        self._device_notes = []
        # UX-08: only the button that was clicked shows the spinner
        self._run_btn = button or self.btn_all
        self._run_btn.set_loading(True)
        for b in (self.btn_all, self.btn_cur, self.btn_sel):
            if b is not self._run_btn:
                b.setEnabled(False)
        self.btn_cancel.show()
        self.ring.set_tone("accent")
        self.ring.set_label(None)
        self.ring.set_value(0, animate=False)
        self.run_title.setText(f"Analysing {len(jobs)} image{'s' if len(jobs) != 1 else ''}")
        self.busy_changed.emit(True)
        started = False
        try:
            started = bool(self.queue.start(jobs))
        finally:
            if not started and not self.queue.is_running():
                self._end_run_controls()       # never leave the edit lock on
        if not started:
            for im in images:
                self.state.set_image_status(im.uid, "done" if im.result is not None else "pending")
            self._toast("Analysis could not start",
                        "The previous analysis is still stopping. Try again in a moment.", "info")

    def running_button(self) -> Optional[AnimatedButton]:
        return self._run_btn if self.queue.is_running() else None

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
        self.run_sub.setText("Analysis stops right away.")

    def _on_job_progress(self, uid, pct: int, msg: str) -> None:
        self.state.set_image_status(uid, "running", pct, msg)
        im = self.state.session.image(uid) if self.state.session else None
        name = im.filename if im else ""
        i = self._batch_uids.index(uid) + 1 if uid in self._batch_uids else 0
        self.run_sub.setText(f"{i} of {self._batch_total} · {name}\n{msg}")

    def _on_job_finished(self, uid, raw) -> None:
        # UPDATE 4 item 10b: e.g. "The graphics card ran out of memory on this
        # image, so it was processed on the CPU instead. ..."
        note = str(getattr(raw, "ai_device_note", "") or "")
        if note:
            self._device_notes.append((uid, note))
            self.run_sub.setText(f"{self.run_sub.text().split(chr(10))[0]}\n{note}")
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

    def _end_run_controls(self) -> None:
        """Run buttons back to idle and the edit lock released -- the lock
        is released even if restoring a control raises (a run that ended,
        failed or was cancelled must never leave editing locked)."""
        try:
            for b in (self.btn_all, self.btn_cur, self.btn_sel):
                b.set_loading(False)
                b.setEnabled(True)
            self._run_btn = None
            self._on_checked_changed()
            self.btn_cancel.hide()
        finally:
            self.busy_changed.emit(False)

    def _on_queue_finished(self, cancelled: bool) -> None:
        self._end_run_controls()
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
                             f"{fmt_int(grains)} grains · saved automatically")
        self._report_device_notes()
        if self.toasts is not None:
            self.toasts.show_toast("Analysis complete",
                                   f"{len(ok)} image{'s' if len(ok) != 1 else ''} · "
                                   f"{fmt_int(grains)} grains"
                                   + (f" · {len(failed)} failed" if failed else ""),
                                   "warning" if failed else "success", "Review results",
                                   lambda: self.review_requested.emit())

    def _report_device_notes(self) -> None:
        """UPDATE 4 item 10b: images that could not stay on the graphics card
        (out of memory / GPU error) were finished on the CPU -- say so under
        the run card and in a toast; results are unaffected."""
        notes = self._device_notes
        if not notes:
            return
        n = len(notes)
        first = notes[0][1]
        self.run_sub.setText(f"{self.run_sub.text()}\n"
                             f"{n} image{'s' if n != 1 else ''} finished on the CPU: {first}")
        self._toast(f"{n} image{'s were' if n != 1 else ' was'} processed on the CPU",
                    first, "warning")
        # a GPU error marks the card unusable for this session: grey it out
        self.params.recheck_device()

    def _set_idle(self) -> None:
        if self.queue.is_running() or self.state.is_loading():
            return
        self.ring.set_tone("accent")
        self.ring.set_value(0, animate=False)
        self.ring.set_label("—")
        self.ring.set_caption("ready")
        imgs = self.state.images()
        n = len(imgs)
        done = sum(1 for im in imgs if im.result is not None)
        doc = self.state.session
        where = (f"from {len(doc.records)} folders" if doc is not None and doc.multi
                 else f"in this {self._rec}")
        self.run_title.setText("Ready to analyse" if n else "Add images to begin")
        self.run_sub.setText(f"{n} image{'s' if n != 1 else ''} {where}"
                             + (f" · {done} already analysed" if done else ""))

    def _toast(self, title, body="", sev="info") -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev)
