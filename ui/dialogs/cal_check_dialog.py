"""
Calibration check (INN-29) - OPTIONAL feature. Only reachable when
Settings ▸ Calibration verification is switched on (default off).

Layout spec
  regions : left: reference-image preview (detected period lines drawn over
            the image; in manual mode drag across N periods) · right: form
            (reference image, instrument, magnification, pixel size,
            standard [+ new standard inline], tolerance, method) · result
            block (measured pitch, certified pitch, error %, PASS/FAIL,
            low-confidence note) · footer Close / Save check.
  primary : Measure → Save check (appends to calibration/checks.jsonl and
            copies the image; never blocks analysis or export).
  states  : empty (no image: preview placeholder) · measuring (in-place
            progress bar; buttons disabled) · error (inline message, e.g.
            "no periodic pattern found" → suggests manual mode) ·
            measured (result + Save enabled) · saved (toast, dialog closes).

``CalStatusChip`` is the small "Cal ✔ 2 d ago" / "Cal due" / "Cal failed"
pill; it hides itself when the feature is off or no check was ever
recorded for the instrument (user requirement: no nags when unused).
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit,
    QProgressBar, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from core.cal_verify import manual_pitch_px, measure_pitch, period_lines
from data.cal_records import (
    DEFAULT_TOLERANCE_PCT, STANDARD_TYPES, CalibrationStandard, CalibrationStore, build_check,
    instrument_config,
)
from ui.design.tokens import SPACE
from ui.widgets import AnimatedButton, Badge, Card, Divider, KeyValueList, SegmentedControl, label
from ui.widgets._base import qcolor, tokens
from ui.workers import bgr_to_qimage, read_image, run_task

STATE_KIND = {"ok": "success", "due": "warning", "failed": "danger"}
IMAGE_FILTER = "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)"


def cal_store(state) -> CalibrationStore:
    return CalibrationStore(state.root, enabled=bool(
        getattr(state.settings, "calibration_verification_enabled", False)))


def known_instruments(state, store: Optional[CalibrationStore] = None) -> List[str]:
    """Instruments from Settings + every instrument that has a check."""
    out: List[str] = []
    for it in getattr(state.settings, "instruments", None) or []:
        n = str((it or {}).get("name", "")).strip() if isinstance(it, dict) else ""
        if n and n not in out:
            out.append(n)
    store = store or cal_store(state)
    try:
        checks = store.load_checks()
    except (OSError, ValueError):
        checks = []
    for c in checks:
        if c.instrument and c.instrument not in out:
            out.append(c.instrument)
    return out


def load_reference(path: str) -> dict:
    """Pool thread: the image + whatever the SEM metadata says."""
    from core.sem_metadata import read_sem_metadata
    bgr = read_image(path)
    if bgr is None:
        raise ValueError("The file could not be read as an image.")
    meta = read_sem_metadata(path, image_width=bgr.shape[1])
    return {"path": path, "bgr": bgr, "qimage": bgr_to_qimage(bgr), "meta": meta}


# ======================================================================
# status chip
# ======================================================================

class CalStatusChip(Badge):
    """Calibration status for one instrument; hidden when not in use."""

    def __init__(self, parent=None) -> None:
        super().__init__("", "neutral", dot=True, parent=parent)
        self.state = "off"
        self.hide()

    def refresh(self, store: CalibrationStore, instrument: str, instruments=None) -> None:
        st = {"state": "off", "text": ""}
        if instrument and store.checks_for(instrument):   # never a nag before a first check
            st = store.instrument_status(instrument, instruments=instruments)
        self.state = st["state"]
        if self.state == "off" or not st.get("text"):
            self.hide()
            return
        self.set_text(f"{instrument}: {st['text']}")
        self.set_kind(STATE_KIND.get(self.state, "neutral"))
        c = st.get("check")
        tip = {"ok": "Scale verified against the reference standard",
               "due": "The last calibration check is older than the check interval",
               "failed": "The latest calibration check failed"}.get(self.state, "")
        if c is not None:
            tip += (f"\nLast check {c.datetime[:10]}: error {c.error_pct:+.2f} % "
                    f"(limit ±{c.tolerance_pct:g} %), {c.standard_name or c.standard_id}")
        self.setToolTip(tip)
        self.show()


# ======================================================================
# preview
# ======================================================================

class GridPreview(QWidget):
    """Aspect-fit image with detected period lines; manual drag line."""

    line_drawn = Signal(object, object)       # (x0, y0), (x1, y1) in image px

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pix: Optional[QPixmap] = None
        self.lines: list = []
        self.manual = False
        self.p0 = self.p1 = None
        self._drag = False
        self.setMinimumSize(420, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("Reference image preview")
        self.setToolTip("Reference image. Detected period lines are drawn in the accent "
                        "colour; in manual mode drag across a whole number of periods.")

    def set_image(self, qimage) -> None:
        self.pix = QPixmap.fromImage(qimage) if qimage is not None else None
        self.lines, self.p0, self.p1 = [], None, None
        self.update()

    def set_lines(self, lines) -> None:
        self.lines = list(lines or [])
        self.update()

    def set_manual(self, on: bool) -> None:
        self.manual = on
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.update()

    def _geom(self):
        if self.pix is None or self.pix.isNull():
            return None
        w, h = self.pix.width(), self.pix.height()
        s = min(self.width() / w, self.height() / h)
        ox, oy = (self.width() - w * s) / 2, (self.height() - h * s) / 2
        return s, ox, oy

    def to_image(self, pos) -> Optional[tuple]:
        g = self._geom()
        if g is None:
            return None
        s, ox, oy = g
        x = min(max((pos.x() - ox) / s, 0.0), self.pix.width() - 1.0)
        y = min(max((pos.y() - oy) / s, 0.0), self.pix.height() - 1.0)
        return (x, y)

    def mousePressEvent(self, e) -> None:
        if self.manual and e.button() == Qt.LeftButton and self.pix is not None:
            self.p0 = self.p1 = self.to_image(e.position())
            self._drag = True
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag:
            self.p1 = self.to_image(e.position())
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag:
            self._drag = False
            self.p1 = self.to_image(e.position())
            self.update()
            if self.p0 and self.p1 and math.dist(self.p0, self.p1) > 2:
                self.line_drawn.emit(self.p0, self.p1)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(t.surface.surface2))
        g = self._geom()
        if g is None:
            p.setPen(qcolor(t.text.secondary))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Load an image of the reference grating or grid")
            return
        s, ox, oy = g
        p.drawPixmap(QRectF(ox, oy, self.pix.width() * s, self.pix.height() * s), self.pix,
                     QRectF(self.pix.rect()))
        if self.lines and not self.manual:
            c = QColor(qcolor(t.accent.text))
            c.setAlphaF(0.85)
            p.setPen(QPen(c, 1.0))
            for (x0, y0), (x1, y1) in self.lines:
                p.drawLine(QPointF(ox + x0 * s, oy + y0 * s), QPointF(ox + x1 * s, oy + y1 * s))
        if self.manual and self.p0 and self.p1:
            c = qcolor(t.warning.solid)
            p.setPen(QPen(c, 2.0))
            a = QPointF(ox + self.p0[0] * s, oy + self.p0[1] * s)
            b = QPointF(ox + self.p1[0] * s, oy + self.p1[1] * s)
            p.drawLine(a, b)
            p.setBrush(c)
            for q in (a, b):
                p.drawEllipse(q, 4, 4)


# ======================================================================
# dialog
# ======================================================================

class CalCheckDialog(QDialog):
    """Measure a reference standard and record the check."""

    saved = Signal(object)                    # CalibrationCheck

    def __init__(self, state, toasts=None, parent=None, instrument: str = "") -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        self.store = cal_store(state)
        self.ref: Optional[dict] = None
        self.pitch = None                     # core.cal_verify.PitchResult (auto)
        self.check = None                     # CalibrationCheck (unsaved)
        self._busy = False
        self.setWindowTitle("Check calibration")
        self.setMinimumSize(1040, 760)

        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        v.addWidget(label("Check calibration", "h2"))
        hint = label("Image your certified reference grating or grid at the magnification you "
                     "use, load it here and compare the measured pitch with the certificate. "
                     "The check is recorded for traceability; it never blocks analysis.",
                     tone="secondary")
        hint.setWordWrap(True)
        v.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(SPACE.lg)
        self.preview = GridPreview()
        self.preview.line_drawn.connect(self._manual_line)
        body.addWidget(self.preview, 3)

        side = QVBoxLayout()
        side.setSpacing(SPACE.sm)
        form = QGridLayout()
        form.setHorizontalSpacing(SPACE.md)
        form.setVerticalSpacing(SPACE.sm)
        r = 0
        self.btn_load = AnimatedButton("Load image…", "open", "secondary")
        self.btn_load.setToolTip("Open the image of the reference standard")
        self.btn_load.clicked.connect(self.choose_image)
        self.file_lbl = label("No image loaded", "caption")
        self.file_lbl.setWordWrap(True)
        lrow = QHBoxLayout()
        lrow.addWidget(self.btn_load)
        lrow.addWidget(self.file_lbl, 1)
        side.addLayout(lrow)
        self.instrument = QComboBox()
        self.instrument.setEditable(True)
        self.instrument.setAccessibleName("Instrument")
        self.instrument.setToolTip("Microscope the reference image was taken on")
        for n in known_instruments(state, self.store):
            self.instrument.addItem(n)
        if instrument:
            self.instrument.setCurrentText(instrument)
        self.instrument.currentTextChanged.connect(self._instrument_changed)
        form.addWidget(label("Instrument", tone="secondary"), r, 0)
        form.addWidget(self.instrument, r, 1)
        r += 1
        self.mag = QLineEdit()
        self.mag.setPlaceholderText("e.g. 1000")
        self.mag.setAccessibleName("Magnification")
        self.mag.setToolTip("Magnification of the reference image (×). Checks apply to "
                            "sessions at the same magnification.")
        form.addWidget(label("Magnification ×", tone="secondary"), r, 0)
        form.addWidget(self.mag, r, 1)
        r += 1
        self.ppu = QDoubleSpinBox()
        self.ppu.setRange(0.0, 100000.0)
        self.ppu.setDecimals(4)
        self.ppu.setSuffix(" px/µm")
        self.ppu.setAccessibleName("Pixel scale")
        self.ppu.setToolTip("Image scale being verified (from the image metadata when "
                            "available)")
        self.ppu.valueChanged.connect(lambda _v: self._reevaluate())
        self.ppu_src = label("", "caption")
        form.addWidget(label("Scale", tone="secondary"), r, 0)
        form.addWidget(self.ppu, r, 1)
        r += 1
        form.addWidget(self.ppu_src, r, 1)
        r += 1
        srow = QHBoxLayout()
        self.standard = QComboBox()
        self.standard.setAccessibleName("Reference standard")
        self.standard.setToolTip("Certified reference standard in the image")
        self.standard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.standard.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.standard.setMinimumContentsLength(12)
        self.standard.currentIndexChanged.connect(lambda _i: self._reevaluate())
        self.btn_new_std = AnimatedButton("New…", "add", "ghost")
        self.btn_new_std.setToolTip("Add a reference standard from its certificate")
        self.btn_new_std.clicked.connect(lambda: self.std_form.setVisible(
            not self.std_form.isVisible()))
        srow.addWidget(self.standard, 1)
        srow.addWidget(self.btn_new_std)
        form.addWidget(label("Standard", tone="secondary"), r, 0)
        form.addLayout(srow, r, 1)
        r += 1
        self.std_form = self._build_std_form()
        self.std_form.hide()
        form.addWidget(self.std_form, r, 0, 1, 2)
        r += 1
        form2 = form
        self.tol = QDoubleSpinBox()
        self.tol.setRange(0.1, 20.0)
        self.tol.setSingleStep(0.5)
        self.tol.setDecimals(2)
        self.tol.setPrefix("± ")
        self.tol.setSuffix(" %")
        self.tol.setValue(DEFAULT_TOLERANCE_PCT)
        self.tol.setAccessibleName("Tolerance")
        self.tol.setToolTip("Largest acceptable scale error for this instrument")
        self.tol.valueChanged.connect(lambda _v: self._reevaluate())
        form2.addWidget(label("Tolerance", tone="secondary"), r, 0)
        form2.addWidget(self.tol, r, 1)
        self.method = SegmentedControl(["Automatic", "Manual"], 0)
        self.method.setToolTip("Automatic: Fourier analysis of the whole image. Manual: drag "
                               "across a whole number of periods.")
        self.method.current_changed.connect(self._method_changed)
        form2.addWidget(label("Method", tone="secondary"), r + 1, 0)
        form2.addWidget(self.method, r + 1, 1, Qt.AlignLeft)
        self.n_periods = QSpinBox()
        self.n_periods.setRange(1, 1000)
        self.n_periods.setValue(10)
        self.n_periods.setSuffix(" periods")
        self.n_periods.setAccessibleName("Periods spanned by the manual line")
        self.n_periods.setToolTip("How many periods your drawn line spans")
        self.n_periods.valueChanged.connect(lambda _v: self._manual_line(self.preview.p0,
                                                                         self.preview.p1))
        self.n_lbl = label("Line spans", tone="secondary")
        form2.addWidget(self.n_lbl, r + 2, 0)
        form2.addWidget(self.n_periods, r + 2, 1)
        form.setColumnStretch(1, 1)
        side.addLayout(form)
        self.btn_measure = AnimatedButton("Measure pitch", "target", "secondary")
        self.btn_measure.setToolTip("Measure the pitch of the pattern automatically")
        self.btn_measure.clicked.connect(self.measure)
        side.addWidget(self.btn_measure, 0, Qt.AlignLeft)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setAccessibleName("Measuring")
        self.progress.hide()
        side.addWidget(self.progress)

        # result
        self.result_card = Card("Result")
        self.verdict = Badge("", "neutral", dot=True)
        self.verdict.hide()
        self.result_card.add_action(self.verdict)
        self.kv = KeyValueList()
        self.result_card.add_widget(self.kv)
        self.note = label("", "caption")
        self.note.setWordWrap(True)
        self.result_card.add_widget(self.note)
        side.addWidget(self.result_card)
        self.error = label("", tone="danger")
        self.error.setWordWrap(True)
        self.error.hide()
        side.addWidget(self.error)
        side.addStretch(1)
        sw = QWidget()
        sw.setLayout(side)
        sw.setMinimumWidth(380)
        sw.setMaximumWidth(460)
        body.addWidget(sw, 2)
        v.addLayout(body, 1)

        v.addWidget(Divider())
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.btn_close = AnimatedButton("Close", None, "ghost")
        self.btn_close.setToolTip("Close without saving (Esc)")
        self.btn_close.clicked.connect(self.reject)
        self.btn_save = AnimatedButton("Save check", "save", "primary")
        self.btn_save.setToolTip("Record this check (with a copy of the image) in the "
                                 "workspace's calibration log")
        self.btn_save.clicked.connect(self.save)
        foot.addWidget(self.btn_close)
        foot.addWidget(self.btn_save)
        v.addLayout(foot)

        self._reload_standards()
        self._instrument_changed(self.instrument.currentText())
        self._method_changed(0)
        self._show_result()

    # ------------------------------------------------------------------ standards
    def _build_std_form(self) -> QWidget:
        w = Card("New reference standard", "From the calibration certificate")
        g = QGridLayout()
        g.setHorizontalSpacing(SPACE.sm)
        g.setVerticalSpacing(SPACE.xs)
        self.s_name = QLineEdit()
        self.s_name.setPlaceholderText("e.g. Grating 2160 l/mm")
        self.s_name.setAccessibleName("Standard name")
        self.s_type = QComboBox()
        self.s_type.addItems(list(STANDARD_TYPES))
        self.s_type.setAccessibleName("Standard type")
        self.s_pitch = QDoubleSpinBox()
        self.s_pitch.setRange(0.0, 100000.0)
        self.s_pitch.setDecimals(4)
        self.s_pitch.setSuffix(" µm")
        self.s_pitch.setAccessibleName("Certified pitch")
        self.s_u = QDoubleSpinBox()
        self.s_u.setRange(0.0, 1000.0)
        self.s_u.setDecimals(4)
        self.s_u.setSuffix(" µm")
        self.s_u.setAccessibleName("Expanded uncertainty")
        self.s_cert = QLineEdit()
        self.s_cert.setPlaceholderText("Certificate / serial no.")
        self.s_cert.setAccessibleName("Certificate number")
        self.s_exp = QLineEdit()
        self.s_exp.setPlaceholderText("YYYY-MM-DD")
        self.s_exp.setAccessibleName("Certificate expiry date")
        rows = [("Name", self.s_name, "Name of the standard"),
                ("Type", self.s_type, "Pattern of the standard"),
                ("Certified pitch", self.s_pitch, "Pitch stated on the certificate"),
                ("Uncertainty U", self.s_u, "Expanded uncertainty (k = 2) of the pitch"),
                ("Certificate no.", self.s_cert, "Printed in reports as 'standard SN …'"),
                ("Expires", self.s_exp, "Certificate expiry date (optional)")]
        for i, (t, wdg, tip) in enumerate(rows):
            wdg.setToolTip(tip)
            g.addWidget(label(t, tone="secondary"), i, 0)
            g.addWidget(wdg, i, 1)
        g.setColumnStretch(1, 1)
        w.body_layout().addLayout(g)
        self.s_error = label("", "caption", "danger")
        self.s_error.hide()
        w.add_widget(self.s_error)
        b = AnimatedButton("Add standard", "add", "secondary")
        b.setToolTip("Save this standard to the workspace")
        b.clicked.connect(self.add_standard)
        w.add_widget(b)
        return w

    def _reload_standards(self, select_id: str = "") -> None:
        self.standard.blockSignals(True)
        self.standard.clear()
        for s in self.store.load_standards():
            txt = f"{s.name} — {s.certified_pitch_um:g} µm"
            if s.is_expired():
                txt += "  (certificate expired)"
            self.standard.addItem(txt, s.id)
        if select_id:
            self.standard.setCurrentIndex(max(0, self.standard.findData(select_id)))
        self.standard.blockSignals(False)
        if self.standard.count() == 0:
            self.standard.setPlaceholderText("No standards yet — add one")
            self.std_form.show()

    def current_standard(self) -> Optional[CalibrationStandard]:
        sid = self.standard.currentData()
        return self.store.get_standard(sid) if sid else None

    def add_standard(self) -> Optional[CalibrationStandard]:
        name = self.s_name.text().strip()
        if not name or self.s_pitch.value() <= 0:
            self.s_error.setText("Enter a name and the certified pitch.")
            self.s_error.show()
            return None
        exp = self.s_exp.text().strip()
        if exp:
            from datetime import date
            try:
                date.fromisoformat(exp)
            except ValueError:
                self.s_error.setText("Expiry date must look like 2027-03-31.")
                self.s_error.show()
                return None
        std = CalibrationStandard(id="std-" + uuid.uuid4().hex[:8], name=name,
                                  type=self.s_type.currentText(),
                                  certified_pitch_um=self.s_pitch.value(),
                                  expanded_uncertainty_um=self.s_u.value(),
                                  certificate_no=self.s_cert.text().strip(), cert_expiry=exp)
        try:
            std = self.store.add_standard(std)
        except (OSError, ValueError) as e:
            self.s_error.setText(f"Could not save the standard: {e}")
            self.s_error.show()
            return None
        self.s_error.hide()
        self.std_form.hide()
        for w in (self.s_name, self.s_cert, self.s_exp):
            w.clear()
        self._reload_standards(std.id)
        self._reevaluate()
        return std

    # ------------------------------------------------------------------ inputs
    def _instrument_changed(self, name: str) -> None:
        cfg = instrument_config(getattr(self.state.settings, "instruments", None), name)
        self.tol.setValue(cfg.tolerance_pct)

    def _method_changed(self, idx: int) -> None:
        manual = idx == 1
        self.preview.set_manual(manual)
        self.n_periods.setVisible(manual)
        self.n_lbl.setVisible(manual)
        self.btn_measure.setVisible(not manual)
        self.pitch = None
        self.check = None
        self.preview.set_lines([])
        if manual:
            self.note.setText("Drag across the image from one line to another, spanning a "
                              "whole number of periods, then set that number.")
        self._show_result()

    def choose_image(self) -> None:
        start = self.state.settings.last_export_dir or str(self.state.root)
        path, _ = QFileDialog.getOpenFileName(self, "Open the reference-standard image", start,
                                              IMAGE_FILTER)
        if path:
            self.load_image(path)

    def load_image(self, path: str) -> None:
        self._set_busy(True)
        self.error.hide()
        run_task(load_reference, str(path), on_done=self._on_loaded,
                 on_error=lambda m: self._fail(m.splitlines()[0]))

    def _on_loaded(self, ref: dict) -> None:
        self._set_busy(False)
        self.ref = ref
        self.pitch = self.check = None
        self.preview.set_image(ref["qimage"])
        self.file_lbl.setText(Path(ref["path"]).name)
        self.file_lbl.setToolTip(ref["path"])
        m = ref.get("meta")
        if m is not None and m.px_per_um:
            self.ppu.setValue(float(m.px_per_um))
            self.ppu_src.setText(f"From image metadata ({m.vendor})")
        else:
            self.ppu_src.setText("Not in the image metadata — enter the scale")
        if m is not None and m.magnification and not self.mag.text().strip():
            self.mag.setText(f"{m.magnification:g}")
        if m is not None and m.instrument and not self.instrument.currentText().strip():
            self.instrument.setCurrentText(m.instrument)
        self._show_result()

    # ------------------------------------------------------------------ measuring
    def measure(self) -> None:
        if self.ref is None:
            self._fail("Load an image of the reference standard first.")
            return
        ppu = self.ppu.value()
        std = self.current_standard()
        pattern = std.type if std is not None else "auto"
        self._set_busy(True)
        self.error.hide()
        run_task(measure_pitch, self.ref["bgr"], px_per_um=ppu or None, pattern=pattern,
                 on_done=self._on_measured, on_error=self._on_measure_failed)

    def _on_measure_failed(self, msg: str) -> None:
        first = msg.splitlines()[0].split(": ", 1)[-1]
        self._fail(f"Automatic measurement failed: {first}. Switch to Manual and drag across "
                   "a known number of periods.")

    def _on_measured(self, res) -> None:
        self._set_busy(False)
        self.pitch = res
        shape = self.ref["bgr"].shape[:2]
        lines = []
        for ax in res.axes:
            lines += period_lines(ax, shape)
        self.preview.set_lines(lines)
        self._reevaluate()

    def _manual_line(self, p0, p1) -> None:
        if self.method.current_index() != 1 or not p0 or not p1:
            return
        self._reevaluate()

    def measured_pitch_um(self) -> Optional[tuple]:
        """(pitch µm, method, confidence, repeatability µm, span px) or None."""
        ppu = self.ppu.value()
        if ppu <= 0:
            return None
        if self.method.current_index() == 1:
            p0, p1 = self.preview.p0, self.preview.p1
            if not p0 or not p1 or math.dist(p0, p1) <= 2:
                return None
            period = manual_pitch_px(p0, p1, self.n_periods.value())
            return period / ppu, "manual", "high", 0.0, math.dist(p0, p1)
        if self.pitch is None:
            return None
        return (self.pitch.period_px / ppu, "fft", self.pitch.confidence,
                (self.pitch.repeatability_px or 0.0) / ppu, self.pitch.span_px)

    def _reevaluate(self) -> None:
        self.check = None
        std = self.current_standard()
        m = self.measured_pitch_um()
        if std is not None and m is not None:
            pitch, method, conf, rep, span = m
            self.check = build_check(
                standard=std, instrument=self.instrument.currentText().strip(),
                magnification=self.mag.text().strip(), measured_pitch_um=pitch,
                tolerance_pct=self.tol.value(), method=method, px_per_um=self.ppu.value(),
                confidence=conf, repeatability_um=rep, span_px=span,
                operator=self.state.operator())
        self._show_result()

    def _show_result(self) -> None:
        c = self.check
        m = self.measured_pitch_um()
        std = self.current_standard()
        rows = [("Measured pitch", f"{m[0]:.4f} µm" if m else "—"),
                ("Certified pitch", f"{std.certified_pitch_um:g} µm" if std else "—"),
                ("Error", f"{c.error_pct:+.2f} %" if c else "—"),
                ("Tolerance", f"± {self.tol.value():g} %")]
        if c is not None and c.expanded_uncertainty_um:
            rows.append(("Uncertainty U (k = 2)", f"{c.expanded_uncertainty_um:.4f} µm"))
        self.kv.set_items(rows)
        if c is None:
            self.verdict.hide()
            if self.method.current_index() == 0:
                self.note.setText("" if self.ref is not None else
                                  "Load an image, choose the standard, then measure.")
            if std is None and m is not None:
                self.note.setText("Choose or add the reference standard to compare.")
        else:
            self.verdict.set_text("PASS" if c.passed else "FAIL")
            self.verdict.set_kind("success" if c.passed else "danger")
            self.verdict.setAccessibleName(f"Calibration check {'passed' if c.passed else 'failed'}")
            self.verdict.show()
            notes = []
            if self.pitch is not None and c.method == "fft":
                notes.append(f"{self.pitch.pattern.capitalize()} · period "
                             f"{self.pitch.period_px:.3f} px")
                if self.pitch.low_confidence:
                    notes.append("Low confidence — confirm with the manual method")
            if std is not None and std.is_expired():
                notes.append("Standard certificate expired — the check is saved but flagged")
            if not self.instrument.currentText().strip():
                notes.append("Enter the instrument so sessions can cite this check")
            self.note.setText(" · ".join(notes))
        self.btn_save.setEnabled(c is not None and not self._busy
                                 and bool(self.instrument.currentText().strip()))

    # ------------------------------------------------------------------ saving
    def save(self) -> None:
        self._reevaluate()
        c = self.check
        if c is None:
            return
        if not c.instrument:
            self._fail("Enter the instrument.")
            return
        img = self.ref["path"] if self.ref else None
        store = self.store
        self._set_busy(True)
        self.btn_save.set_loading(True)

        def done(saved):
            self._set_busy(False)
            self.btn_save.set_loading(False)
            self._remember_instrument(saved.instrument)
            if self.toasts is not None:
                self.toasts.show_toast(
                    "Calibration check saved",
                    f"{saved.instrument}: error {saved.error_pct:+.2f} % — "
                    f"{'PASS' if saved.passed else 'FAIL'}.",
                    "success" if saved.passed else "warning")
            self.saved.emit(saved)
            self.accept()

        def failed(msg):
            self.btn_save.set_loading(False)
            self._fail(f"Could not save the check: {msg.splitlines()[0]}")

        run_task(store.record_check, c, img, on_done=done, on_error=failed)

    def _remember_instrument(self, name: str) -> None:
        s = self.state.settings
        insts = list(getattr(s, "instruments", None) or [])
        names = [str((i or {}).get("name", "")).lower() for i in insts if isinstance(i, dict)]
        tol = self.tol.value()
        if name.lower() in names:
            for i in insts:
                if isinstance(i, dict) and str(i.get("name", "")).lower() == name.lower():
                    i["tolerance_pct"] = tol
        else:
            insts.append({"name": name, "tolerance_pct": tol, "check_interval_days": 7})
        s.instruments = insts
        self.state.save_settings()

    # ------------------------------------------------------------------ helpers
    def _set_busy(self, on: bool) -> None:
        self._busy = on
        self.progress.setVisible(on)
        for b in (self.btn_load, self.btn_measure):
            b.setEnabled(not on)
        self.btn_save.setEnabled(not on and self.check is not None)

    def _fail(self, msg: str) -> None:
        self._set_busy(False)
        self.error.setText(msg)
        self.error.show()


__all__ = ["CalCheckDialog", "CalStatusChip", "GridPreview", "cal_store", "known_instruments",
           "load_reference"]
