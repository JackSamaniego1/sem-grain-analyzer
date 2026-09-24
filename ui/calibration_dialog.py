"""
Calibration dialog (UI-11) - measure the scale bar on the image and enter
its printed length.  Three measurement modes (segmented control, remembered
between sessions): Rectangle, Level line (default) and Free line - see
``ui.canvas.calibration_canvas``.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ui.canvas.calibration_canvas import (  # noqa: F401  (re-exported)
    DEFAULT_MODE, MODE_LABELS, MODES, TILT_WARN_DEG, CalibrationCanvas,
    ZoomableCalibCanvas, _bgr_to_qpixmap,
)
from ui.widgets import Badge, SegmentedControl

LEVEL_IT_MAX_DEG = 5.0      # "Level it" is offered for small tilts only
_PREFS_FILE = "calibration_ui.json"

_INSTRUCTIONS = {
    "rect": "Drag a box over the scale bar — only its width counts. The left and right "
            "edges snap to the bar ends. Drag the handles to adjust, drag inside to move, "
            "arrow keys nudge 1 px (Shift: 10 px). Then enter the length printed on the label.",
    "level": "Click the LEFT end of the scale bar, then the RIGHT end — the line stays "
             "level. Drag either end left/right to adjust (a magnifier follows the cursor and "
             "the ends snap to the bar). Then enter the length printed on the label.",
    "free": "Click the two ends of the scale bar. Use this only when the bar is tilted in the "
            "image; drag the ends to adjust. Then enter the length printed on the label.",
}


def _prefs_path(path=None) -> Path:
    if path is not None:
        return Path(path)
    from data.settings import get_settings_dir
    return get_settings_dir() / _PREFS_FILE


def load_calibration_mode(path=None) -> str:
    """Last measurement mode the user picked (``DEFAULT_MODE`` if none)."""
    try:
        mode = json.loads(_prefs_path(path).read_text(encoding="utf-8")).get("mode")
    except Exception:
        return DEFAULT_MODE
    return mode if mode in MODES else DEFAULT_MODE


def save_calibration_mode(mode: str, path=None) -> None:
    """Remember the measurement mode (atomic write; never raises)."""
    if mode not in MODES:
        return
    p = _prefs_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".cal_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"mode": mode}, fh)
        os.replace(tmp, p)
    except Exception:
        pass


def suggest_bar_length_um(length_px: float, px_per_um: float) -> float | None:
    """Real length of a scale bar ``length_px`` long at ``px_per_um``,
    snapped to the "nice" value printed on SEM bars (1/2/2.5/5 × 10^n)
    when within 4 %, else rounded to 3 significant figures."""
    if not length_px or not px_per_um or length_px <= 0 or px_per_um <= 0:
        return None
    raw = float(length_px) / float(px_per_um)
    exp = math.floor(math.log10(raw))
    best = None
    for e in (exp - 1, exp, exp + 1):
        for m in (1.0, 2.0, 2.5, 5.0, 10.0):
            v = m * 10.0 ** e
            if best is None or abs(v - raw) < abs(best - raw):
                best = v
    if best is not None and abs(best - raw) / raw <= 0.04:
        return float(f"{best:.6g}")
    return float(f"{raw:.3g}")


def _to_um(length: float, unit: str) -> float:
    if unit == "nm":
        return length / 1000.0
    if unit == "mm":
        return length * 1000.0
    return length


class CalibrationDialog(QDialog):
    calibration_set = Signal(float)   # px_per_um

    def __init__(self, image_bgr: np.ndarray, auto_bar_px=None, parent=None,
                 mode: Optional[str] = None, prefs_path=None):
        super().__init__(parent)
        self.setWindowTitle("Set scale bar")
        self.setMinimumSize(900, 680)
        self.resize(1000, 740)
        self._image_bgr = image_bgr
        self._prefs_path = prefs_path
        self._px_distance: Optional[float] = None
        self.auto_bar = None
        start_mode = mode if mode in MODES else load_calibration_mode(prefs_path)
        self._build_ui(start_mode)
        if auto_bar_px is not None:
            self.prefill(auto_bar_px)

    # ------------------------------------------------------------------ build
    def _build_ui(self, mode: str):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # mode row --------------------------------------------------------
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        lbl_mode = QLabel("Measure with")
        lbl_mode.setProperty("tone", "secondary")
        mode_row.addWidget(lbl_mode)
        self.mode_seg = SegmentedControl([MODE_LABELS[m] for m in MODES], MODES.index(mode))
        self.mode_seg.setToolTip("Rectangle: box over the bar, width only.\n"
                                 "Level line: two clicks, kept horizontal (recommended).\n"
                                 "Free line: two clicks at any angle, for tilted images.")
        self.mode_seg.setAccessibleName("Measurement mode")
        self.mode_seg.current_changed.connect(self._on_mode_index)
        mode_row.addWidget(self.mode_seg)
        self.chk_snap = QCheckBox("Snap to bar ends")
        self.chk_snap.setChecked(True)
        self.chk_snap.setToolTip("Move the ends onto the detected ends of the scale bar. "
                                 "Hold Alt while dragging to place freely.")
        mode_row.addWidget(self.chk_snap)
        mode_row.addStretch()
        self.badge_snap = Badge("Snapped", "success", icon="check")
        self.badge_snap.setToolTip("Both ends sit on the detected ends of the scale bar.")
        self.badge_snap.hide()
        mode_row.addWidget(self.badge_snap)
        self.btn_undo_snap = QPushButton("Undo snap")
        self.btn_undo_snap.setToolTip("Put the ends back where you placed them.")
        self.btn_undo_snap.hide()
        mode_row.addWidget(self.btn_undo_snap)
        self.lbl_tilt = QLabel("")
        self.lbl_tilt.setToolTip(f"Angle of the line from horizontal. Above {TILT_WARN_DEG:g}° "
                                 "the measured length is probably too long.")
        self.lbl_tilt.hide()
        mode_row.addWidget(self.lbl_tilt)
        self.btn_level = QPushButton("Level it")
        self.btn_level.setToolTip("Make the line horizontal (keeps both x positions).")
        self.btn_level.hide()
        mode_row.addWidget(self.btn_level)
        lay.addLayout(mode_row)

        self.lbl_inst = QLabel(_INSTRUCTIONS[mode])
        self.lbl_inst.setProperty("tone", "secondary")
        self.lbl_inst.setWordWrap(True)
        lay.addWidget(self.lbl_inst)
        self.lbl_auto = QLabel("")
        self.lbl_auto.setProperty("tone", "accent")
        self.lbl_auto.setWordWrap(True)
        self.lbl_auto.hide()
        lay.addWidget(self.lbl_auto)

        # canvas ----------------------------------------------------------
        self.canvas = CalibrationCanvas(mode=mode)
        self.canvas.set_image(self._image_bgr)
        self.canvas.point_placed.connect(self._on_point_placed)
        self.canvas.snap_changed.connect(lambda _on: self._refresh_state())
        self.chk_snap.toggled.connect(self.canvas.set_snap_enabled)
        self.chk_snap.setVisible(mode != "free")
        self.btn_undo_snap.clicked.connect(self.canvas.undo_snap)
        self.btn_level.clicked.connect(self.canvas.level_line)
        lay.addWidget(self.canvas, 1)

        # measurement -----------------------------------------------------
        ctrl_group = QGroupBox("Measurement")
        ctrl_lay = QHBoxLayout(ctrl_group)
        ctrl_lay.setSpacing(12)
        self.lbl_dist = QLabel("Bar length:  — px")
        self.lbl_dist.setProperty("tone", "secondary")
        self.lbl_dist.setToolTip("Scale-bar length in image pixels, with its reading uncertainty.")
        ctrl_lay.addWidget(self.lbl_dist)
        ctrl_lay.addStretch()
        ctrl_lay.addWidget(QLabel("Real-world length:"))
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setRange(0.001, 100000.0)
        self.length_spin.setValue(50.0)
        self.length_spin.setDecimals(3)
        self.length_spin.setSingleStep(1.0)
        self.length_spin.setFixedWidth(110)
        self.length_spin.setToolTip("The length printed on the scale-bar label.")
        ctrl_lay.addWidget(self.length_spin)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["µm", "nm", "mm"])
        self.unit_combo.setFixedWidth(86)
        self.unit_combo.setToolTip("Unit printed on the label.")
        ctrl_lay.addWidget(self.unit_combo)
        self.lbl_result = QLabel("px/µm:  —")
        self.lbl_result.setProperty("role", "body_strong")
        self.lbl_result.setProperty("tone", "accent")
        self.lbl_result.setToolTip("Resulting calibration.")
        ctrl_lay.addWidget(self.lbl_result)
        self.length_spin.valueChanged.connect(self._update_result)
        self.unit_combo.currentIndexChanged.connect(self._update_result)
        lay.addWidget(ctrl_group)

        # buttons ---------------------------------------------------------
        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setToolTip("Clear the measurement and start again.")
        self.btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(self.btn_reset)
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        self.btn_apply = QPushButton("Apply to all images")
        self.btn_apply.setProperty("variant", "primary")
        self.btn_apply.setMinimumHeight(36)
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self.btn_apply)
        lay.addLayout(btn_row)
        self._refresh_state()

    # ------------------------------------------------------------------- mode
    def mode(self) -> str:
        return self.canvas.mode()

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.mode_seg.set_current_index(MODES.index(mode))   # -> _on_mode_index

    def _on_mode_index(self, idx: int) -> None:
        mode = MODES[idx]
        self.canvas.set_mode(mode)
        self.lbl_inst.setText(_INSTRUCTIONS[mode])
        self.chk_snap.setVisible(mode != "free")     # free line never snaps
        save_calibration_mode(mode, self._prefs_path)
        self._on_point_placed()

    # ---------------------------------------------------------------- prefill
    def prefill(self, bar: dict | None, length_um: float | None = None) -> bool:
        """Show an automatically found scale bar (``core.scale_bar.
        find_scale_bar_line`` result) as an editable box or level line and,
        when the image metadata gives the pixel size, fill in its real
        length.  The user only checks and presses Apply."""
        if not bar or not bar.get("rect"):
            return False
        x, y, w, h = (int(v) for v in bar["rect"])
        if w < 4:
            return False
        self.auto_bar = dict(bar)
        self.canvas.set_bar((x, y, w, h))
        shape = "box" if self.mode() == "rect" else "line ends"
        msg = (f"Scale bar found automatically ({w} px) — check the {shape}, "
               "then enter the length printed on the label.")
        if length_um and length_um > 0:
            if length_um < 1.0:
                self.unit_combo.setCurrentText("nm")
                self.length_spin.setValue(round(length_um * 1000.0, 3))
            else:
                self.unit_combo.setCurrentText("µm")
                self.length_spin.setValue(round(length_um, 3))
            msg = (f"Scale bar found automatically ({w} px); its length was filled in from "
                   "the image metadata — check it against the label, then Apply.")
        self.lbl_auto.setText(msg)
        self.lbl_auto.show()
        return True

    # ---------------------------------------------------------------- updates
    def _on_point_placed(self):
        d = self.canvas.pixel_distance()
        if d is not None:
            self._px_distance = d
            u = self.canvas.uncertainty_px() or 0.0
            self.lbl_dist.setText(f"Bar length:  {d:.1f} px  ± {u:.1f} px"
                                  f"  ({100.0 * u / d:.1f} %)")
            self.btn_apply.setEnabled(True)
            self._update_result()
        else:
            self._px_distance = None
            n = self.canvas.point_count()
            if self.mode() == "rect":
                self.lbl_dist.setText("Bar length:  drag a box over the scale bar")
            elif n:
                self.lbl_dist.setText(f"Point {n} placed — click point {n + 1}")
            else:
                self.lbl_dist.setText("Bar length:  — px")
            self.lbl_result.setText("px/µm:  —")
            self.btn_apply.setEnabled(False)
        self._refresh_state()

    def _refresh_state(self) -> None:
        c = self.canvas
        self.badge_snap.setVisible(c.is_snapped())
        self.btn_undo_snap.setVisible(c.can_undo_snap())
        tilt = c.tilt_deg() if c.mode() == "free" else None
        if tilt is None:
            self.lbl_tilt.hide()
            self.btn_level.hide()
            return
        warn = tilt > TILT_WARN_DEG
        self.lbl_tilt.setText(f"tilt {tilt:.1f}°" + ("  — line is not level" if warn else ""))
        tone = "warning" if warn else "secondary"
        if self.lbl_tilt.property("tone") != tone:
            self.lbl_tilt.setProperty("tone", tone)
            self.lbl_tilt.style().unpolish(self.lbl_tilt)
            self.lbl_tilt.style().polish(self.lbl_tilt)
        self.lbl_tilt.show()
        self.btn_level.setVisible(0.05 < tilt <= LEVEL_IT_MAX_DEG)

    def tilt_warning_shown(self) -> bool:
        """The free-line tilt warning is visible (tilt above TILT_WARN_DEG)."""
        return (not self.lbl_tilt.isHidden()) and self.lbl_tilt.property("tone") == "warning"

    def px_per_um(self) -> Optional[float]:
        """Calibration for the current measurement and entered length."""
        if self._px_distance is None:
            return None
        length_um = _to_um(self.length_spin.value(), self.unit_combo.currentText())
        return self._px_distance / length_um if length_um > 0 else None

    def _update_result(self):
        v = self.px_per_um()
        if v is not None:
            self.lbl_result.setText(f"px/µm:  {v:.4f}   ({1.0 / v:.5g} µm/px)")

    def _reset(self):
        self.canvas.reset_points()
        self._px_distance = None
        self.lbl_dist.setText("Bar length:  — px")
        self.lbl_result.setText("px/µm:  —")
        self.btn_apply.setEnabled(False)
        self._refresh_state()

    def _apply(self):
        if self._px_distance is None:
            QMessageBox.warning(self, "No measurement", "Measure the scale bar first.")
            return
        length_um = _to_um(self.length_spin.value(), self.unit_combo.currentText())
        if length_um <= 0:
            QMessageBox.warning(self, "Invalid", "Length must be > 0.")
            return
        self.calibration_set.emit(self._px_distance / length_um)
        self.accept()
