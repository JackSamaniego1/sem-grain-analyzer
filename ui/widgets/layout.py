"""Responsive layout helpers (FIX-09): toolbars that wrap instead of
overlapping, and titles that wrap onto a second line instead of eliding
mid-word.

* :class:`ResponsiveToolbar` -- a lead widget (image title) plus groups of
  controls.  When everything fits it is one row: lead left, groups
  right-aligned.  When it does not, groups that do not fit move to extra
  rows (left-aligned) -- never overlapping, never clipped.
* :func:`wrap_elide` / :class:`WrapLabel` -- wrap text over at most
  ``max_lines`` lines (long unbreakable words are broken by character);
  the last line elides with "…".  The full text stays in the tooltip.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ui.design.tokens import SPACE


# ======================================================================
# wrap + elide
# ======================================================================

def _break_word(word: str, fm: QFontMetrics, width: int) -> List[str]:
    """Split a word wider than ``width`` into character chunks that fit."""
    out, cur = [], ""
    for ch in word:
        if cur and fm.horizontalAdvance(cur + ch) > width:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def wrap_elide(text: str, fm: QFontMetrics, width: int, max_lines: int = 2) -> List[str]:
    """Greedy word wrap of ``text`` to ``width`` px, at most ``max_lines``
    lines; the last line is right-elided when text remains."""
    width = max(8, int(width))
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    cur = ""
    tokens: List[tuple] = []            # (text, glue before it: " " or "" in a broken word)
    for w in words:
        parts = [w] if fm.horizontalAdvance(w) <= width else _break_word(w, fm, width)
        tokens += [(p, " " if j == 0 else "") for j, p in enumerate(parts)]
    for i, (w, glue) in enumerate(tokens):
        cand = f"{cur}{glue}{w}" if cur else w
        if not cur or (glue and fm.horizontalAdvance(cand) <= width):
            cur = cand
            continue
        if len(lines) == max_lines - 1:
            # ``cur`` is the last allowed line: it takes everything left, elided
            rest = cur + "".join(g + t for t, g in tokens[i:])
            lines.append(fm.elidedText(rest, Qt.ElideRight, width))
            return lines
        lines.append(cur)
        cur = w
    lines.append(cur)
    return lines


class WrapLabel(QLabel):
    """Label that wraps over up to ``max_lines`` lines and elides the rest;
    the full text is always the tooltip (and accessible name)."""

    def __init__(self, text: str = "", role: Optional[str] = None, max_lines: int = 2,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = ""
        self._max = max(1, int(max_lines))
        self._lines = 1
        if role:
            self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(48)
        self.set_full_text(text)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self.setAccessibleName(self._full)
        self._rewrap()

    def shown_lines(self) -> List[str]:
        return self.text().split("\n")

    def _rewrap(self) -> None:
        w = self.width() if self.width() > 20 else 10_000
        lines = wrap_elide(self._full, self.fontMetrics(), w, self._max)
        self.setText("\n".join(lines))
        if len(lines) != self._lines:
            self._lines = len(lines)
            self.updateGeometry()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full) + 2, fm.lineSpacing() * self._lines + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(48, self.fontMetrics().lineSpacing() * self._lines + 2)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._rewrap()

    def changeEvent(self, e) -> None:          # font / style change -> re-measure
        super().changeEvent(e)
        if e.type() in (e.Type.FontChange, e.Type.StyleChange):
            self._rewrap()


# ======================================================================
# responsive toolbar
# ======================================================================

def group(*widgets: QWidget, spacing: int = SPACE.xs) -> QWidget:
    """Wrap controls into one toolbar group (they always stay together)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for x in widgets:
        h.addWidget(x)
    w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return w


class ResponsiveToolbar(QWidget):
    """Lead widget + control groups that wrap onto extra rows when narrow."""

    def __init__(self, lead: Optional[QWidget] = None, groups: Sequence[QWidget] = (),
                 lead_min: int = 140, spacing: int = SPACE.sm, row_gap: int = SPACE.xs,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lead = lead
        self.groups: List[QWidget] = []
        self._lead_min = lead_min
        self._sp = spacing
        self._row_gap = row_gap
        self._rows: List[List[QWidget]] = []
        self._height = 0
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        if lead is not None:
            lead.setParent(self)
        for g in groups:
            self.add_group(g)

    def add_group(self, g: QWidget) -> QWidget:
        g.setParent(self)
        self.groups.append(g)
        self._relayout()
        return g

    def row_count(self) -> int:
        return max(1, len(self._rows))

    def rows(self) -> List[List[QWidget]]:
        """Visible groups per row (row 0 also holds the lead)."""
        return [list(r) for r in self._rows]

    # ---- geometry
    def _gw(self, g: QWidget) -> int:
        return g.sizeHint().expandedTo(g.minimumSizeHint()).width()

    def _gh(self, g: QWidget) -> int:
        return g.sizeHint().expandedTo(g.minimumSizeHint()).height()

    def _plan(self, width: int) -> List[List[QWidget]]:
        vis = [g for g in self.groups if not g.isHidden()]
        rows: List[List[QWidget]] = [[]]
        avail = width - (self._lead_min + self._sp if self.lead is not None else 0)
        used = 0
        for g in vis:
            gw = self._gw(g)
            need = gw + (self._sp if rows[-1] else 0)
            if rows[-1] and used + need > avail:
                rows.append([])
                avail, used, need = width, 0, gw
            elif not rows[-1] and len(rows) == 1 and gw > avail:
                # not even the first group fits beside the lead: own row
                rows.append([])
                avail, used, need = width, 0, gw
            rows[-1].append(g)
            used += need
        return rows

    def _row_h(self, row: List[QWidget], first: bool) -> int:
        h = max([self._gh(g) for g in row] or [0])
        if first and self.lead is not None:
            h = max(h, self.lead.sizeHint().height())
        return h

    def _total_h(self, rows) -> int:
        hs = [self._row_h(r, i == 0) for i, r in enumerate(rows)]
        hs = [h for h in hs if h] or [0]
        return sum(hs) + self._row_gap * (len(hs) - 1)

    def sizeHint(self) -> QSize:
        w = sum(self._gw(g) + self._sp for g in self.groups if not g.isHidden())
        w += self._lead_min if self.lead is not None else 0
        return QSize(w, self._height or self._total_h(self._plan(10 ** 6)))

    def minimumSizeHint(self) -> QSize:
        w = max([self._gw(g) for g in self.groups if not g.isHidden()] + [self._lead_min])
        return QSize(w, self._height or self._total_h(self._plan(10 ** 6)))

    def _relayout(self) -> None:
        width = self.width() if self.width() > 0 else self.sizeHint().width()
        rows = self._plan(width)
        self._rows = rows
        y = 0
        for i, row in enumerate(rows):
            rh = self._row_h(row, i == 0)
            if i == 0:
                right = width
                for g in reversed(row):
                    gw = self._gw(g)
                    right -= gw
                    g.setGeometry(QRect(right, y + (rh - self._gh(g)) // 2, gw, self._gh(g)))
                    right -= self._sp
                if self.lead is not None:
                    lw = max(0, right)
                    lh = min(rh, max(self.lead.sizeHint().height(), 1))
                    self.lead.setGeometry(QRect(0, y + (rh - lh) // 2, lw, lh))
            else:
                x = 0
                for g in row:
                    gw = self._gw(g)
                    g.setGeometry(QRect(x, y + (rh - self._gh(g)) // 2, gw, self._gh(g)))
                    x += gw + self._sp
            if rh:
                y += rh + self._row_gap
        h = max(0, y - self._row_gap)
        if h != self._height:
            self._height = h
            self.setFixedHeight(max(h, 1))
            self.updateGeometry()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._relayout()

    def event(self, e) -> bool:
        if e.type() == e.Type.LayoutRequest:     # a group changed size / visibility
            self._relayout()
        return super().event(e)


__all__ = ["ResponsiveToolbar", "WrapLabel", "group", "wrap_elide"]
