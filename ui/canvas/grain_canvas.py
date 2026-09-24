"""
GrainCanvas — the v3 image viewer used by the Analyze and Review pages.

* views: Original / Overlay / Mask / Excluded (+ optional excluded-region
  tint on top of any view, DET-09)
* smooth wheel zoom about the cursor, fit, 1:1, drag to pan (left-drag on
  the image, middle-drag anywhere), minimap in the corner when zoomed in
* hover tooltip with grain id / area / ECD / circularity
* click to select (animated highlight), Ctrl+click multi-select,
  Delete emits ``delete_requested`` (the page pushes an undo command)
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.canvas.layers import (
    desaturate, excluded_layer, grain_mask_image, grain_outline, overlay_layer,
)
from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import MOTION, RADII, TYPE
from ui.format import area_value, diam_value, smart_format, units_for
from ui.widgets._base import ThemeAware, animate_value, qcolor, stop, tokens
from ui.workers import bgr_to_qimage

VIEWS = ("original", "overlay", "mask", "excluded")
VIEW_LABELS = {"original": "Original", "overlay": "Overlay", "mask": "Mask",
               "excluded": "Excluded"}


class GrainCanvas(ThemeAware, QWidget):
    selection_changed = Signal(list)   # grain ids
    delete_requested = Signal(list)    # grain ids
    hovered = Signal(int)              # grain id or 0
    zoom_changed = Signal(float)       # scale (1.0 = 100 %)
    view_changed = Signal(str)

    MIN_SCALE = 0.02
    MAX_SCALE = 32.0
    MINIMAP_W = 168

    def __init__(self, parent: Optional[QWidget] = None, interactive: bool = True,
                 placeholder: str = "No image") -> None:
        super().__init__(parent)
        self._interactive = interactive
        self._placeholder = placeholder
        self._bgr: Optional[np.ndarray] = None
        self._result = None
        self._raw = None
        self._pm: Dict[str, QPixmap] = {}
        self._view = "original"
        self._show_excluded = False
        self._scan_rect = None
        self._info_bar_rect = None      # DET-05: detected SEM data bar (never analysed)
        self._grains: Dict[int, object] = {}
        self._labels = None
        self._excluded: Dict[int, list] = {}
        self._show_ex_grains = True
        self._selected: List[int] = []
        self._sel_paths: Dict[int, QPainterPath] = {}
        self._hover_id = 0
        self._hover_path: Optional[QPainterPath] = None
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self._fit_mode = True
        self._press_pos: Optional[QPointF] = None
        self._press_offset = QPointF()
        self._panning = False
        self._mini_drag = False
        self._mouse = QPointF(-1, -1)
        self._pulse = 1.0
        self._anims = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 240)
        self.setAccessibleName("Image canvas")
        self.setToolTip("")
        self._connect_theme()

    # ------------------------------------------------------------------ API
    def sizeHint(self) -> QSize:
        return QSize(900, 680)

    def has_image(self) -> bool:
        return self._bgr is not None

    def image_size(self) -> QSize:
        if self._bgr is None:
            return QSize()
        h, w = self._bgr.shape[:2]
        return QSize(w, h)

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def set_image(self, bgr: Optional[np.ndarray], result=None, keep_view: bool = True,
                  raw=None, excluded=None) -> None:
        """Show a new image (and optionally its result); refits."""
        same_size = (self._bgr is not None and bgr is not None
                     and self._bgr.shape[:2] == bgr.shape[:2])
        self._bgr = bgr
        self._pm = {}
        if bgr is not None:
            self._pm["original"] = QPixmap.fromImage(bgr_to_qimage(bgr))
        self._selected, self._sel_paths = [], {}
        self._hover_id, self._hover_path = 0, None
        self._set_result(result, raw, excluded)
        if not keep_view:
            self._view = "overlay" if result is not None else "original"
        if not same_size or self._fit_mode:
            self.fit(animate=False)
        self.update()

    def set_result(self, result, raw=None, excluded=None) -> None:
        """Attach/replace the (filtered) result; ``raw`` supplies the full
        label image so ``excluded`` grains can be shown greyed + explained."""
        keep = [g for g in self._selected]
        self._set_result(result, raw, excluded)
        self._selected = [g for g in keep if g in self._grains and g not in self._excluded]
        self._rebuild_sel_paths()
        self.update()

    def refresh_result(self) -> None:
        """Rebuild layers after the result was edited in place (undo/redo)."""
        self.set_result(self._result, self._raw, self._excluded)
        self.selection_changed.emit(list(self._selected))

    def excluded(self) -> Dict[int, list]:
        return dict(self._excluded)

    def set_show_excluded_grains(self, on: bool) -> None:
        self._show_ex_grains = bool(on)
        self._pm.pop("overlay", None)
        self._ensure_layer(self._view)
        self.update()

    def result(self):
        return self._result

    def view(self) -> str:
        return self._view

    def set_view(self, view: str) -> None:
        if view not in VIEWS or view == self._view:
            return
        self._view = view
        self._ensure_layer(view)
        self.view_changed.emit(view)
        self.update()

    def set_show_excluded_regions(self, on: bool) -> None:
        self._show_excluded = bool(on)
        self._ensure_layer("excluded_layer")
        self.update()

    def show_excluded_regions(self) -> bool:
        return self._show_excluded

    def set_scan_rect(self, rect) -> None:
        self._scan_rect = tuple(rect) if rect else None
        self.update()

    def set_info_bar_rect(self, rect) -> None:
        """Shade the detected SEM info bar (hatched: "not analysed")."""
        r = tuple(int(v) for v in rect) if rect else None
        if r != self._info_bar_rect:
            self._info_bar_rect = r
            self.update()

    def info_bar_rect(self):
        return self._info_bar_rect

    def selected(self) -> List[int]:
        return list(self._selected)

    def select(self, ids, center: bool = False, emit: bool = True) -> None:
        ids = [int(i) for i in ids if int(i) in self._grains and int(i) not in self._excluded]
        self._selected = ids
        self._rebuild_sel_paths()
        self._start_pulse()
        if center and ids:
            g = self._grains[ids[0]]
            self.center_on(g.centroid_x, g.centroid_y)
        if emit:
            self.selection_changed.emit(list(ids))
        self.update()

    def clear_selection(self) -> None:
        if self._selected:
            self.select([])

    def scale(self) -> float:
        return self._scale

    def fit(self, animate: bool = True) -> None:
        self._fit_mode = True
        s, off = self._fit_params()
        self._animate_to(s, off, animate)

    def actual_size(self) -> None:
        if self._bgr is None:
            return
        self._fit_mode = False
        c = QPointF(self.width() / 2, self.height() / 2)
        self._zoom_to(1.0, c, animate=True)

    def zoom_by(self, factor: float, anchor: Optional[QPointF] = None) -> None:
        if self._bgr is None:
            return
        self._fit_mode = False
        anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        self._zoom_to(self._scale * factor, anchor, animate=False)

    def center_on(self, ix: float, iy: float) -> None:
        if self._bgr is None:
            return
        self._fit_mode = False
        if self._scale < self._fit_params()[0] * 1.5:
            self._scale = min(self.MAX_SCALE, self._fit_params()[0] * 2.5)
            self.zoom_changed.emit(self._scale)
        self._offset = QPointF(self.width() / 2 - ix * self._scale,
                               self.height() / 2 - iy * self._scale)
        self.update()

    def widget_to_image(self, p: QPointF) -> Optional[tuple]:
        if self._bgr is None:
            return None
        x = (p.x() - self._offset.x()) / self._scale
        y = (p.y() - self._offset.y()) / self._scale
        h, w = self._bgr.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            return int(x), int(y)
        return None

    def grain_at(self, p: QPointF) -> int:
        lab = self._labels
        pt = self.widget_to_image(p)
        if pt is None or lab is None or lab.shape[:2] != self._bgr.shape[:2]:
            return 0
        gid = int(lab[pt[1], pt[0]])
        return gid if gid in self._grains else 0

    def rendered_image(self) -> QImage:
        """What the current view shows at 1:1 (tests / export)."""
        pm = self._view_pixmap()
        return pm.toImage() if pm is not None else QImage()

    # ------------------------------------------------------------------ internals
    def _set_result(self, result, raw=None, excluded=None) -> None:
        self._result = result
        self._raw = raw
        self._excluded = {int(k): list(v) for k, v in (excluded or {}).items()}
        src = raw if raw is not None else result
        self._labels = getattr(src, "label_image", None) if src is not None else None
        self._grains = {int(g.grain_id): g for g in (src.grains if src else [])}
        for k in ("overlay", "mask", "excluded", "excluded_layer", "desat"):
            self._pm.pop(k, None)
        if self._hover_id and self._hover_id not in self._grains:
            self._hover_id, self._hover_path = 0, None
        self._ensure_layer(self._view)
        if self._show_excluded:
            self._ensure_layer("excluded_layer")

    def _label_ok(self) -> bool:
        lab = self._labels
        return (lab is not None and self._bgr is not None
                and lab.shape[:2] == self._bgr.shape[:2])

    def _ensure_layer(self, key: str) -> None:
        if key in self._pm or self._bgr is None:
            return
        r = self._result
        if key == "overlay" and self._label_ok():
            self._pm["overlay"] = QPixmap.fromImage(
                overlay_layer(self._labels, list(self._excluded), self._show_ex_grains))
        elif key == "mask" and r is not None:
            m = grain_mask_image(r, self._labels if self._label_ok() else None,
                                 list(self._excluded))
            if m is not None and m.shape[:2] == self._bgr.shape[:2]:
                self._pm["mask"] = QPixmap.fromImage(bgr_to_qimage(m))
        elif key in ("excluded", "excluded_layer"):
            vm = getattr(r, "valid_mask", None) if r is not None else None
            if "excluded_layer" not in self._pm:
                if vm is not None and vm.shape[:2] == self._bgr.shape[:2]:
                    self._pm["excluded_layer"] = QPixmap.fromImage(excluded_layer(vm))
                else:
                    self._pm["excluded_layer"] = QPixmap()
            if key == "excluded" and "desat" not in self._pm:
                self._pm["desat"] = QPixmap.fromImage(bgr_to_qimage(desaturate(self._bgr)))

    def _view_pixmap(self) -> Optional[QPixmap]:
        if self._bgr is None:
            return None
        if self._view == "mask":
            self._ensure_layer("mask")
            return self._pm.get("mask") or self._pm.get("original")
        if self._view == "excluded":
            self._ensure_layer("excluded")
            return self._pm.get("desat") or self._pm.get("original")
        return self._pm.get("original")

    def _rebuild_sel_paths(self) -> None:
        self._sel_paths = {}
        if not self._label_ok():
            return
        lab = self._labels
        for gid in self._selected:
            g = self._grains.get(gid)
            if g is not None:
                self._sel_paths[gid] = self._contour_path(lab, g)

    @staticmethod
    def _contour_path(lab, g) -> QPainterPath:
        path = QPainterPath()
        for c in grain_outline(lab, g):
            if len(c) < 2:
                continue
            path.moveTo(float(c[0][0]) + 0.5, float(c[0][1]) + 0.5)
            for x, y in c[1:]:
                path.lineTo(float(x) + 0.5, float(y) + 0.5)
            path.closeSubpath()
        return path

    def _start_pulse(self) -> None:
        stop(self._anims.get("pulse"))
        self._pulse = 0.0
        self._anims["pulse"] = animate_value(self, 0.0, 1.0, 700, self._set_pulse,
                                             curve=MOTION.ease_out)

    def _set_pulse(self, v) -> None:
        self._pulse = float(v)
        self.update()

    def _fit_params(self):
        if self._bgr is None:
            return 1.0, QPointF(0, 0)
        h, w = self._bgr.shape[:2]
        ww, wh = max(1, self.width()), max(1, self.height())
        s = min(ww / w, wh / h) * 0.96
        s = max(self.MIN_SCALE, s)
        off = QPointF((ww - w * s) / 2, (wh - h * s) / 2)
        return s, off

    def _zoom_to(self, new_scale: float, anchor: QPointF, animate: bool) -> None:
        fit_s = self._fit_params()[0]
        new_scale = max(min(fit_s * 0.5, 1.0), min(self.MAX_SCALE, new_scale))
        k = new_scale / self._scale
        off = QPointF(anchor.x() - (anchor.x() - self._offset.x()) * k,
                      anchor.y() - (anchor.y() - self._offset.y()) * k)
        self._animate_to(new_scale, off, animate)

    def _animate_to(self, s: float, off: QPointF, animate: bool) -> None:
        stop(self._anims.get("zoom"))
        s0, o0 = self._scale, QPointF(self._offset)
        if not animate or not self.isVisible():
            self._apply_view(s, off)
            return

        def step(t):
            t = float(t)
            self._apply_view(s0 + (s - s0) * t,
                             QPointF(o0.x() + (off.x() - o0.x()) * t,
                                     o0.y() + (off.y() - o0.y()) * t))
        self._anims["zoom"] = animate_value(self, 0.0, 1.0, MOTION.base, step,
                                            curve=MOTION.ease_out)

    def _apply_view(self, s: float, off: QPointF) -> None:
        changed = abs(s - self._scale) > 1e-9
        self._scale, self._offset = s, off
        if changed:
            self.zoom_changed.emit(s)
        self.update()

    def _image_rect_on_widget(self) -> QRectF:
        if self._bgr is None:
            return QRectF()
        h, w = self._bgr.shape[:2]
        return QRectF(self._offset.x(), self._offset.y(), w * self._scale, h * self._scale)

    def _minimap_rect(self) -> Optional[QRectF]:
        if self._bgr is None:
            return None
        img = self._image_rect_on_widget()
        view = QRectF(self.rect())
        if view.contains(img.adjusted(1, 1, -1, -1)):
            return None
        h, w = self._bgr.shape[:2]
        mw = self.MINIMAP_W
        mh = mw * h / w
        if mh > 140:
            mh = 140
            mw = mh * w / h
        return QRectF(self.width() - mw - 14, self.height() - mh - 14, mw, mh)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(t.surface.bg))
        pm = self._view_pixmap()
        if pm is None or pm.isNull():
            self._paint_placeholder(p, t)
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, self._scale < 2.0)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.save()
        p.translate(self._offset)
        p.scale(self._scale, self._scale)
        p.drawPixmap(0, 0, pm)
        if self._view == "overlay":
            ov = self._pm.get("overlay")
            if ov is not None and not ov.isNull():
                p.drawPixmap(0, 0, ov)
        if self._view == "excluded" or self._show_excluded:
            self._ensure_layer("excluded_layer")
            ex = self._pm.get("excluded_layer")
            if ex is not None and not ex.isNull():
                p.drawPixmap(0, 0, ex)
        if self._info_bar_rect:
            x, y, w, h = self._info_bar_rect
            shade = QColor(qcolor(t.surface.bg))
            shade.setAlphaF(0.45)
            p.fillRect(QRectF(x, y, w, h), shade)
            hatch = QColor(qcolor(t.text.tertiary))
            hatch.setAlphaF(0.55)
            br = QBrush(hatch, Qt.BDiagPattern)
            tr = QTransform()
            tr.scale(1.0 / max(self._scale, 1e-6), 1.0 / max(self._scale, 1e-6))
            br.setTransform(tr)
            p.fillRect(QRectF(x, y, w, h), br)
        if self._scan_rect:
            x, y, w, h = self._scan_rect
            pen = QPen(qcolor(t.accent.text), 1.5, Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x, y, w, h))
        if self._hover_path is not None and self._hover_id not in self._selected:
            pen = QPen(QColor(255, 255, 255, 220), 1.4)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QColor(255, 255, 255, 36))
            p.drawPath(self._hover_path)
        if self._sel_paths:
            acc = qcolor(t.accent.text)
            glow = QColor(acc)
            glow.setAlphaF(0.45 * (1.0 - self._pulse) + 0.35)
            gpen = QPen(glow, 5.0 + 9.0 * (1.0 - self._pulse))
            gpen.setCosmetic(True)
            gpen.setJoinStyle(Qt.RoundJoin)
            fill = QColor(acc)
            fill.setAlphaF(0.30)
            spen = QPen(QColor(255, 255, 255), 2.0)
            spen.setCosmetic(True)
            for path in self._sel_paths.values():
                p.setPen(gpen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                p.setPen(spen)
                p.setBrush(fill)
                p.drawPath(path)
        p.restore()
        self._paint_hud(p, t)
        self._paint_minimap(p, t)
        self._paint_tooltip(p, t)

    def _paint_placeholder(self, p: QPainter, t) -> None:
        r = QRectF(self.rect())
        ic = icons.pixmap("image", 44, t.text.tertiary)
        p.drawPixmap(int(r.center().x() - 22), int(r.center().y() - 46), ic)
        p.setPen(qcolor(t.text.tertiary))
        p.setFont(ui_font(TYPE.body))
        p.drawText(QRectF(r.left(), r.center().y() + 8, r.width(), 40),
                   Qt.AlignHCenter | Qt.AlignTop, self._placeholder)

    def _pill(self, p: QPainter, t, rect: QRectF) -> None:
        bg = qcolor(t.surface.elevated)
        bg.setAlphaF(0.92)
        p.setPen(QPen(qcolor(t.border.strong), 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, RADII.md, RADII.md)

    def _paint_hud(self, p: QPainter, t) -> None:
        f = ui_font(TYPE.caption)
        p.setFont(f)
        fm = QFontMetricsF(f)
        txt = f"{self._scale * 100:.0f} %"
        if self._view != "original":
            txt = f"{VIEW_LABELS.get(self._view, '')}  ·  {txt}"
        if self._selected:
            n = len(self._selected)
            txt += f"  ·  {n} selected"
        w = fm.horizontalAdvance(txt) + 20
        r = QRectF(12, 12, w, 24)
        self._pill(p, t, r)
        p.setPen(qcolor(t.text.secondary))
        p.drawText(r, Qt.AlignCenter, txt)

    def _paint_minimap(self, p: QPainter, t) -> None:
        mr = self._minimap_rect()
        if mr is None:
            return
        pm = self._view_pixmap()
        self._pill(p, t, mr.adjusted(-4, -4, 4, 4))
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(mr, pm, QRectF(pm.rect()))
        if self._view == "overlay" and self._pm.get("overlay") is not None:
            ov = self._pm["overlay"]
            if not ov.isNull():
                p.drawPixmap(mr, ov, QRectF(ov.rect()))
        h, w = self._bgr.shape[:2]
        k = mr.width() / w
        tl = QPointF(-self._offset.x() / self._scale, -self._offset.y() / self._scale)
        vw, vh = self.width() / self._scale, self.height() / self._scale
        vr = QRectF(mr.left() + tl.x() * k, mr.top() + tl.y() * k, vw * k, vh * k)
        vr = vr.intersected(mr)
        acc = qcolor(t.accent.text)
        p.setPen(QPen(acc, 1.5))
        fill = QColor(acc)
        fill.setAlphaF(0.12)
        p.setBrush(fill)
        p.drawRect(vr)

    def _paint_tooltip(self, p: QPainter, t) -> None:
        if not self._interactive or not self._hover_id or self._panning:
            return
        g = self._grains.get(self._hover_id)
        if g is None:
            return
        r = self._result
        au, _, du, _ = units_for(r)
        lines = [(f"Grain #{g.grain_id}", ""),
                 ("Area", f"{smart_format(area_value(r, g))} {au}"),
                 ("Diameter (ECD)", f"{smart_format(diam_value(r, g))} {du}"),
                 ("Circularity", f"{g.circularity:.3f}"),
                 ("Aspect ratio", f"{g.aspect_ratio:.2f}")]
        why = self._excluded.get(g.grain_id)
        if why:
            from ui.filtering import REASON_LABELS
            lines[0] = (f"Grain #{g.grain_id}  ·  excluded", "")
            for r_ in why:
                lines.append(("Reason", REASON_LABELS.get(r_, r_)))
        f = ui_font(TYPE.caption)
        fb = ui_font(TYPE.body_strong)
        fm = QFontMetricsF(f)
        lh = fm.height() + 3
        kw = max(fm.horizontalAdvance(k) for k, _ in lines[1:])
        vw = max(fm.horizontalAdvance(v) for _, v in lines[1:])
        w = max(kw + vw + 44, QFontMetricsF(fb).horizontalAdvance(lines[0][0]) + 24)
        h = lh * (len(lines) - 1) + 38
        x, y = self._mouse.x() + 18, self._mouse.y() + 18
        if x + w > self.width() - 8:
            x = self._mouse.x() - w - 14
        if y + h > self.height() - 8:
            y = self._mouse.y() - h - 14
        rect = QRectF(x, y, w, h)
        p.setRenderHint(QPainter.Antialiasing)
        self._pill(p, t, rect)
        p.setFont(fb)
        p.setPen(qcolor(t.text.primary))
        p.drawText(QRectF(x + 12, y + 8, w - 24, 20), Qt.AlignLeft | Qt.AlignVCenter, lines[0][0])
        p.setFont(f)
        yy = y + 32
        for k, v in lines[1:]:
            p.setPen(qcolor(t.text.tertiary))
            p.drawText(QRectF(x + 12, yy, kw + 4, lh), Qt.AlignLeft | Qt.AlignVCenter, k)
            p.setPen(qcolor(t.warning.fg if k == "Reason" else t.text.primary))
            p.drawText(QRectF(x + 12, yy, w - 24, lh), Qt.AlignRight | Qt.AlignVCenter, v)
            yy += lh

    # ------------------------------------------------------------------ events
    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self._fit_mode:
            self.fit(animate=False)

    def wheelEvent(self, e) -> None:
        if self._bgr is None:
            return
        dy = e.angleDelta().y() or e.angleDelta().x()
        if not dy:
            return
        self.zoom_by(1.2 ** (dy / 120.0), e.position())
        e.accept()

    def mousePressEvent(self, e) -> None:
        self.setFocus(Qt.MouseFocusReason)
        pos = e.position()
        mr = self._minimap_rect()
        if e.button() == Qt.LeftButton and mr is not None and mr.contains(pos):
            self._mini_drag = True
            self._minimap_jump(pos)
            return
        if e.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._press_pos = QPointF(pos)
            self._press_offset = QPointF(self._offset)
            self._panning = e.button() == Qt.MiddleButton
            if self._panning:
                self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        self._mouse = QPointF(pos)
        if self._mini_drag:
            self._minimap_jump(pos)
            return
        if self._press_pos is not None:
            d = pos - self._press_pos
            if not self._panning and (abs(d.x()) + abs(d.y())) > 4:
                self._panning = True
                self.setCursor(Qt.ClosedHandCursor)
            if self._panning:
                self._fit_mode = False
                self._offset = self._press_offset + d
                self.update()
                return
        if self._interactive:
            gid = self.grain_at(pos)
            if gid != self._hover_id:
                self._hover_id = gid
                g = self._grains.get(gid)
                self._hover_path = (self._contour_path(self._labels, g)
                                    if g is not None else None)
                self.setCursor(Qt.PointingHandCursor if gid else Qt.ArrowCursor)
                self.hovered.emit(gid)
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._mini_drag:
            self._mini_drag = False
            return
        was_pan = self._panning
        self._panning = False
        self._press_pos = None
        self.setCursor(Qt.PointingHandCursor if self._hover_id else Qt.ArrowCursor)
        if was_pan or e.button() != Qt.LeftButton or not self._interactive:
            self.update()
            return
        gid = self.grain_at(e.position())
        multi = bool(e.modifiers() & Qt.ControlModifier)
        if gid and gid not in self._excluded:
            if multi:
                ids = [g for g in self._selected if g != gid]
                if gid not in self._selected:
                    ids.append(gid)
            else:
                ids = [gid]
            self.select(ids)
        elif not multi:
            self.clear_selection()

    def mouseDoubleClickEvent(self, e) -> None:
        if self.grain_at(e.position()) == 0:
            self.fit()

    def leaveEvent(self, e) -> None:
        self._mouse = QPointF(-1, -1)
        if self._hover_id:
            self._hover_id, self._hover_path = 0, None
            self.hovered.emit(0)
        self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k in (Qt.Key_Delete, Qt.Key_Backspace) and self._selected and self._interactive:
            self.delete_requested.emit(list(self._selected))
        elif k == Qt.Key_Escape and self._selected:
            self.clear_selection()
        elif k == Qt.Key_F:
            self.fit()
        elif k == Qt.Key_1:
            self.actual_size()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(1.25)
        elif k == Qt.Key_Minus:
            self.zoom_by(0.8)
        else:
            super().keyPressEvent(e)

    def _minimap_jump(self, pos: QPointF) -> None:
        mr = self._minimap_rect()
        if mr is None:
            return
        h, w = self._bgr.shape[:2]
        k = w / mr.width()
        ix = (min(max(pos.x(), mr.left()), mr.right()) - mr.left()) * k
        iy = (min(max(pos.y(), mr.top()), mr.bottom()) - mr.top()) * k
        self._fit_mode = False
        self._offset = QPointF(self.width() / 2 - ix * self._scale,
                               self.height() / 2 - iy * self._scale)
        self.update()

    def _on_theme_changed(self) -> None:
        self.update()


__all__ = ["GrainCanvas", "VIEWS", "VIEW_LABELS"]
