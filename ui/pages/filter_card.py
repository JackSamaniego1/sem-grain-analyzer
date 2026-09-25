"""
"Grain filters" card — border-grain and false-grain removal plus size/shape
limits, shown on Analyze and Review once an image has results.

Non-destructive: every change only re-runs the post-filter on the kept raw
result, so switching a filter off restores exactly what was there.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QDoubleSpinBox, QFormLayout, QGraphicsOpacityEffect,
    QHBoxLayout, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ui.design.tokens import MOTION, SPACE
from ui.filtering import PostFilterOptions, counts_summary, options_from_dict, options_to_dict
from ui.format import fmt_int
from ui.widgets import (
    AnimatedButton, Badge, Card, CollapsibleSection, SegmentedControl, Spinner, label,
)
from ui.widgets._base import (
    ThemeAware, animate_property, animate_value, lerp_color, qcolor, stop, tokens,
)


class Switch(ThemeAware, QAbstractButton):
    """Animated on/off switch."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._pos = 0.0
        self._anim = None
        self.toggled.connect(self._animate)
        self._connect_theme()

    def sizeHint(self) -> QSize:
        return QSize(38, 22)

    def _animate(self, on: bool) -> None:
        stop(self._anim)
        self._anim = animate_value(self, self._pos, 1.0 if on else 0.0, MOTION.fast, self._set)

    def _set(self, v) -> None:
        self._pos = float(v)
        self.update()

    def setChecked(self, on: bool) -> None:  # noqa: N802 - keep knob in sync without animation
        super().setChecked(on)
        stop(self._anim)
        self._pos = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        track = lerp_color(qcolor(t.surface.surface3), qcolor(t.accent.base), self._pos)
        p.setPen(QPen(lerp_color(qcolor(t.border.strong), qcolor(t.accent.base), self._pos), 1))
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._pos
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor("#FFFFFF" if self._pos > 0.5 else t.text.secondary))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.border.focus), 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 11, 11)


class ToggleRow(QWidget):
    """Switch + title + caption + live count badge; the whole row is clickable."""

    toggled = Signal(bool)

    def __init__(self, title: str, caption: str, tip: str, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(SPACE.md)
        self.switch = Switch()
        self.switch.setToolTip(tip)
        self.switch.setAccessibleName(title)
        self.switch.toggled.connect(self.toggled)
        h.addWidget(self.switch, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = label(title, "body_strong")
        self.caption = label(caption, "caption")
        self.caption.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.caption)
        h.addLayout(col, 1)
        self.badge = Badge("0", "neutral")
        self.badge.setToolTip("Grains this filter removes from the current image")
        h.addWidget(self.badge, 0, Qt.AlignTop)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.switch.toggle()

    def set_count(self, n: int, active: bool) -> None:
        self.badge.set_text(f"−{fmt_int(n)}" if active and n else fmt_int(n))
        self.badge.set_kind("warning" if active and n else "neutral")


class FilterCard(Card):
    options_changed = Signal(object, str)     # PostFilterOptions, scope ("session"|"image")
    apply_all_requested = Signal(object)      # PostFilterOptions
    apply_image_requested = Signal(object)    # UX-04: "Apply" with the "This image" scope
    show_excluded_toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__("Grain filters",
                         "Non-destructive — switch a filter off to restore its grains",
                         elevation=1, parent=parent)
        self._ppu = 0.0
        self._loading = False
        self.spinner = Spinner(16)
        self.spinner.setToolTip("Applying filters…")
        self.spinner.hide()
        self.add_action(self.spinner)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._emit)
        b = self.body_layout()
        b.setSpacing(SPACE.sm)
        self.border = ToggleRow(
            "Remove border grains", "Grains cut by the edge of the analysed area",
            "Exclude grains that touch the image edge or the scan-area border — they are "
            "only partially visible, so their size is unknown (ASTM E112 practice).")
        self.false = ToggleRow(
            "Remove false grains", "Low-contrast, very dark, or touching black regions",
            "Exclude detections that are probably not real grains: flat low-contrast "
            "patches, near-black voids and regions touching excluded black areas.")
        self.border.toggled.connect(lambda _v: self._emit())
        self.false.toggled.connect(lambda _v: self._emit())
        b.addWidget(self.border)
        b.addWidget(self.false)

        self.more = CollapsibleSection("More filters", expanded=False)
        self.more_badge = Badge("", "neutral")
        self.more.header_trailing().addWidget(self.more_badge)
        f = QFormLayout()
        f.setVerticalSpacing(SPACE.sm)
        f.setHorizontalSpacing(SPACE.md)
        self.min_area = self._dspin(0, 1e9, 2, "Grains smaller than this are excluded (0 = off)")
        self.max_area = self._dspin(0, 1e10, 2, "Grains larger than this are excluded (0 = off)")
        self.max_ar = self._dspin(0, 50, 2, "Exclude grains more elongated than this "
                                            "major/minor axis ratio (0 = off)")
        self.min_circ = self._dspin(0, 1, 2, "Exclude grains less round than this "
                                             "(1 = perfect circle, 0 = off)")
        self.min_circ.setSingleStep(0.05)
        self.lc_std = self._dspin(0, 50, 1, "Intensity standard deviation below which a "
                                            "region counts as low-contrast (false grain)")
        self.lc_std.setSpecialValueText("")
        self.dark = QSpinBox()
        self.dark.setRange(0, 80)
        self.dark.setToolTip("Mean grey level at or below which a region counts as "
                             "too dark (void / black area)")
        self.dark.valueChanged.connect(lambda _v: self._debounce.start())
        self.area_lbl_min = label("Min area")
        self.area_lbl_max = label("Max area")
        f.addRow(self.area_lbl_min, self.min_area)
        f.addRow(self.area_lbl_max, self.max_area)
        f.addRow("Max aspect ratio", self.max_ar)
        f.addRow("Min circularity", self.min_circ)
        f.addRow("Low-contrast limit (σ)", self.lc_std)
        f.addRow("Too-dark limit (grey)", self.dark)
        host = QWidget()
        host.setLayout(f)
        self.more.add_widget(host)
        b.addWidget(self.more)

        self.show_ex = QCheckBox("Show excluded grains (greyed) on the overlay")
        self.show_ex.setChecked(True)
        self.show_ex.setToolTip("Hide or show the grains removed by filters or by hand")
        self.show_ex.toggled.connect(self.show_excluded_toggled)
        b.addWidget(self.show_ex)

        row = QHBoxLayout()
        row.setSpacing(SPACE.sm)
        row.addWidget(label("Applies to", tone="secondary"))
        self.scope = SegmentedControl(["All images", "This image"])
        self.scope.setToolTip("Change the session filters (all images) or give only the "
                              "current image its own filters")
        self.scope.setFixedWidth(180)
        self.scope.current_changed.connect(lambda _i: self._sync_apply_button())
        row.addWidget(self.scope)
        row.addStretch(1)
        b.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(SPACE.sm)
        self.summary = label("", "caption")
        self.summary.setWordWrap(True)
        row2.addWidget(self.summary, 1)
        self.apply_all = AnimatedButton("Apply to all images", "check", "ghost", "sm")
        self.apply_all.setToolTip("Use these filters for every image of the session "
                                  "(removes per-image filters)")
        self.apply_all.clicked.connect(self._apply_clicked)
        row2.addWidget(self.apply_all)
        b.addLayout(row2)
        self._base = PostFilterOptions()

    def _dspin(self, lo, hi, dec, tip) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setSpecialValueText("Off")
        s.setToolTip(tip)
        s.setKeyboardTracking(False)
        s.valueChanged.connect(lambda _v: self._debounce.start())
        return s

    # ------------------------------------------------------------------ API
    def set_state(self, opts: PostFilterOptions, counts: dict, ppu: float,
                  image_scope: bool, n_kept: Optional[int] = None) -> None:
        self._loading = True
        self._base = opts
        self._ppu = float(ppu or 0.0)
        cal = self._ppu > 0
        unit = " µm²" if cal else " px²"
        k = self._ppu ** 2 if cal else 1.0
        self.border.switch.setChecked(bool(opts.exclude_border))
        self.false.switch.setChecked(bool(opts.exclude_low_contrast or opts.exclude_touching_invalid))
        for s in (self.min_area, self.max_area):
            s.blockSignals(True)
            s.setSuffix(unit)
            s.setDecimals(2 if cal else 0)
        self.min_area.setValue((opts.min_area_px or 0) / k)
        self.max_area.setValue((opts.max_area_px or 0) / k)
        for s, v in ((self.max_ar, opts.max_aspect_ratio), (self.min_circ, opts.min_circularity),
                     (self.lc_std, opts.low_contrast_std)):
            s.blockSignals(True)
            s.setValue(float(v or 0.0))
        self.dark.blockSignals(True)
        self.dark.setValue(int(opts.dark_threshold or 0))
        for s in (self.min_area, self.max_area, self.max_ar, self.min_circ, self.lc_std, self.dark):
            s.blockSignals(False)
        self.scope.blockSignals(True)
        self.scope.set_current_index(1 if image_scope else 0, animate=False)
        self.scope.blockSignals(False)
        self._sync_apply_button()
        self.update_counts(counts, n_kept)
        self._loading = False

    def update_counts(self, counts: dict, n_kept: Optional[int] = None) -> None:
        c = counts_summary(counts or {})
        self.border.set_count(c["border"], self.border.switch.isChecked())
        self.false.set_count(c["false"], self.false.switch.isChecked())
        self.more_badge.set_text(f"−{c['shape']}" if c["shape"] else "")
        self.more_badge.set_kind("warning" if c["shape"] else "neutral")
        self.more_badge.setVisible(bool(c["shape"]))
        parts = []
        if n_kept is not None:
            parts.append(f"{fmt_int(n_kept)} grains kept")
        removed = sum(v for v in (counts or {}).values())
        if c["manual"]:
            parts.append(f"{c['manual']} removed by hand (Ctrl+Z to undo)")
        if not parts and not removed:
            parts.append("No grains excluded")
        self.summary.setText(" · ".join(parts))

    def set_busy(self, on: bool) -> None:
        self.spinner.setVisible(bool(on))

    def scope_is_image(self) -> bool:
        return self.scope.current_index() == 1

    def _sync_apply_button(self) -> None:
        """UX-04: "Apply" for this image, "Apply to all images" otherwise."""
        if self.scope_is_image():
            self.apply_all.setText("Apply")
            self.apply_all.setToolTip("Use these filters for this image only")
        else:
            self.apply_all.setText("Apply to all images")
            self.apply_all.setToolTip("Use these filters for every image in the analyzer "
                                      "(removes per-image filters)")

    def _apply_clicked(self) -> None:
        if self.scope_is_image():
            self.apply_image_requested.emit(self.options())
        else:
            self.apply_all_requested.emit(self.options())

    def options(self) -> PostFilterOptions:
        d = options_to_dict(self._base)
        k = self._ppu ** 2 if self._ppu > 0 else 1.0
        on_false = self.false.switch.isChecked()
        d.update(exclude_border=self.border.switch.isChecked(),
                 exclude_low_contrast=on_false, exclude_touching_invalid=on_false,
                 min_area_px=float(self.min_area.value() * k),
                 max_area_px=float(self.max_area.value() * k),
                 max_aspect_ratio=float(self.max_ar.value()),
                 min_circularity=float(self.min_circ.value()),
                 low_contrast_std=float(self.lc_std.value() or 3.0),
                 dark_threshold=int(self.dark.value()))
        return options_from_dict(d)

    def _emit(self) -> None:
        if self._loading:
            return
        self._debounce.stop()
        self.options_changed.emit(self.options(), "image" if self.scope_is_image() else "session")


class Reveal(QWidget):
    """Host that slides its child open (animated height) the first time it
    is shown — used for the FilterCard once results exist.  (No opacity
    effect: the Card inside already owns a drop-shadow effect and nested
    graphics effects render unreliably.)"""

    def __init__(self, child: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.child = child
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.addWidget(child)
        self._anim = None
        self.setVisible(False)

    def reveal(self, on: bool) -> None:
        if bool(on) == (not self.isHidden()):
            return
        stop(self._anim)
        if on:
            self.setVisible(True)
            target = max(self.sizeHint().height(), self.child.sizeHint().height()) + 6
            self.setMaximumHeight(0)
            self._anim = animate_value(self, 0, target, MOTION.slow,
                                       lambda v: self.setMaximumHeight(int(v)),
                                       on_finished=lambda: self.setMaximumHeight(16777215))
        else:
            self.setVisible(False)
