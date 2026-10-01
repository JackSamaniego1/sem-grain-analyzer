"""
Launcher splash — animated grain microstructure (UPDATE 4 batch 4, item C / D-40).

A frameless top-level widget that paints, with QPainter only (no media files,
no network), a seeded Voronoi polycrystal of ~60 grains.  Each grain
"nucleates" at a staggered time and fades in with a subtle grey level (like
SEM channelling contrast); a grain boundary draws in from its midpoint once
both neighbouring grains exist.  One pass takes ``DURATION_MS`` (~1.2 s).

The animation runs on *rendered* time, not wall time: each timer tick adds
the real time since the previous tick, capped at ``MAX_TICK_MS``.  Time the
GUI thread spends blocked (e.g. building the main window) therefore does not
count, so the user always actually sees one full pass.  The splash closes
when BOTH the window is ready (:meth:`finish`) AND one rendered pass has
completed.  With ``GRAIN_REDUCED_MOTION`` the final frame is shown
immediately and there is no fade.

Geometry is computed in pure Python (half-plane clipping of the frame
rectangle per seed) — no numpy/scipy needed, ~60x60 clips, a few ms.
"""
from __future__ import annotations

import math
import random
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import (QEasingCurve, QElapsedTimer, QPointF, QPropertyAnimation,
                            QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QWidget

from ui.design.tokens import DARK, MOTION, TypeStyle

Point = Tuple[float, float]

DURATION_MS = 1200
MAX_TICK_MS = 50                          # cap per tick: blocked time does not count
SPLASH_W, SPLASH_H = 560, 300
_COLS, _ROWS = 10, 6                      # 60 grains
_SEED = 0x5E4                             # fixed: same microstructure every launch
_GRAIN_FADE = 0.30                        # fraction of the pass a grain takes to fade in
_EDGE_DRAW = 0.28                         # fraction of the pass a boundary takes to draw


# ---------------------------------------------------------------- geometry
def _clip(verts: List[Point], tags: List[int], a: float, b: float, c: float,
          tag: int) -> Tuple[List[Point], List[int]]:
    """Clip a convex polygon to the half-plane a*x + b*y <= c (Sutherland-
    Hodgman).  ``tags[k]`` labels edge verts[k] -> verts[k+1]; the new edge
    along the clip line gets ``tag``."""
    out_v: List[Point] = []
    out_t: List[int] = []
    n = len(verts)
    for k in range(n):
        p, q, tg = verts[k], verts[(k + 1) % n], tags[k]
        dp = a * p[0] + b * p[1] - c
        dq = a * q[0] + b * q[1] - c
        if dp <= 0:
            out_v.append(p)
            out_t.append(tg)
            if dq > 0:
                s = dp / (dp - dq)
                out_v.append((p[0] + s * (q[0] - p[0]), p[1] + s * (q[1] - p[1])))
                out_t.append(tag)
        elif dq <= 0:
            s = dp / (dp - dq)
            out_v.append((p[0] + s * (q[0] - p[0]), p[1] + s * (q[1] - p[1])))
            out_t.append(tg)
    return out_v, out_t


def build_microstructure(w: float = SPLASH_W, h: float = SPLASH_H, cols: int = _COLS,
                         rows: int = _ROWS, seed: int = _SEED):
    """Seeded Voronoi polycrystal clipped to (0, 0, w, h).

    Returns ``(grains, edges)``: ``grains`` is a list of dicts with
    ``poly`` (vertex list), ``seed``, ``start`` (0..1 nucleation time) and
    ``tone`` (0..1 grey level); ``edges`` is a list of unique interior
    boundaries ``(x1, y1, x2, y2, start)``."""
    rng = random.Random(seed)
    cw, ch = w / cols, h / rows
    seeds = [((c + 0.5 + rng.uniform(-0.38, 0.38)) * cw,
              (r + 0.5 + rng.uniform(-0.38, 0.38)) * ch)
             for r in range(rows) for c in range(cols)]
    # nucleation front sweeps from the right (behind the text scrim it ends)
    ox, oy = w * 1.05, h * 0.45
    dmax = max(math.hypot(sx - ox, sy - oy) for sx, sy in seeds) or 1.0
    span = 1.0 - max(_GRAIN_FADE, _EDGE_DRAW)
    grains = []
    for i, (sx, sy) in enumerate(seeds):
        verts: List[Point] = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
        tags = [-1, -1, -1, -1]
        for j, (tx, ty) in enumerate(seeds):
            if j == i or (abs(tx - sx) > 3 * cw or abs(ty - sy) > 3 * ch):
                continue
            a, b = tx - sx, ty - sy
            c = a * (sx + tx) / 2 + b * (sy + ty) / 2
            verts, tags = _clip(verts, tags, a, b, c, j)
            if not verts:
                break
        d = math.hypot(sx - ox, sy - oy) / dmax
        start = max(0.0, min(span, span * (0.85 * d + 0.15 * rng.random())))
        grains.append({"poly": verts, "tags": tags, "seed": (sx, sy),
                       "start": start, "tone": rng.random()})
    starts = [g["start"] for g in grains]
    lo, hi = min(starts), max(starts)
    for g in grains:                      # normalise so the pass ends exactly at 1.0
        g["start"] = span * (g["start"] - lo) / ((hi - lo) or 1.0)
    edges = []
    for i, g in enumerate(grains):
        v, t = g["poly"], g["tags"]
        for k, j in enumerate(t):
            if j > i:                     # interior boundary, emit once
                p, q = v[k], v[(k + 1) % len(v)]
                if math.hypot(q[0] - p[0], q[1] - p[1]) < 0.5:
                    continue
                edges.append((p[0], p[1], q[0], q[1],
                              max(g["start"], grains[j]["start"])))
    return grains, edges


def _ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _mix(a: QColor, b: QColor, f: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * f,
                           a.greenF() + (b.greenF() - a.greenF()) * f,
                           a.blueF() + (b.blueF() - a.blueF()) * f)


# ---------------------------------------------------------------- widget
class GrainSplash(QWidget):
    """Animated launcher splash.  Use :meth:`set_progress` / :meth:`set_message`
    while loading, then :meth:`finish` once the main window is built."""

    closed = Signal()
    pass_completed = Signal()

    def __init__(self, title: str, version: str, publisher: str = "",
                 reduced_motion: Optional[bool] = None, parent=None):
        super().__init__(parent, Qt.SplashScreen | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        if reduced_motion is None:
            from ui.design.theme import reduced_motion as _rm
            reduced_motion = _rm()
        self.setObjectName("GrainSplash")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setAccessibleName(f"{title} is starting")
        self.setFixedSize(SPLASH_W, SPLASH_H)
        self._title, self._version, self._publisher = title, version, publisher
        self._reduced = bool(reduced_motion)
        self._t = DARK          # splash is always the dark instrument look
        self._grains, self._edges = build_microstructure()
        self._polys = [QPolygonF([QPointF(x, y) for x, y in g["poly"]]) for g in self._grains]
        self._message = "Starting…"
        self._progress = 0.0                  # displayed (eased)
        self._progress_target = 0.0
        self._ready = False
        self._closing = False
        self._on_close: Optional[Callable[[], None]] = None
        self._fade: Optional[QPropertyAnimation] = None
        self._clock = QElapsedTimer()         # time since the previous tick
        self._rendered_ms = 0.0               # animation time actually rendered
        self._pass_done = self._reduced
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._center_on_screen()

    # ---- public API
    @property
    def reduced_motion(self) -> bool:
        return self._reduced

    @property
    def pass_done(self) -> bool:
        """True once one full animation pass has played (always, in reduced motion)."""
        return self._pass_done

    @property
    def is_ready(self) -> bool:
        return self._ready

    def anim_t(self) -> float:
        """Current animation position 0..1 (rendered time, see module doc)."""
        if self._pass_done:
            return 1.0
        return min(1.0, self._rendered_ms / DURATION_MS)

    def advance(self, dt_ms: float) -> None:
        """Advance the animation clock by ``dt_ms`` of real time, capped at
        ``MAX_TICK_MS`` so a blocked GUI thread does not skip the animation."""
        self._rendered_ms += max(0.0, min(float(dt_ms), MAX_TICK_MS))

    def set_progress(self, value: float) -> None:
        """Loading progress 0..1 shown by the thin line (eased unless reduced motion)."""
        self._progress_target = max(self._progress_target, max(0.0, min(1.0, float(value))))
        if self._reduced:
            self._progress = self._progress_target
        self._ensure_timer()
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    def finish(self, on_close: Optional[Callable[[], None]] = None) -> None:
        """The main window is ready.  The splash closes (calling ``on_close``
        first, e.g. ``window.show``) as soon as one pass has also finished."""
        self._ready = True
        self._on_close = on_close
        self.set_progress(1.0)
        self._maybe_close()

    def render_frame(self, t: float, progress: Optional[float] = None) -> QImage:
        """Paint the splash at animation position ``t`` into an image (tests/docs)."""
        img = QImage(SPLASH_W, SPLASH_H, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(self._t.surface.bg))
        p = QPainter(img)
        self._paint(p, t, self._progress if progress is None else progress)
        p.end()
        return img

    # ---- Qt
    def showEvent(self, e):
        super().showEvent(e)
        if not self._clock.isValid():
            self._clock.start()
        self._ensure_timer()

    def paintEvent(self, _e):
        p = QPainter(self)
        self._paint(p, self.anim_t(), self._progress)
        p.end()

    # ---- internals
    def _center_on_screen(self) -> None:
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - SPLASH_W // 2, g.center().y() - SPLASH_H // 2)

    def _ensure_timer(self) -> None:
        if not self._reduced and self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        if self._clock.isValid():
            self.advance(self._clock.restart())
        else:
            self._clock.start()
        if not self._pass_done and self.anim_t() >= 1.0:
            self._pass_done = True
            self.pass_completed.emit()
        # ease the progress line toward its target (~150 ms time constant)
        diff = self._progress_target - self._progress
        self._progress = self._progress_target if abs(diff) < 0.002 else self._progress + diff * 0.18
        self.update()
        if self._pass_done:
            self._maybe_close()
            if self._progress == self._progress_target and not self._closing:
                self._timer.stop()

    def _maybe_close(self) -> None:
        if self._closing or not (self._ready and self._pass_done):
            return
        self._closing = True
        cb, self._on_close = self._on_close, None
        if cb is not None:
            cb()
        if self._reduced or not self.isVisible():
            self._done()
            return
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(MOTION.fast)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._done)
        self._fade.start()

    def _done(self) -> None:
        self._timer.stop()
        self.close()
        self.closed.emit()

    def _paint(self, p: QPainter, t: float, progress: float) -> None:
        from ui.design.theme import ui_font
        tk = self._t
        w, h = SPLASH_W, SPLASH_H
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(tk.surface.bg)
        p.fillRect(QRectF(0, 0, w, h), bg)

        # grains: SEM-like grey levels between surface1 and surface3
        lo, hi = QColor(tk.surface.surface1), QColor(tk.border.strong)
        p.setPen(Qt.NoPen)
        for g, poly in zip(self._grains, self._polys):
            a = _ease_out((t - g["start"]) / _GRAIN_FADE)
            if a <= 0.0:
                continue
            c = _mix(lo, hi, 0.10 + 0.75 * g["tone"])
            c.setAlphaF(a)
            p.setBrush(c)
            p.drawPolygon(poly)

        # boundaries: draw out from the midpoint once both grains exist
        edge = QColor(tk.accent.text)
        edge.setAlphaF(0.55)
        pen = QPen(edge, 1.1)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        path = QPainterPath()
        for x1, y1, x2, y2, s in self._edges:
            f = _ease_out((t - s) / _EDGE_DRAW)
            if f <= 0.0:
                continue
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            path.moveTo(mx + (x1 - mx) * f, my + (y1 - my) * f)
            path.lineTo(mx + (x2 - mx) * f, my + (y2 - my) * f)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # scrim so the text reads at >= 4.5:1 regardless of the grains
        sc = QLinearGradient(0, 0, w, 0)
        c0 = QColor(bg); c0.setAlphaF(0.96)
        c1 = QColor(bg); c1.setAlphaF(0.90)
        c2 = QColor(bg); c2.setAlphaF(0.10)
        sc.setColorAt(0.0, c0); sc.setColorAt(0.40, c1); sc.setColorAt(0.80, c2)
        sc.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, w, h), sc)
        bot = QLinearGradient(0, h - 70, 0, h)
        b0 = QColor(bg); b0.setAlphaF(0.0)
        b1 = QColor(bg); b1.setAlphaF(0.92)
        bot.setColorAt(0.0, b0); bot.setColorAt(1.0, b1)
        p.fillRect(QRectF(0, h - 70, w, 70), bot)

        # text block
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tk.accent.base))
        p.drawRoundedRect(QRectF(40, 52, 4, 64), 2, 2)
        p.setPen(QColor(tk.text.primary))
        p.setFont(ui_font(TypeStyle(30, 600, 38, -0.5)))
        p.drawText(QRectF(60, 48, 460, 44), Qt.AlignLeft | Qt.AlignVCenter, self._title)
        p.setPen(QColor(tk.text.secondary))
        p.setFont(ui_font(TypeStyle(14, 400, 20)))
        p.drawText(QRectF(60, 92, 460, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   "SEM grain detection · measurement · reporting")
        p.setPen(QColor(tk.text.tertiary))
        p.setFont(ui_font(TypeStyle(12, 400, 16)))
        ver = f"Version {self._version}" + (f"  ·  {self._publisher}" if self._publisher else "")
        p.drawText(QRectF(40, h - 70, 480, 18), Qt.AlignLeft | Qt.AlignVCenter, ver)
        p.drawText(QRectF(40, h - 50, 480, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   "Works fully offline — your data stays on this computer")
        p.setPen(QColor(tk.text.secondary))
        p.setFont(ui_font(TypeStyle(11, 400, 16)))
        p.drawText(QRectF(w - 40 - 220, h - 70, 220, 18), Qt.AlignRight | Qt.AlignVCenter,
                   self._message)

        # thin progress line
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tk.surface.surface3))
        p.drawRoundedRect(QRectF(40, h - 24, w - 80, 2), 1, 1)
        if progress > 0:
            p.setBrush(QColor(tk.accent.text))
            p.drawRoundedRect(QRectF(40, h - 24, (w - 80) * progress, 2), 1, 1)

        # hairline frame
        p.setPen(QPen(QColor(tk.border.strong), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, w - 1, h - 1))
