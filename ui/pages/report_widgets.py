"""
Report-designer building blocks.

"Paper" widgets (``CoverSlide``, ``ImageSlide``, ``TextSlide``,
``MethodsSlide``, ``OverviewSheet``) paint the document in the renderers'
own palette (``reports.charts.SERIES``) — a report looks the same whatever
the app theme, exactly like a page on a light table.  Everything around the
paper (banner, callouts, chrome) uses the app's theme tokens.
"""
from __future__ import annotations

import os
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from reports.charts import SERIES, TAB_COLORS
from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, SPACE, TypeStyle
from ui.widgets import AnimatedButton, IconButton, label
from ui.widgets._base import ThemeAware, animate_value, qcolor, stop, tokens

SLIDE_W, SLIDE_H = 1333.0, 750.0          # 13.333 in × 7.5 in, 1 unit = 0.01 in
NAVY = QColor(SERIES["navy"])
TEAL = QColor(SERIES["teal"])
WHITE = QColor("#FFFFFF")
GREY = QColor(SERIES["grey"])
TEXT_DARK = QColor(SERIES["text_dark"])
BAND = QColor(SERIES["band"])
BAND_ALT = QColor(SERIES["band_alt"])
HEADER = QColor("#2E5FA3")
TOTAL = QColor("#D9DEE8")
LINK = QColor("#1155CC")
GRID = QColor("#D0D5DD")


def _font(px: float, weight: int = 400, italic: bool = False) -> QFont:
    f = ui_font(TypeStyle(max(1, int(round(px))), weight, int(px * 1.3)))
    f.setItalic(italic)
    return f


def bgr_pixmap(arr) -> QPixmap:
    from ui.workers import thumb_qimage
    if arr is None:
        return QPixmap()
    return QPixmap.fromImage(thumb_qimage(arr, 1100, 800))


def file_pixmap(path: Optional[str]) -> QPixmap:
    if not path or not os.path.exists(path):
        return QPixmap()
    from ui.workers import read_image
    return bgr_pixmap(read_image(path))


# ----------------------------------------------------------------------
# UX-12: thread-safe (no QPixmap) decode helpers for background loading.
# QImage — unlike QPixmap — is safe to build off the GUI thread; callers
# wrap the result in QPixmap.fromImage(...) back on the GUI thread once the
# background task hands the QImage back (see ``ReportsPage.pixmaps``).
# ----------------------------------------------------------------------

def arr_thumb_qimage(arr, max_w: int = 1100, max_h: int = 800):
    from ui.workers import thumb_qimage
    if arr is None:
        from PySide6.QtGui import QImage
        return QImage()
    return thumb_qimage(arr, max_w, max_h)


def file_thumb_qimage(path: Optional[str], max_w: int = 1100, max_h: int = 800):
    from PySide6.QtGui import QImage
    if not path or not os.path.exists(path):
        return QImage()
    from ui.workers import read_image
    return arr_thumb_qimage(read_image(path), max_w, max_h)


# ======================================================================
# Paper base
# ======================================================================

class Paper(ThemeAware, QWidget):
    """Fixed-aspect page with a soft shadow; subclasses paint in slide units
    (1333 × 750) through :meth:`paint_page`."""

    clicked_region = Signal(str)

    def __init__(self, aspect: float = SLIDE_W / SLIDE_H, parent=None) -> None:
        super().__init__(parent)
        self._aspect = aspect
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumWidth(360)
        self._connect_theme()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return int((w - 12) / self._aspect) + 12

    def sizeHint(self) -> QSize:
        return QSize(880, self.heightForWidth(880))

    def page_rect(self) -> QRectF:
        w = self.width() - 12
        h = w / self._aspect
        if h > self.height() - 12:
            h = self.height() - 12
            w = h * self._aspect
        return QRectF((self.width() - w) / 2, 4, w, h)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.page_rect()
        for i, a in enumerate((0.10, 0.06, 0.03)):
            sh = QColor(0, 0, 0)
            sh.setAlphaF(a * (2.2 if t.mode == "dark" else 1.0))
            p.setPen(Qt.NoPen)
            p.setBrush(sh)
            p.drawRoundedRect(r.adjusted(-i, 1 + i, i, 3 + i * 2), 3 + i, 3 + i)
        p.setBrush(WHITE)
        p.setPen(QPen(qcolor(t.border.strong), 1))
        p.drawRect(r)
        p.save()
        p.translate(r.topLeft())
        s = r.width() / SLIDE_W
        p.scale(s, s)
        p.setClipRect(QRectF(0, 0, SLIDE_W, SLIDE_W / self._aspect))
        self.paint_page(p, SLIDE_W, SLIDE_W / self._aspect)
        p.restore()

    def paint_page(self, p: QPainter, w: float, h: float) -> None:  # pragma: no cover
        pass


def _text(p: QPainter, rect: QRectF, text: str, px: float, color: QColor, weight: int = 400,
          flags=Qt.AlignLeft | Qt.AlignVCenter, italic: bool = False) -> None:
    p.setFont(_font(px, weight, italic))
    p.setPen(color)
    p.drawText(rect, int(flags) | Qt.TextWordWrap, text)


def _heading(p: QPainter, w: float, text: str) -> None:
    p.fillRect(QRectF(0, 0, w, 90), NAVY)
    _text(p, QRectF(50, 12, w - 100, 66), text, 30, WHITE, 700)


def _footer(p: QPainter, w: float, h: float, title: str, page: str) -> None:
    p.fillRect(QRectF(0, h - 32, w, 32), NAVY)
    _text(p, QRectF(30, h - 32, w * 0.7, 32), title, 12, WHITE)
    _text(p, QRectF(w - 130, h - 32, 100, 32), page, 12, WHITE, 400,
          Qt.AlignRight | Qt.AlignVCenter)


def _fit(pm: QPixmap, box: QRectF) -> QRectF:
    if pm.isNull():
        return box
    s = min(box.width() / pm.width(), box.height() / pm.height())
    w, h = pm.width() * s, pm.height() * s
    return QRectF(box.x() + (box.width() - w) / 2, box.y() + (box.height() - h) / 2, w, h)


def _placeholder(p: QPainter, box: QRectF, text: str) -> None:
    p.setPen(QPen(GRID, 2, Qt.DashLine))
    p.setBrush(BAND)
    p.drawRect(box)
    _text(p, box, text, 16, GREY, 400, Qt.AlignCenter)


# ======================================================================
# Slides
# ======================================================================

class CoverSlide(Paper):
    """Title slide (PowerPoint) — also the Overview sheet's header block."""

    def __init__(self, model, parent=None) -> None:
        super().__init__(parent=parent)
        self.model = model
        self._logo = QPixmap()
        self._logo_path = None

    def paint_page(self, p, w, h) -> None:
        m = self.model
        p.fillRect(QRectF(0, 0, w, 18), NAVY)
        p.fillRect(QRectF(0, 18, w, 6), TEAL)
        _text(p, QRectF(80, 250, w - 160, 120), m.title or "Grain Analysis Report", 44, NAVY, 700,
              Qt.AlignLeft | Qt.AlignBottom)
        if m.hierarchy:
            where = "    ·    ".join(f"{h.get('label', '')}: {h.get('value', '') or '—'}"
                                    for h in m.hierarchy)
        else:
            samples = sorted({i.sample_id for i in m.images if i.sample_id}) or ["—"]
            lots = sorted({i.lot_number for i in m.images if i.lot_number}) or ["—"]
            where = f"Sample/Lot: {', '.join(samples)} / {', '.join(lots)}"
        _text(p, QRectF(80, 380, w - 160, 44), f"{where}    |    {m.date}", 18, GREY)
        _text(p, QRectF(80, 428, w - 160, 44),
              f"Operator: {m.operator or '—'}    |    Organization: {m.organization or '—'}", 18, GREY)
        if m.logo_path != self._logo_path:
            self._logo_path = m.logo_path
            self._logo = QPixmap(m.logo_path) if m.logo_path and os.path.exists(m.logo_path) \
                else QPixmap()
        box = QRectF(w - 260, 50, 200, 100)
        if not self._logo.isNull():
            p.drawPixmap(_fit(self._logo, box).toRect(), self._logo)
        else:
            p.setPen(QPen(GRID, 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(box)
            _text(p, box, "Logo", 14, GREY, 400, Qt.AlignCenter)
        _footer(p, w, h, m.title or "", "1")


class ImageSlide(Paper):
    """Per-image slide: original + overlay, six metric callouts, caption."""

    def __init__(self, model, img, original: QPixmap, overlay: QPixmap, number: int,
                 parent=None, loading: bool = False) -> None:
        super().__init__(parent=parent)
        self.model, self.img = model, img
        self.original, self.overlay = original, overlay
        self.number = number
        # UX-12: true while the real pixmaps are still decoding on a
        # background thread (see ``ReportsPage.pixmaps``) — the placeholder
        # then reads "Loading..." instead of "Image file not found".
        self.loading = loading

    def metrics(self) -> List[tuple]:
        from reports.excel_renderer import _row_size_stats
        img = self.img
        au, du, mean_a, _med, _std, mean_d, _sd = _row_size_stats(self.model, img)
        return [(str(img.grain_count), "Grains"), (f"{mean_d:.2f} {du}", "Mean Diameter"),
                (f"{mean_a:.2f} {au}", "Mean Area"), (f"{img.grain_coverage_pct:.1f}%", "Coverage"),
                (f"{img.mean_circularity:.3f}", "Mean Circularity"),
                (f"{img.mean_aspect_ratio:.3f}", "Mean Aspect Ratio")]

    def paint_page(self, p, w, h) -> None:
        img = self.img
        _heading(p, w, f"Image {self.number}: {os.path.basename(img.image_path)}")
        left = QRectF(50, 105, 610, 360)
        right = QRectF(690, 105, 610, 360)
        if not self.original.isNull():
            p.drawPixmap(_fit(self.original, left).toRect(), self.original)
        else:
            _placeholder(p, left, "Loading..." if self.loading else "Image file not found")
        if not self.overlay.isNull():
            p.drawPixmap(_fit(self.overlay, right).toRect(), self.overlay)
        else:
            _placeholder(p, right, "Loading..." if self.loading else "No overlay")
        top, cw = 485.0, 195.0
        for i, (val, lbl) in enumerate(self.metrics()):
            r = QRectF(50 + cw * i, top, cw - 8, 90)
            p.fillRect(r, BAND)
            _text(p, QRectF(r.x(), r.y() + 6, r.width(), 50), val, 22, NAVY, 700, Qt.AlignCenter)
            _text(p, QRectF(r.x(), r.bottom() - 34, r.width(), 28), lbl, 13, GREY, 400,
                  Qt.AlignCenter)
        cap = (img.caption + ("\n" + img.notes if img.notes else "")).strip()
        if cap:
            _text(p, QRectF(50, top + 102, w - 110, h - top - 102 - 40), cap, 16, GREY, 400,
                  Qt.AlignLeft | Qt.AlignTop)
        else:
            _text(p, QRectF(50, top + 102, w - 110, 40), "Add a caption below — it prints here.",
                  13, GRID, 400, Qt.AlignLeft | Qt.AlignTop, italic=True)
        _footer(p, w, h, self.model.title or "", "")


class TextSlide(Paper):
    def __init__(self, model, section, parent=None) -> None:
        super().__init__(parent=parent)
        self.model, self.section = model, section

    def paint_page(self, p, w, h) -> None:
        _heading(p, w, self.section.title or "Notes")
        body = str(self.section.payload.get("body", "") or "")
        if body.strip():
            _text(p, QRectF(80, 130, w - 160, h - 200), body, 17, TEXT_DARK, 400,
                  Qt.AlignLeft | Qt.AlignTop)
        else:
            _text(p, QRectF(80, 130, w - 160, 60), "Type the text for this slide on the right.",
                  17, GRID, 400, Qt.AlignLeft | Qt.AlignTop, italic=True)
        _footer(p, w, h, self.model.title or "", "")


class MethodsSlide(Paper):
    def __init__(self, model, lines: Sequence[tuple], parent=None) -> None:
        super().__init__(parent=parent)
        self.model = model
        self.lines = list(lines)

    def paint_page(self, p, w, h) -> None:
        _heading(p, w, "Methods & Parameters")
        y = 120.0
        rows = self.lines[:22]
        step = min(34.0, (h - 170) / max(1, len(rows)))
        for i, (k, v) in enumerate(rows):
            if i % 2 == 0:
                p.fillRect(QRectF(70, y - 2, w - 140, step), BAND)
            _text(p, QRectF(86, y, 420, step - 4), str(k), 14, TEXT_DARK, 600)
            _text(p, QRectF(520, y, w - 600, step - 4), str(v), 14, TEXT_DARK)
            y += step
        _footer(p, w, h, self.model.title or "", "")


# ======================================================================
# Overview sheet (Excel look)
# ======================================================================

class OverviewSheet(ThemeAware, QWidget):
    """The Overview worksheet, cell for cell: title band, sub-title band,
    header row, one banded row per included image, bold combined row."""

    WIDTHS = [5, 30, 12, 12, 9, 13, 13, 13, 15, 14, 13, 11, 10, 15, 15, 9]
    CHAR = 7.2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.title = ""
        self.subtitle = ""
        self.header: List[str] = []
        self.rows: List[List[str]] = []
        self.total: Optional[List[str]] = None
        self.table_enabled = True
        self.hier_line = ""
        self._connect_theme()

    def set_table(self, title, subtitle, header, rows, total, enabled=True,
                  hier_line: str = "") -> None:
        """``hier_line`` (HIER-01): "Job #: 24-117 | Part Number: 7718-A |
        Lot: L-44A" band under the sub-title, as the workbook prints it."""
        self.title, self.subtitle = title, subtitle
        self.header, self.rows, self.total = header, rows, total
        self.table_enabled = enabled
        self.hier_line = hier_line or ""
        self.updateGeometry()
        self.update()

    def _has_file_col(self) -> bool:
        return len(self.header) > 2 and self.header[2] == "File"

    def _cols(self) -> List[float]:
        widths = list(self.WIDTHS)
        if self._has_file_col():
            n_levels = len(self.header) - 3 - (len(self.WIDTHS) - 4)
            widths = widths[:2] + [24] + [13] * max(0, n_levels) + widths[4:]
        return [max(34.0, c * self.CHAR + 10) for c in widths]

    def _band_h(self) -> int:
        return 22 + (22 if self.hier_line else 0)

    def sizeHint(self) -> QSize:
        n = len(self.rows) + (1 if self.total else 0)
        return QSize(int(sum(self._cols())) + 2,
                     int(30 + self._band_h() + 16 + 42 + 24 * n + 30))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        cols = self._cols()
        W = sum(cols)
        p.fillRect(QRectF(0, 0, W + 2, self.height()), WHITE)
        # row 1: title, row 2: subtitle (merged across the table)
        p.fillRect(QRectF(0, 0, W, 30), NAVY)
        _text(p, QRectF(12, 0, W - 24, 30), self.title, 16, WHITE, 700)
        p.fillRect(QRectF(0, 30, W, 22), QColor(TAB_COLORS["overview"]))
        _text(p, QRectF(8, 30, W - 16, 22), self.subtitle, 10.5, WHITE)
        if self.hier_line:
            p.fillRect(QRectF(0, 52, W, 22), QColor(TAB_COLORS["overview"]))
            _text(p, QRectF(8, 52, W - 16, 22), self.hier_line, 10.5, WHITE, 700)
        y = 30 + self._band_h() + 16
        links = {1, 2} if self._has_file_col() else {1}
        if not self.table_enabled or not self.rows:
            msg = ("Overview table is switched off — the sheet keeps only its header."
                   if not self.table_enabled else "No images are included in the report.")
            _text(p, QRectF(8, y, W - 16, 30), msg, 12, GREY, 400, Qt.AlignLeft | Qt.AlignVCenter,
                  italic=True)
            return
        x = 0.0
        for c, h in enumerate(self.header):
            r = QRectF(x, y, cols[c], 42)
            p.fillRect(r, HEADER)
            p.setPen(QPen(GRID, 1))
            p.drawRect(r)
            _text(p, r.adjusted(3, 0, -3, 0), h, 10.5, WHITE, 700, Qt.AlignCenter)
            x += cols[c]
        y += 42
        body = list(self.rows) + ([self.total] if self.total else [])
        for i, row in enumerate(body):
            is_total = self.total is not None and i == len(body) - 1
            bg = TOTAL if is_total else (BAND if i % 2 else BAND_ALT)
            x = 0.0
            for c, val in enumerate(row):
                r = QRectF(x, y, cols[c], 24)
                p.fillRect(r, bg)
                p.setPen(QPen(GRID, 1))
                p.drawRect(r)
                if c in links and not is_total:
                    f = _font(11.5)
                    f.setUnderline(True)
                    p.setFont(f)
                    p.setPen(LINK)
                    p.drawText(r.adjusted(8, 0, -4, 0), int(Qt.AlignLeft | Qt.AlignVCenter),
                               QFontMetricsF(f).elidedText(val, Qt.ElideRight, r.width() - 12))
                else:
                    align = Qt.AlignLeft | Qt.AlignVCenter if (is_total and c == 1) else Qt.AlignCenter
                    _text(p, r.adjusted(4, 0, -4, 0), val, 11.5, TEXT_DARK,
                          700 if is_total else 400, align)
                x += cols[c]
            y += 24
        _text(p, QRectF(4, y + 4, W, 20),
              "* size columns use each row's own units; the combined row uses the report units.",
              10, GREY, 400, Qt.AlignLeft | Qt.AlignVCenter, italic=True)


# ======================================================================
# Per-image bars (Summary Charts sheet: mean diameter ± std per image)
# ======================================================================

class PerImageBars(ThemeAware, QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: List[tuple] = []   # (label, mean, std, count)
        self.unit = ""
        self.setMinimumHeight(200)
        self._connect_theme()

    def set_items(self, items, unit: str) -> None:
        self.items, self.unit = list(items), unit
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), qcolor(t.surface.surface1))
        if not self.items:
            return
        ml, mb, mt = 46, 46, 14
        pw, ph = w - ml - 12, h - mb - mt
        vmax = max((m + s) for _l, m, s, _c in self.items) or 1.0
        n = len(self.items)
        bw = pw / n
        grid = qcolor(t.border.subtle)
        f = ui_font(TypeStyle(10, 400, 14))
        p.setFont(f)
        for i in range(5):
            yv = vmax * i / 4
            yp = mt + ph - yv / vmax * ph
            p.setPen(QPen(grid, 1, Qt.SolidLine if i == 0 else Qt.DotLine))
            p.drawLine(ml, int(yp), ml + pw, int(yp))
            p.setPen(qcolor(t.text.tertiary))
            p.drawText(QRectF(0, yp - 8, ml - 6, 16), int(Qt.AlignRight | Qt.AlignVCenter),
                       f"{yv:.3g}")
        col = QColor(t.dataviz[2 % len(t.dataviz)])
        for i, (lbl, mean, std, _c) in enumerate(self.items):
            x0 = ml + i * bw + bw * 0.2
            bh = mean / vmax * ph
            r = QRectF(x0, mt + ph - bh, bw * 0.6, bh)
            path = QPainterPath()
            path.addRoundedRect(r, 2, 2)
            p.fillPath(path, col)
            cx = r.center().x()
            p.setPen(QPen(qcolor(t.text.secondary), 1.4))
            y1 = mt + ph - (mean + std) / vmax * ph
            y2 = mt + ph - max(0.0, mean - std) / vmax * ph
            p.drawLine(int(cx), int(y1), int(cx), int(y2))
            p.drawLine(int(cx - 5), int(y1), int(cx + 5), int(y1))
            p.setPen(qcolor(t.text.tertiary))
            fm = QFontMetricsF(f)
            p.drawText(QRectF(ml + i * bw, mt + ph + 4, bw, 18), int(Qt.AlignHCenter | Qt.AlignTop),
                       fm.elidedText(lbl, Qt.ElideMiddle, bw - 4))
        p.setPen(qcolor(t.text.secondary))
        p.setFont(ui_font(TypeStyle(11, 600, 16)))
        p.drawText(QRectF(ml, h - 20, pw, 18), int(Qt.AlignCenter),
                   f"Mean grain diameter per image ({self.unit}) ± 1 std")


# ======================================================================
# Banner (non-blocking notice with actions)
# ======================================================================

class Banner(ThemeAware, QWidget):
    """Rounded semantic strip: icon · title · body · actions · dismiss.
    Slides open/closed (animated height)."""

    dismissed = Signal()

    def __init__(self, kind: str = "warning", parent=None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._anim = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SPACE.lg, SPACE.sm + 2, SPACE.sm, SPACE.sm + 2)
        lay.setSpacing(SPACE.md)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = label("", "body_strong")
        self.body = label("", tone="secondary")
        self.body.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.body)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(SPACE.sm)
        lay.addLayout(self.actions)
        self.close_btn = IconButton("close", "Dismiss", size=26)
        self.close_btn.clicked.connect(self._dismiss)
        lay.addWidget(self.close_btn, 0, Qt.AlignVCenter)
        self._refresh_icon()
        self._connect_theme()
        self.setVisible(False)

    def add_action(self, text: str, icon: Optional[str], cb: Callable, variant="secondary"):
        b = AnimatedButton(text, icon, variant, "sm")
        b.clicked.connect(cb)
        self.actions.addWidget(b)
        return b

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._refresh_icon()
        self.update()

    def _refresh_icon(self) -> None:
        sem = tokens().semantic(self._kind)
        self.icon.setPixmap(icons.pixmap({"danger": "danger", "info": "info", "success": "success"}
                                         .get(self._kind, "warning"), 20, sem.fg))

    def _on_theme_changed(self) -> None:
        self._refresh_icon()
        self.update()

    def show_message(self, title: str, body: str = "") -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        if self.isHidden():
            stop(self._anim)
            self.setVisible(True)
            target = self.sizeHint().height()
            self.setMaximumHeight(0)
            self._anim = animate_value(self, 0, target, MOTION.base,
                                       lambda v: self.setMaximumHeight(int(v)),
                                       on_finished=lambda: self.setMaximumHeight(16777215))

    def hide_banner(self) -> None:
        stop(self._anim)
        self.setVisible(False)

    def _dismiss(self) -> None:
        self.hide_banner()
        self.dismissed.emit()

    def paintEvent(self, _e) -> None:
        sem = tokens().semantic(self._kind)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(qcolor(sem.border), 1))
        p.setBrush(qcolor(sem.bg))
        p.drawRoundedRect(r, RADII.lg, RADII.lg)
        p.fillRect(QRectF(r.x() + 1, r.y() + 8, 3, r.height() - 16), qcolor(sem.solid))


class Swatch(QWidget):
    """Small colour chip (outline / legend)."""

    def __init__(self, color: str, size: int = 10, parent=None) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(self.rect()), 3, 3)


def swatch_icon(color: str, size: int = 14):
    from PySide6.QtGui import QIcon
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), 3, 3)
    p.end()
    return QIcon(pm)


__all__ = [
    "Paper", "CoverSlide", "ImageSlide", "TextSlide", "MethodsSlide", "OverviewSheet",
    "PerImageBars", "Banner", "Swatch", "swatch_icon", "bgr_pixmap", "file_pixmap",
    "arr_thumb_qimage", "file_thumb_qimage",
]
