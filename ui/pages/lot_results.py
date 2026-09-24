"""
Lot result card + field table (INN-27) for the Projects page.

Layout spec
  regions : "Lot result" card (header: scope picker + status chip; hero
            "G 7.40 ± 0.39"; four figures: 95 % CI range, %RA, fields
            n / required with a progress bar, mean ECD ± CI) · inline
            exclusion-reason bar · field table (Include, image, session,
            G, grains, valid area, note).
  primary : read the lot result; secondary: include / exclude a field.
  states  : loading (skeleton) · empty (no analysed images) · error
            (message) · populated.  Excluding a field needs a reason
            (inline, non-blocking) and is written to the session's audit log.

Numbers come from ``data.catalog.fields_for_lot`` (with
:func:`refilter_saved_field` so grain filters / manual grain removals are
honoured) and ``core.metrics.sample_statistics``.  Loading runs on the
thread pool; changing scope recomputes on the GUI thread (pure numpy, tiny).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QProgressBar,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.metrics import FieldResult, SampleStatistics, sample_statistics
from data.catalog import Catalog
from data.models import read_json
from ui.design.tokens import MOTION, SPACE
from ui.widgets import AnimatedButton, Badge, Card, Divider, Skeleton, label
from ui.widgets._base import animate_value, lerp_color, qcolor, repolish, stop, tokens
from ui.workers import load_thumb_file

# INN-02 verdict badge (only when a spec applies to the lot)
VERDICT_KIND = {"pass": "success", "fail": "danger", "inconclusive": "warning"}
VERDICT_TEXT = {"pass": "PASS", "fail": "FAIL", "inconclusive": "INCONCLUSIVE"}

STATUS_KIND = {"green": "success", "amber": "warning", "grey": "neutral"}
FIELD_ROLE = Qt.UserRole + 11
_COLS = ["Include", "Image", "Session", "ASTM G", "Grains", "Valid area", "Note"]


# ======================================================================
# worker-thread helpers
# ======================================================================

def refilter_saved_field(session_dir, manifest: dict, img: dict):
    """Re-apply an image's saved grain filters + manual grain removals to its
    saved result, the way the Review page does on load (``ui.app_state``):
    rebuild the raw grain list from the full label image, then run
    ``ui.filtering.filter_image`` with the manifest's options.  Returns the
    edited ``AnalysisResult`` or None when the arrays/image are missing."""
    from data.session_io import load_session
    from ui.app_state import params_from_dict
    from ui.filtering import default_options, filter_image, options_from_dict, remeasure_excluded
    from ui.workers import read_image

    ls = load_session(session_dir)
    si = ls.image_by_filename(img.get("filename", ""))
    if si is None:
        return None
    res = si.result
    if res is None or res.label_image is None or not si.path.exists():
        return None
    bgr = read_image(si.path)
    if bgr is None or res.label_image.shape[:2] != bgr.shape[:2]:
        return None
    kept = {int(g.grain_id) for g in res.grains}
    ids = {int(i) for i in np.unique(res.label_image).tolist()} - {0} - kept
    raw = remeasure_excluded(res, ids) if ids else res
    m = ls.manifest
    ov = si.entry.filters_override
    opts = (options_from_dict(ov) if ov else
            options_from_dict(m.filters) if m.filters else default_options(m.scan_rect))
    params = params_from_dict({k: v for k, v in (m.detection_params or {}).items()
                               if k != "post_filters"})
    manual = frozenset(int(i) for i in (si.entry.manual_excluded or []))
    return filter_image(raw, bgr, opts, manual, params)["result"]


def load_lot_result(root: str, lot_path: str) -> dict:
    """Thread-pool: every field of the lot (edits honoured), the lot's
    sessions for the scope picker, and the field thumbnails (QImage)."""
    lot = Path(lot_path)
    fields = Catalog(root).fields_for_lot(lot, refilter=refilter_saved_field)
    sessions: List[tuple] = []
    seen = set()
    for f in fields:
        if f.session_id in seen:
            continue
        seen.add(f.session_id)
        try:
            m = read_json(Path(f.session_path) / "manifest.json")
        except (OSError, ValueError):
            m = {}
        name = m.get("label") or (lot.name if Path(f.session_path) == lot else
                                  Path(f.session_path).name)
        sessions.append((f.session_id, name))
    thumbs = {f.field_id: load_thumb_file(f.thumb_path) for f in fields if f.thumb_path}
    return {"lot": lot, "fields": fields, "sessions": sessions, "thumbs": thumbs,
            "spec": lot_spec(lot)}


def lot_spec(lot_path):
    """The INN-02 spec that applies to the lot (sample override beats the
    project spec), or None -- the common case: spec limits are optional."""
    from data.catalog import _project_meta_for_lot, _sample_id_for_lot
    from data.specs import select_spec, specs_from_project_dict
    lot = Path(lot_path)
    specs = specs_from_project_dict(_project_meta_for_lot(lot))
    return select_spec(specs, _sample_id_for_lot(lot)) if specs else None


def verdict_tooltip(v) -> str:
    """Hover text: spec, each rule result, decision rule statement."""
    head = v.spec_name + (f" ({v.spec_revision})" if v.spec_revision else "")
    lines = [f"{VERDICT_TEXT.get(v.overall, v.overall.upper())} against {head}"
             if head else VERDICT_TEXT.get(v.overall, v.overall.upper())]
    lines += [r.text for r in v.rules if r.text]
    if v.statement:
        lines.append(v.statement)
    return "\n".join(lines)


def stats_config(settings, scope_sid: Optional[str] = None) -> dict:
    cfg = {"required_fields": int(getattr(settings, "required_fields", 5) or 5),
           "target_RA_pct": float(getattr(settings, "target_RA_pct", 10.0) or 10.0)}
    if scope_sid:
        cfg.update(scope="session", session_id=scope_sid)
    return cfg


def status_text(st: SampleStatistics) -> str:
    s = st.status or ""
    return s[:1].upper() + s[1:] if s else "—"


def _num(v: Optional[float], dec: int = 2) -> str:
    return "—" if v is None else f"{v:.{dec}f}"


# ======================================================================
# widgets
# ======================================================================

class _Figure(QWidget):
    """One labelled figure of the card: OVERLINE / value / caption."""

    def __init__(self, title: str, tip: str, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.title = label(title.upper(), "overline")
        self.value = label("—", "h3")
        self.caption = label("", "caption")
        self.caption.setWordWrap(True)
        v.addWidget(self.title)
        v.addWidget(self.value)
        v.addWidget(self.caption)
        v.addStretch(1)
        self.setToolTip(tip)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set(self, value: str, caption: str = "", tone: Optional[str] = None) -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        if self.value.property("tone") != tone:
            self.value.setProperty("tone", tone)
            repolish(self.value)
        self.setAccessibleName(f"{self.title.text()}: {value}. {caption}")


class VerdictBadge(Badge):
    """PASS / FAIL / INCONCLUSIVE pill; colour change cross-fades (200 ms)."""

    def __init__(self, parent=None) -> None:
        super().__init__("", "neutral", dot=True, parent=parent)
        self._from = None
        self._t = 1.0
        self._anim = None

    def set_verdict_kind(self, kind: str, animate: bool = True) -> None:
        if kind == self._kind:
            return
        self._from = self._colors() if (animate and self.isVisible()) else None
        self.set_kind(kind)
        stop(self._anim)
        if self._from is not None:
            self._t = 0.0
            self._anim = animate_value(self, 0.0, 1.0, MOTION.base, self._set_t)
        else:
            self._t = 1.0

    def _set_t(self, t) -> None:
        self._t = float(t)
        self.update()

    def _colors(self):
        to = super()._colors()
        if self._from is None or self._t >= 1.0:
            return to
        return tuple(lerp_color(a, b, self._t) for a, b in zip(self._from, to))


class LotResultCard(Card):
    """The lot's one-line answer: mean G ± 95 % CI, %RA, fields, ECD, status."""

    scope_changed = Signal(object)          # session_id or None (whole lot)

    def __init__(self, parent=None) -> None:
        super().__init__("Lot result", "ASTM E112 field statistics · 95 % confidence (Student t)",
                         elevation=1, parent=parent)
        self.stats: Optional[SampleStatistics] = None
        self._g_anim = None
        self.scope = QComboBox()
        self.scope.setToolTip("Which fields count: every session of the lot, or one session")
        self.scope.setAccessibleName("Statistics scope")
        self.scope.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.scope.currentIndexChanged.connect(
            lambda _i: self.scope_changed.emit(self.scope.currentData()))
        self.add_action(self.scope)
        self.chip = Badge("—", "neutral", dot=True)
        self.chip.setToolTip("Green: enough fields and %RA within target. Amber: image more "
                             "fields. Grey: the images are not calibrated.")
        self.add_action(self.chip)
        self.verdict = None
        self.verdict_badge = VerdictBadge()
        self.verdict_badge.hide()          # optional feature: absent without a spec
        self.add_action(self.verdict_badge)

        # hero
        hero = QHBoxLayout()
        hero.setSpacing(SPACE.sm)
        self.g_value = label("—", "stat")
        self.g_value.setToolTip("Mean ASTM E112 grain size number G of the included fields")
        self.g_pm = label("", "h2", "secondary")
        self.g_pm.setToolTip("Half-width of the 95 % confidence interval of G (t·s/√n)")
        hero.addWidget(self.g_value, 0, Qt.AlignBottom)
        hero.addWidget(self.g_pm, 0, Qt.AlignBottom)
        hero.addStretch(1)
        self.method = label("", "caption")
        hero.addWidget(self.method, 0, Qt.AlignBottom)
        self.add_widget(self._wrap(hero))

        # figures
        row = QHBoxLayout()
        row.setSpacing(SPACE.lg)
        self.f_ci = _Figure("95 % CI of G", "Confidence interval of the mean G, from the "
                            "confidence bounds of N_A (planimetric) or mean intercept ℓ̄")
        self.f_ra = _Figure("Relative accuracy", "%RA = 100 · CI95 / mean, on N_A or ℓ̄ "
                            "(ASTM E112). 10 % or less is generally acceptable.")
        self.f_fields = _Figure("Fields", "Analysed images counted in the statistics, and "
                                "how many are needed for the target %RA")
        self.fields_bar = QProgressBar()
        self.fields_bar.setTextVisible(False)
        self.fields_bar.setFixedHeight(4)
        self.fields_bar.setAccessibleName("Fields measured of required")
        self.f_fields.layout().insertWidget(2, self.fields_bar)
        self.f_ecd = _Figure("Mean ECD", "Mean equivalent circle diameter of the fields "
                             "± 95 % confidence half-width")
        for i, f in enumerate((self.f_ci, self.f_ra, self.f_fields, self.f_ecd)):
            if i:
                row.addWidget(Divider(Qt.Vertical))
            row.addWidget(f, 1)
        self.figures = self._wrap(row)
        self.add_widget(self.figures)
        self.spec_line = label("", "caption")
        self.spec_line.setWordWrap(True)
        self.spec_line.hide()
        self.add_widget(self.spec_line)
        self.summary = label("", "caption")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.add_widget(self.summary)
        self.skeleton = Skeleton()
        self.skeleton.setFixedHeight(96)
        self.skeleton.hide()
        self.add_widget(self.skeleton)

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    # -------------------------------------------------------------- API
    def set_sessions(self, sessions: List[tuple], current: Optional[str]) -> None:
        self.scope.blockSignals(True)
        self.scope.clear()
        self.scope.addItem("Whole lot (all sessions)", None)
        for sid, name in sessions:
            self.scope.addItem(f"Session: {name}", sid)
        idx = self.scope.findData(current) if current else 0
        self.scope.setCurrentIndex(max(idx, 0))
        self.scope.blockSignals(False)
        self.scope.setVisible(len(sessions) > 1)

    def set_loading(self) -> None:
        self.skeleton.show()
        self.figures.hide()
        self.g_value.setText("G …")
        self.g_pm.setText("")
        self.summary.setText("Collecting the lot’s analysed fields…")
        self.chip.set_text("Loading")
        self.chip.set_kind("info")

    def set_error(self, msg: str) -> None:
        self.skeleton.hide()
        self.figures.hide()
        self.g_value.setText("G —")
        self.g_pm.setText("")
        self.summary.setText(f"Could not compute the lot result: {msg}")
        self.chip.set_text("Error")
        self.chip.set_kind("danger")

    def set_stats(self, st: SampleStatistics, animate: bool = True) -> None:
        prev = self.stats.G_mean if self.stats is not None else None
        self.stats = st
        self.skeleton.hide()
        self.figures.show()
        self.chip.set_text(status_text(st))
        self.chip.set_kind(STATUS_KIND.get(st.status_level, "neutral"))
        self.chip.setAccessibleName(f"Lot status: {status_text(st)}")
        stop(self._g_anim)
        if st.G_mean is None:
            self.g_value.setText("G n/a")
        else:
            start = prev if (animate and prev is not None) else st.G_mean - 0.5 if animate \
                else st.G_mean
            self._g_anim = animate_value(self, float(start), float(st.G_mean), MOTION.slow,
                                         lambda v: self.g_value.setText(f"G {float(v):.2f}"))
        self.g_pm.setText(f"± {st.G_ci95:.2f}" if st.G_ci95 is not None else
                          ("CI n/a" if st.G_mean is not None else ""))
        self.method.setText(f"{st.method.capitalize()} method · %RA on "
                            f"{'ℓ̄' if st.basis == 'l_bar' else 'N_A'}" if st.method else "")
        if st.G_ci_low is not None and st.G_ci_high is not None:
            self.f_ci.set(f"{st.G_ci_low:.2f} – {st.G_ci_high:.2f}",
                          f"t = {st.t_value:.3f}, s = {st.G_std:.3f}"
                          if st.t_value is not None and st.G_std is not None else "")
        else:
            self.f_ci.set("n/a", "Needs at least 2 fields")
        if st.RA_pct is not None:
            ok = st.RA_pct <= st.target_RA_pct
            self.f_ra.set(f"{st.RA_pct:.1f} %", f"Target ≤ {st.target_RA_pct:g} %",
                          "success" if ok else "warning")
        else:
            self.f_ra.set("n/a", f"Target ≤ {st.target_RA_pct:g} %")
        extra = []
        if st.n_excluded:
            extra.append(f"{st.n_excluded} excluded")
        if st.n_uncalibrated:
            extra.append(f"{st.n_uncalibrated} uncalibrated")
        self.f_fields.set(f"{st.n_fields} of {st.n_needed}",
                          " · ".join(extra) or f"Minimum {st.required_fields} fields")
        self.fields_bar.setRange(0, max(st.n_needed, 1))
        self.fields_bar.setValue(min(st.n_fields, max(st.n_needed, 1)))
        tone = "success" if st.adequate else None
        if self.fields_bar.property("tone") != tone:
            self.fields_bar.setProperty("tone", tone)
            repolish(self.fields_bar)
        if st.ecd_mean_um is not None:
            ci = f" ± {st.ecd_ci95_um:.2f}" if st.ecd_ci95_um is not None else ""
            self.f_ecd.set(f"{st.ecd_mean_um:.2f}{ci} µm",
                           "95 % CI" if ci else "CI needs ≥ 2 calibrated fields")
        else:
            self.f_ecd.set("n/a", "Calibrate the images to measure in µm")
        self.summary.setText(st.summary_text())
        self.setAccessibleDescription(st.summary_text())

    def set_verdict(self, verdict, animate: bool = True) -> None:
        """INN-02: show the conformity badge, or hide it (no spec -- the
        card then looks exactly as without the feature)."""
        self.verdict = verdict
        b = self.verdict_badge
        if verdict is None or verdict.overall not in VERDICT_KIND:
            b.hide()
            self.spec_line.hide()
            return
        b.set_text(VERDICT_TEXT[verdict.overall])
        b.set_verdict_kind(VERDICT_KIND[verdict.overall], animate=animate)
        tip = verdict_tooltip(verdict)
        b.setToolTip(tip)
        b.setAccessibleName(f"Spec verdict: {VERDICT_TEXT[verdict.overall]}")
        b.setAccessibleDescription(tip)
        name = verdict.spec_name + (f" {verdict.spec_revision}" if verdict.spec_revision else "")
        reasons = [r.text for r in verdict.rules if r.status != "pass" and r.text]
        rule = "guarded acceptance" if verdict.decision_rule == "guarded" else "simple acceptance"
        self.spec_line.setText(f"Spec {name} · {rule}" + (" · " + "; ".join(reasons[:2])
                                                          if reasons else ""))
        self.spec_line.setToolTip(tip)
        self.spec_line.show()
        b.show()

    def set_spec_error(self, msg: str) -> None:
        self.verdict = None
        b = self.verdict_badge
        b.set_text("SPEC ERROR")
        b.set_verdict_kind("neutral", animate=False)
        b.setToolTip(f"The project's spec limits could not be evaluated: {msg}")
        self.spec_line.hide()
        b.show()


class ExclusionBar(Card):
    """Inline, non-blocking prompt: why is this field excluded?"""

    confirmed = Signal(object, str)         # FieldResult, reason
    cancelled = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent, elevation=2)
        self.field: Optional[FieldResult] = None
        self.title = label("", "body_strong")
        self.add_widget(self.title)
        row = QHBoxLayout()
        row.setSpacing(SPACE.sm)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason (required) — e.g. scratch, out of focus, "
                                       "inclusion cluster")
        self.reason.setAccessibleName("Reason for excluding the field")
        self.reason.setToolTip("Written to the session’s audit log with your operator name")
        self.reason.textChanged.connect(lambda t: self.ok.setEnabled(bool(t.strip())))
        self.reason.returnPressed.connect(self._ok)
        self.cancel = AnimatedButton("Keep included", None, "ghost")
        self.cancel.setToolTip("Leave the field in the statistics")
        self.cancel.clicked.connect(self._cancel)
        self.ok = AnimatedButton("Exclude field", "remove", "primary")
        self.ok.setToolTip("Exclude this field from the lot statistics (logged)")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._ok)
        row.addWidget(self.reason, 1)
        row.addWidget(self.cancel)
        row.addWidget(self.ok)
        self.body_layout().addLayout(row)
        self.hide()

    def ask(self, field: FieldResult) -> None:
        self.field = field
        self.title.setText(f"Exclude “{field.image_name}” from the lot statistics?")
        self.reason.clear()
        self.show()
        self.reason.setFocus()

    def _ok(self) -> None:
        r = self.reason.text().strip()
        if not r or self.field is None:
            return
        f, self.field = self.field, None
        self.hide()
        self.confirmed.emit(f, r)

    def _cancel(self) -> None:
        f, self.field = self.field, None
        self.hide()
        if f is not None:
            self.cancelled.emit(f)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(e)


class FieldTable(QTableWidget):
    """One row per analysed image; column 0 is the Include checkbox."""

    include_toggled = Signal(object, bool)  # FieldResult, include
    open_requested = Signal(object)         # FieldResult

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(_COLS), parent)
        self.setHorizontalHeaderLabels(_COLS)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setIconSize(QPixmap(40, 30).size())
        self.verticalHeader().setDefaultSectionSize(38)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setStretchLastSection(False)
        self.setToolTip("Fields of this lot. Untick Include to leave a field out of the "
                        "statistics (a reason is required and logged). Space toggles; "
                        "double-click or Enter opens the image.")
        self.setAccessibleName("Fields of the lot")
        self.itemChanged.connect(self._on_item_changed)
        self.itemActivated.connect(lambda it: self._open_row(it.row()))
        self._fields: List[FieldResult] = []

    def fields(self) -> List[FieldResult]:
        return list(self._fields)

    def set_fields(self, fields: List[FieldResult], thumbs: dict, sessions: dict,
                   outliers=()) -> None:
        self.blockSignals(True)
        self._fields = list(fields)
        t = tokens()
        dim = QBrush(qcolor(t.text.tertiary))
        warn = QBrush(qcolor(t.warning.fg))
        self.setRowCount(len(fields))
        for r, f in enumerate(fields):
            inc = QTableWidgetItem()
            inc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            inc.setCheckState(Qt.Checked if f.included else Qt.Unchecked)
            inc.setData(FIELD_ROLE, f.field_id)
            inc.setToolTip("Counted in the lot statistics" if f.included else
                           f"Excluded: {f.exclusion_reason or 'no reason recorded'}")
            name = QTableWidgetItem(f.image_name)
            img = thumbs.get(f.field_id)
            if img is not None and not img.isNull():
                name.setIcon(QIcon(QPixmap.fromImage(img)))
            name.setToolTip(f"{f.image_name}\nDouble-click to open this image")
            vals = [sessions.get(f.session_id, f.session_id), _num(f.G),
                    f"{f.grain_count:,}", f"{f.valid_area_pct:.1f} %"]
            if not f.included:
                note = f"Excluded — {f.exclusion_reason or 'no reason'}"
            elif f.G is None:
                note = "Not calibrated"
            elif f.field_id in outliers:
                note = "Outlier (> 2.5 s from mean) — check this field"
            else:
                note = ""
            items = [inc, name] + [QTableWidgetItem(v) for v in vals] + [QTableWidgetItem(note)]
            for c, it in enumerate(items):
                if c >= 3 and c <= 5:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 6 and note:
                    it.setToolTip(note)
                if not f.included and c > 0:
                    it.setForeground(dim)
                elif c == 6 and f.field_id in outliers:
                    it.setForeground(warn)
                self.setItem(r, c, it)
        self.blockSignals(False)
        self.setFixedHeight(min(8, max(len(fields), 1)) * 38 +
                            self.horizontalHeader().sizeHint().height() + 4)

    def set_checked(self, field_id: str, on: bool) -> None:
        for r, f in enumerate(self._fields):
            if f.field_id == field_id:
                self.blockSignals(True)
                self.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
                self.blockSignals(False)

    def _on_item_changed(self, it: QTableWidgetItem) -> None:
        if it.column() != 0 or it.row() >= len(self._fields):
            return
        self.include_toggled.emit(self._fields[it.row()], it.checkState() == Qt.Checked)

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._fields):
            self.open_requested.emit(self._fields[row])


class LotResultPanel(QWidget):
    """Card + exclusion bar + field table, as shown above a lot's cards."""

    include_requested = Signal(object, bool, str)   # FieldResult, include, reason
    open_field_requested = Signal(object, str)      # session Path, image filename

    def __init__(self, settings_getter, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings_getter
        self.data: Optional[dict] = None
        self.scope_sid: Optional[str] = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, SPACE.md)
        v.setSpacing(SPACE.md)
        self.card = LotResultCard()
        self.card.scope_changed.connect(self._on_scope)
        self.bar = ExclusionBar()
        self.bar.confirmed.connect(lambda f, r: self.include_requested.emit(f, False, r))
        self.bar.cancelled.connect(lambda f: self.table.set_checked(f.field_id, True))
        self.table = FieldTable()
        self.table.include_toggled.connect(self._on_toggle)
        self.table.open_requested.connect(
            lambda f: self.open_field_requested.emit(Path(f.session_path), f.image_name))
        self.table_title = label("FIELDS", "overline")
        v.addWidget(self.card)
        v.addWidget(self.bar)
        v.addWidget(self.table_title)
        v.addWidget(self.table)

    def set_loading(self) -> None:
        self.bar.hide()
        self.card.set_loading()

    def set_error(self, msg: str) -> None:
        self.card.set_error(msg)

    def set_data(self, data: dict) -> None:
        same_lot = self.data is not None and self.data.get("lot") == data.get("lot")
        if not same_lot:
            self.scope_sid = None
        sids = {sid for sid, _n in data["sessions"]}
        if self.scope_sid not in sids:
            self.scope_sid = None
        self.data = data
        self.card.set_sessions(data["sessions"], self.scope_sid)
        self._refresh(animate=not same_lot)

    def stats(self) -> Optional[SampleStatistics]:
        return self.card.stats

    def _on_scope(self, sid) -> None:
        self.scope_sid = sid
        self._refresh(animate=True)

    def _refresh(self, animate: bool) -> None:
        d = self.data
        if d is None:
            return
        fields = d["fields"]
        st = sample_statistics(fields, stats_config(self._settings(), self.scope_sid))
        self.card.set_stats(st, animate=animate)
        self._refresh_verdict(st, animate)
        shown = [f for f in fields if self.scope_sid is None or f.session_id == self.scope_sid]
        self.table.set_fields(shown, d.get("thumbs", {}), dict(d["sessions"]),
                              set(st.outlier_field_ids))
        n_ex = sum(1 for f in shown if not f.included)
        self.table_title.setText(f"FIELDS · {len(shown)} ANALYSED"
                                 + (f" · {n_ex} EXCLUDED" if n_ex else ""))

    def verdict(self):
        return self.card.verdict

    def _refresh_verdict(self, st: SampleStatistics, animate: bool) -> None:
        """The verdict is always for the whole lot (not the session scope)."""
        spec = (self.data or {}).get("spec")
        if spec is None:
            self.card.set_verdict(None)
            return
        from data.specs import SpecError, evaluate
        if self.scope_sid is not None:
            st = sample_statistics(self.data["fields"], stats_config(self._settings()))
        try:
            self.card.set_verdict(evaluate(spec, st), animate=animate)
        except SpecError as e:
            self.card.set_spec_error(str(e))

    def _on_toggle(self, field: FieldResult, include: bool) -> None:
        if include:
            self.bar.hide()
            self.include_requested.emit(field, True, "")
        else:
            self.bar.ask(field)
