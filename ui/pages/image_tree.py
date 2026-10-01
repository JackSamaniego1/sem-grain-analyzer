"""
ImageTree (UX-09 / UX-06): the Analyze page's image list as a folder tree
Job > Part > Lot (> Session, when a lot has several) > images.

* Group rows are collapsible and show how many images they hold and how
  many are analysed, with two status marks: analysis (dot) and scale
  (ruler: green = every image has a scale, amber = some have none).  The
  full numbers are in the tooltip.
* Image rows show a thumbnail, the file name and the analysis status; an
  amber "Set scan area / scale" chip flags images not ready for analysis.
* Right-click (or the context-menu key) removes images from the analyzer
  -- never from the lot.  A group that has removed images shows an
  "Add back N removed images" row; the header button puts every removed
  image back.
* Built once per image-list change; a status change updates one row and
  its ancestors, so hundreds of images stay fast.  Thumbnails arrive from
  the background loader; nothing here reads files.
* Up/Down step from image to image in tree order (group rows are skipped,
  a collapsed group opens to show the image it lands on).
* Options: ``checkable`` (Analyze page tick boxes for "Analyze selected")
  and ``manage`` (add / remove / add-back controls).  The Review page uses
  the same tree with both off (UPDATE 4 item 13): browse only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QItemSelectionModel, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QMenu, QStyle, QStyledItemDelegate, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui import hierarchy_ui as hui
from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import RADII, SPACE, TYPE, TypeStyle
from ui.format import fmt_int
from ui.widgets import AnimatedButton, IconButton, label
from ui.widgets._base import ThemeAware, qcolor, tokens
from ui.workers import IMAGE_EXTS

ROLE_KIND = Qt.UserRole + 1       # "image" | "restore" | project | sample | lot | session
ROLE_UID = Qt.UserRole + 2
ROLE_PATH = Qt.UserRole + 3
ROLE_LINE2 = Qt.UserRole + 4      # second line text
ROLE_TONE = Qt.UserRole + 5       # semantic kind of the status dot
ROLE_FLAG = Qt.UserRole + 6       # image: setup chip text ("" = ready)
ROLE_TITLE = Qt.UserRole + 7
ROLE_CAL = Qt.UserRole + 8        # group: scale tone
ROLE_PATHS = Qt.UserRole + 9      # restore row: record paths

THUMB_W, THUMB_H = 46, 34
CHECK_SIZE = 16
CHECK_GUTTER = 24          # room the tick box takes on the left of an image row
IMAGE_ROW_H = 46
GROUP_ROW_H = 40
RESTORE_ROW_H = 28

_ISSUE_TEXT = {"scan": "scan area", "scale": "scale"}


def image_status(im) -> tuple:
    """(semantic kind, short text) for an image row."""
    if getattr(im, "loading", False):
        return "neutral", "Loading…"
    s = im.status
    if s == "done" and im.result is not None and getattr(im, "stale", ""):
        return "warning", "Needs re-analysis"
    if s == "done" and im.result is not None:
        return "success", f"{fmt_int(im.result.grain_count)} grains"
    if s == "running":
        return "info", f"Analyzing {im.progress} %"
    if s == "queued":
        return "warning", "Queued"
    if s == "error":
        return "danger", "Error"
    return "neutral", "Not analyzed"


def record_levels(rec, profile, with_session: bool = True) -> List[tuple]:
    """[(kind, path, caption), ...] from project down to the record.
    ``with_session=False`` stops at the lot (one session of the lot)."""
    path = Path(rec.path)
    if rec.is_lot:
        chain = [("project", path.parent.parent, rec.project_meta),
                 ("sample", path.parent, rec.sample_meta), ("lot", path, rec.lot_meta)]
    else:
        m = rec.meta
        chain = [("project", path.parent.parent.parent, rec.project_meta),
                 ("sample", path.parent.parent, rec.sample_meta),
                 ("lot", path.parent, rec.lot_meta)]
        if with_session:
            chain.append(("session", path, {"label": getattr(m, "label", ""),
                                            "created_local": getattr(m, "created_local", "")}))
    out = []
    for kind, p, meta in chain:
        try:
            cap = (hui.node_caption(profile, kind, meta or {}, p) if kind == "session"
                   else hui.crumb_caption(profile, kind, meta or {}, p))
        except Exception:
            cap = p.name
        out.append((kind, p, cap or p.name))
    return out


#: UX-09: throttle for recounting group rows while images stream in.
GROUP_REFRESH_MS = 100

class _Delegate(QStyledItemDelegate):
    def __init__(self, tree: "ImageTree") -> None:
        super().__init__(tree)
        self.tree = tree

    def sizeHint(self, option, index) -> QSize:
        kind = index.data(ROLE_KIND)
        h = {"image": IMAGE_ROW_H, "restore": RESTORE_ROW_H}.get(kind, GROUP_ROW_H)
        return QSize(option.rect.width(), h)

    def paint(self, p: QPainter, option, index) -> None:
        t = tokens()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(option.rect).adjusted(1, 1, -2, -1)
        kind = index.data(ROLE_KIND)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        if selected or hover:
            p.setPen(QPen(qcolor(t.accent.base), 1.2) if selected else Qt.NoPen)
            p.setBrush(qcolor(t.accent.subtle) if selected else qcolor(t.surface.surface2))
            p.drawRoundedRect(r, RADII.md, RADII.md)
        if kind == "image":
            self._paint_image(p, r, index, t)
        elif kind == "restore":
            self._paint_restore(p, r, index, t)
        else:
            self._paint_group(p, r, index, t)
        p.restore()

    def _dot(self, p, x, y, tone, t) -> None:
        sem = t.semantic(tone)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(sem.solid if tone != "neutral" else t.text.tertiary))
        p.drawEllipse(QRectF(x, y, 7, 7))

    def _paint_image(self, p, r: QRectF, index, t) -> None:
        pad = 5
        if self.tree.checkable:
            self._paint_check(p, self.tree.tree.check_rect(r),
                              self.tree.is_checked(index.data(ROLE_UID)), t)
            pad += CHECK_GUTTER
        tr = QRectF(r.left() + pad, r.center().y() - THUMB_H / 2, THUMB_W, THUMB_H)
        path = QPainterPath()
        path.addRoundedRect(tr, RADII.sm, RADII.sm)
        p.fillPath(path, qcolor(t.surface.bg))
        pm = self.tree.pixmap_for(index.data(ROLE_UID))
        if pm is not None and not pm.isNull():
            s = min(tr.width() / pm.width(), tr.height() / pm.height())
            dw, dh = pm.width() * s, pm.height() * s
            dst = QRectF(tr.center().x() - dw / 2, tr.center().y() - dh / 2, dw, dh)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(dst, pm, QRectF(pm.rect()))
            p.restore()
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        x = tr.right() + 8
        w = r.right() - x - 4
        fn = ui_font(TypeStyle(12, 600, 16))
        p.setFont(fn)
        name = QFontMetricsF(fn).elidedText(index.data(ROLE_TITLE) or "", Qt.ElideMiddle, w)
        p.setPen(qcolor(t.text.primary))
        p.drawText(QRectF(x, r.top() + 5, w, 16), Qt.AlignLeft | Qt.AlignVCenter, name)
        tone = index.data(ROLE_TONE) or "neutral"
        fs = ui_font(TYPE.caption)
        p.setFont(fs)
        fm = QFontMetricsF(fs)
        flag = index.data(ROLE_FLAG) or ""
        if flag:
            # amber "Set scan area / scale" chip instead of the status line
            warn = t.semantic("warning")
            fw = min(fm.horizontalAdvance(flag) + 12, w)
            chip = QRectF(x, r.top() + 24, fw, 16)
            p.setPen(QPen(qcolor(warn.border), 1))
            p.setBrush(qcolor(warn.bg))
            p.drawRoundedRect(chip, 8, 8)
            p.setPen(qcolor(warn.fg))
            p.drawText(chip.adjusted(6, 0, -6, 0), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(flag, Qt.ElideRight, fw - 12))
            return
        self._dot(p, x + 1, r.top() + 29, tone, t)
        sem = t.semantic(tone)
        p.setPen(qcolor(sem.fg if tone != "neutral" else t.text.secondary))
        line2 = index.data(ROLE_LINE2) or ""
        p.drawText(QRectF(x + 12, r.top() + 24, w - 12, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(line2, Qt.ElideRight, w - 12))

    def _paint_check(self, p, box: QRectF, on: bool, t) -> None:
        p.save()
        p.setPen(QPen(qcolor(t.accent.base if on else t.text.tertiary), 1.4))
        p.setBrush(qcolor(t.accent.base) if on else Qt.NoBrush)
        p.drawRoundedRect(box, 3, 3)
        if on:
            p.setPen(QPen(qcolor(t.accent.fg), 1.8,
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            x, y, w, h = box.x(), box.y(), box.width(), box.height()
            p.drawPolyline([QPointF(x + w * .24, y + h * .52), QPointF(x + w * .43, y + h * .70),
                            QPointF(x + w * .76, y + h * .32)])
        p.restore()

    def _paint_group(self, p, r: QRectF, index, t) -> None:
        x = r.left() + 4
        w = r.right() - x - 4
        fb = ui_font(TYPE.body_strong)
        p.setFont(fb)
        p.setPen(qcolor(t.text.primary))
        name = QFontMetricsF(fb).elidedText(index.data(ROLE_TITLE) or "", Qt.ElideRight, w)
        p.drawText(QRectF(x, r.top() + 2, w, 20), Qt.AlignLeft | Qt.AlignVCenter, name)
        tone = index.data(ROLE_TONE) or "neutral"
        cal = index.data(ROLE_CAL) or "neutral"
        self._dot(p, x + 1, r.top() + 25, tone, t)
        sem = t.semantic(cal)
        col = sem.solid if cal != "neutral" else t.text.tertiary
        icons.icon("calibrate", col).paint(p, QRectF(x + 11, r.top() + 22, 13, 13).toRect())
        fs = ui_font(TYPE.caption)
        p.setFont(fs)
        p.setPen(qcolor(t.text.secondary))
        line2 = index.data(ROLE_LINE2) or ""
        p.drawText(QRectF(x + 28, r.top() + 20, w - 28, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(fs).elidedText(line2, Qt.ElideRight, w - 28))

    def _paint_restore(self, p, r: QRectF, index, t) -> None:
        x = r.left() + 6
        icons.icon("mdi6.image-refresh-outline", t.accent.text).paint(
            p, QRectF(x, r.center().y() - 8, 16, 16).toRect())
        fs = ui_font(TypeStyle(12, 600, 16))
        p.setFont(fs)
        p.setPen(qcolor(t.accent.text))
        w = r.right() - x - 26
        p.drawText(QRectF(x + 22, r.top(), w, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(fs).elidedText(index.data(ROLE_TITLE) or "", Qt.ElideRight, w))


class _Tree(QTreeWidget):
    files_dropped = Signal(list)
    check_toggled = Signal(object)           # the image item whose tick box was hit
    step_requested = Signal(int)             # Up/Down: -1 / +1 image

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.checkable = False
        self._checked: set = set()
        self._quiet_current = False

    def check_rect(self, r: QRectF) -> QRectF:
        return QRectF(r.left() + 5, r.center().y() - CHECK_SIZE / 2, CHECK_SIZE, CHECK_SIZE)

    def _box_hit(self, e):
        """The image item whose tick box the mouse event hit, else None."""
        if self.checkable and e.button() == Qt.LeftButton:
            it = self.itemAt(e.position().toPoint())
            if it is not None and it.data(0, ROLE_KIND) == "image":
                rect = QRectF(self.visualItemRect(it)).adjusted(1, 1, -2, -1)
                if self.check_rect(rect).adjusted(-4, -4, 4, 4).contains(QPointF(e.position())):
                    return it
        return None

    def _toggle_from_mouse(self, it) -> None:
        # the row becomes current (so Space acts on it) but is not selected and
        # the displayed image does not change
        self._quiet_current = True
        try:
            self.setCurrentItem(it, 0, QItemSelectionModel.NoUpdate)
        finally:
            self._quiet_current = False
        self.check_toggled.emit(it)

    def mousePressEvent(self, e) -> None:
        # a hit on the tick box toggles it only: no selection / current-image change
        it = self._box_hit(e)
        if it is not None:
            self._toggle_from_mouse(it)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        it = self._box_hit(e)
        if it is not None:          # a fast second click toggles again, never activates
            self._toggle_from_mouse(it)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Up, Qt.Key_Down) and not (
                e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
            self.step_requested.emit(1 if e.key() == Qt.Key_Down else -1)
            e.accept()
            return
        if self.checkable and e.key() == Qt.Key_Space:
            it = self.currentItem()
            if it is not None and it.data(0, ROLE_KIND) == "image":
                self.check_toggled.emit(it)
                e.accept()
                return
        super().keyPressEvent(e)

    @staticmethod
    def _paths(md) -> List[str]:
        out = []
        for u in md.urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                if p.suffix.lower() in IMAGE_EXTS:
                    out.append(str(p))
        return out

    def dragEnterEvent(self, e) -> None:
        if self._paths(e.mimeData()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        if self._paths(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = self._paths(e.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()


class ImageTree(ThemeAware, QWidget):
    """Folder tree of the images in the analyzer."""

    current_changed = Signal(object)        # uid
    files_dropped = Signal(list)
    add_requested = Signal()
    remove_requested = Signal(list)         # uids
    restore_requested = Signal(object)      # list of record Paths, or None = all
    checked_changed = Signal(list)          # uids ticked now (only when checkable)
    remove_checked_requested = Signal(list)  # round 3: "Remove selected" (ticked uids)
    undo_remove_requested = Signal()         # round 3b: header Undo (last removal)

    def __init__(self, state, parent: Optional[QWidget] = None,
                 checkable: bool = False, manage: bool = True) -> None:
        super().__init__(parent)
        self.state = state
        self._manage = bool(manage)
        self._items: Dict[object, QTreeWidgetItem] = {}
        self._groups: Dict[Path, QTreeWidgetItem] = {}
        self._restore_rows: Dict[Path, QTreeWidgetItem] = {}
        self._pm: Dict[object, tuple] = {}
        self._current = None
        self._building = False
        # UX-09: group counts are O(images in group); per-image updates only
        # mark their groups dirty and one throttled pass recounts them.
        self._dirty_groups: List[QTreeWidgetItem] = []
        self._group_timer = QTimer(self)
        self._group_timer.setSingleShot(True)
        self._group_timer.setInterval(GROUP_REFRESH_MS)
        self._group_timer.timeout.connect(self.flush_group_counts)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.md, SPACE.md, SPACE.sm, SPACE.md)
        v.setSpacing(SPACE.sm)
        head = QHBoxLayout()
        self.title = label("IMAGES", "overline")
        self.count = label("", "caption")
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addStretch(1)
        self._tick_memory: set = set()
        self.add_btn = IconButton("add", "Add images to this lot (Ctrl+O). "
                                         "You can also drop files here.", size=26)
        self.add_btn.clicked.connect(self.add_requested)
        self.add_btn.setVisible(self._manage)
        head.addWidget(self.add_btn)
        # Round 3b: one Undo for removals, right of the + (replaces the
        # inline "Add back" rows); a stack in AppState, newest first.
        self.restore_btn = IconButton("mdi6.undo-variant",
                                      "Undo remove — put back the last removed images", size=26)
        self.restore_btn.setObjectName("analyzer_undo_remove")
        self.restore_btn.clicked.connect(self.undo_remove_requested)
        self.restore_btn.setVisible(self._manage)
        self.restore_btn.setEnabled(False)
        head.addWidget(self.restore_btn)
        v.addLayout(head)
        # Round 3: Select all / none + Remove selected (Analyze page list only)
        tools = QHBoxLayout()
        tools.setSpacing(SPACE.xs)
        self.select_all_btn = AnimatedButton("Select all", "mdi6.checkbox-multiple-outline",
                                             "ghost", "sm")
        self.select_all_btn.setObjectName("analyzer_select_all")
        self.select_all_btn.setToolTip("Tick every image in the analyzer")
        self.select_all_btn.clicked.connect(self.toggle_select_all)
        self.remove_sel_btn = AnimatedButton("Remove selected", "mdi6.playlist-remove",
                                             "ghost", "sm")
        self.remove_sel_btn.setObjectName("analyzer_remove_selected")
        self.remove_sel_btn.clicked.connect(self._remove_checked)
        tools.addWidget(self.select_all_btn)
        tools.addWidget(self.remove_sel_btn)
        tools.addStretch(1)
        self._tools = QWidget()
        self._tools.setLayout(tools)
        tools.setContentsMargins(0, 0, 0, 0)
        self._tools.setVisible(bool(checkable) and self._manage)
        v.addWidget(self._tools)
        self.checked_changed.connect(lambda _u: self._sync_tools())
        self.tree = _Tree()
        self.tree.checkable = checkable
        self.tree.check_toggled.connect(self._toggle_item)
        self.tree.step_requested.connect(self.step)
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(10)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setMouseTracking(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection if self._manage
                                   else QAbstractItemView.SingleSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setItemDelegate(_Delegate(self))
        if self._manage:
            self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
            self.tree.customContextMenuRequested.connect(self._menu)
            self.tree.setToolTip("Images in the analyzer, by folder. Click to view; right-click "
                                 "to remove an image from the analyzer (the lot keeps it).")
        else:
            self.tree.setContextMenuPolicy(Qt.NoContextMenu)
            self.tree.setToolTip("Images in the analyzer, by folder. Click to view; "
                                 "Up/Down step through the images.")
        self.tree.currentItemChanged.connect(self._on_current_item)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemActivated.connect(self._on_clicked)
        self.tree.files_dropped.connect(self.files_dropped)
        v.addWidget(self.tree, 1)
        self.hint = label("Drop SEM images here", "caption")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        self._connect_theme()
        self._sync_tools()

    # ------------------------------------------------------------------ round 3 tools
    def all_checked(self) -> bool:
        return bool(self._items) and all(u in self.tree._checked for u in self._items)

    def toggle_select_all(self) -> None:
        """Select all <-> Select none (every image currently in the list)."""
        if not self.tree.checkable:
            return
        self._set_checked(set() if self.all_checked() else set(self._items))
        self._sync_tools()

    def _remove_checked(self) -> None:
        uids = self.checked_uids()
        if uids:
            self.remove_checked_requested.emit(uids)

    def set_remove_blocked(self, why: str) -> None:
        """Analyze page: a run touches the ticked images -> explain, disable."""
        self._remove_block = why or ""
        self._sync_tools()

    def _sync_tools(self) -> None:
        if not hasattr(self, "remove_sel_btn"):
            return
        n = len(self.checked_uids())
        every = self.all_checked()
        self.select_all_btn.setText("Select none" if every else "Select all")
        self.select_all_btn.setToolTip("Untick every image" if every
                                       else "Tick every image in the analyzer")
        self.select_all_btn.setEnabled(bool(self._items))
        self.remove_sel_btn.setText(f"Remove selected ({n})" if n else "Remove selected")
        block = getattr(self, "_remove_block", "")
        self.remove_sel_btn.setEnabled(n > 0 and not block)
        self.remove_sel_btn.setToolTip(
            block or ("Tick images to remove them from the analyzer" if not n else
                      f"Take the {n} ticked image{'s' if n != 1 else ''} out of the analyzer "
                      "only — files and saved results stay in their job; load them again "
                      "from Projects"))

    def sizeHint(self) -> QSize:
        return QSize(260, 600)

    # ------------------------------------------------------------------ tick boxes
    @property
    def checkable(self) -> bool:
        return self.tree.checkable

    @property
    def manage(self) -> bool:
        return self._manage

    def set_checkable(self, on: bool) -> None:
        if on == self.tree.checkable:
            return
        self.tree.checkable = bool(on)
        if not on:
            self._set_checked(set())
        self.tree.viewport().update()
        self._tools.setVisible(bool(on) and self._manage)
        self._sync_tools()

    def is_checked(self, uid) -> bool:
        return uid in self.tree._checked

    def checked_uids(self) -> List:
        """Ticked images, in list order."""
        return [u for u in self._items if u in self.tree._checked]

    def set_checked(self, uids) -> None:
        self._set_checked({u for u in uids if u in self._items})

    def _set_checked(self, new: set) -> None:
        if new != self.tree._checked:
            self.tree._checked = new
            self.tree.viewport().update()
            self.checked_changed.emit(self.checked_uids())

    def _toggle_item(self, it) -> None:
        uid = it.data(0, ROLE_UID)
        self._set_checked(self.tree._checked ^ {uid})

    # ------------------------------------------------------------------ build
    def _group(self, kind: str, path: Path, cap: str, parent) -> QTreeWidgetItem:
        g = self._groups.get(path)
        if g is None:
            g = QTreeWidgetItem()
            g.setData(0, ROLE_KIND, kind)
            g.setData(0, ROLE_PATH, str(path))
            g.setData(0, ROLE_TITLE, cap)
            g.setFlags(Qt.ItemIsEnabled)
            (parent.addChild(g) if parent is not None else self.tree.addTopLevelItem(g))
            self._groups[path] = g
        return g

    def _chain(self, rec, cache: Dict[int, List[tuple]], multi_session: set) -> List[tuple]:
        key = id(rec)
        if key not in cache:
            lot = Path(rec.path) if rec.is_lot else Path(rec.path).parent
            cache[key] = record_levels(rec, self.state.profile,
                                       with_session=lot in multi_session)
        return cache[key]

    def set_images(self, images) -> None:
        """Rebuild the tree for ``images`` (keeps collapsed groups + current)."""
        collapsed = {p for p, it in self._groups.items() if not it.isExpanded()}
        self._building = True
        self._group_timer.stop()
        self._dirty_groups = []
        self.tree.clear()
        self._items, self._groups, self._restore_rows = {}, {}, {}
        doc = self.state.session
        cache: Dict[int, List[tuple]] = {}
        per_lot: Dict[Path, int] = {}
        for rec in (doc.records if doc is not None else []):
            if not rec.is_lot:
                lot = Path(rec.path).parent
                per_lot[lot] = per_lot.get(lot, 0) + 1
        multi_session = {lot for lot, n in per_lot.items() if n > 1}
        for im in images:
            rec = doc.record_for(im) if doc is not None else None
            parent = None
            if rec is not None:
                for kind, path, cap in self._chain(rec, cache, multi_session):
                    parent = self._group(kind, path, cap, parent)
            it = QTreeWidgetItem()
            it.setData(0, ROLE_KIND, "image")
            it.setData(0, ROLE_UID, im.uid)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            (parent.addChild(it) if parent is not None else self.tree.addTopLevelItem(it))
            self._items[im.uid] = it
            self._fill(it, im)
        # Round 3b: removed images leave NO trace in the list (no row, no empty
        # Job / Part / Lot header -- groups exist only for present images);
        # the header's Undo button puts the last removal back.
        self.tree.expandAll()
        for p in collapsed:
            if p in self._groups:
                self._groups[p].setExpanded(False)
        for g in self._groups.values():
            self._refresh_group(g)
        self._building = False
        n = len(images)
        self.count.setText(str(n) if n else "")
        self.hint.setVisible(not n)
        self.sync_undo()
        if self._current in self._items:
            self.set_current(self._current)
        # ticks survive rebuilds; removed images drop out but their tick is
        # remembered so an Undo brings them back ticked as before
        keep = {u for u in self.tree._checked if u in self._items}
        self._tick_memory |= self.tree._checked - keep
        back = {u for u in self._tick_memory if u in self._items}
        self._tick_memory -= back
        if keep | back != self.tree._checked:
            self._set_checked(keep | back)
        self._sync_tools()

    def sync_undo(self) -> None:
        """Undo-remove button: enabled while the analyzer has a removal to undo."""
        st = self.state
        can = bool(self._manage and getattr(st, "can_undo_removal", lambda: False)())
        self.restore_btn.setEnabled(can)
        self.restore_btn.setToolTip("Undo remove — put back the last removed images" if can
                                    else "Undo remove — nothing removed to put back")

    def _fill(self, it: QTreeWidgetItem, im) -> None:
        tone, text = image_status(im)
        it.setData(0, ROLE_TITLE, im.display_name)
        it.setData(0, ROLE_TONE, tone)
        it.setData(0, ROLE_LINE2, text)
        issues = [] if im.loading else [i for i in self.state.setup_issues(im)
                                        if i in _ISSUE_TEXT]
        flag = ""
        if issues and im.status not in ("running", "queued"):
            flag = "Set " + " & ".join(_ISSUE_TEXT[i] for i in issues)
        it.setData(0, ROLE_FLAG, flag)
        tip = [im.filename, text]
        if issues:
            tip.append("Before analysis: set the " + " and ".join(_ISSUE_TEXT[i] for i in issues))
        if im.status == "error" and im.message:
            tip.append(im.message.splitlines()[0])
        it.setToolTip(0, "\n".join(tip))

    def pixmap_for(self, uid) -> Optional[QPixmap]:
        doc = self.state.session
        im = doc.image(uid) if doc is not None else None
        if im is None or im.thumb is None or im.thumb.isNull():
            return None
        key = im.thumb.cacheKey()
        cached = self._pm.get(uid)
        if cached is None or cached[0] != key:
            pm = QPixmap.fromImage(im.thumb.scaled(THUMB_W * 2, THUMB_H * 2, Qt.KeepAspectRatio,
                                                   Qt.SmoothTransformation))
            self._pm[uid] = (key, pm)
            return pm
        return cached[1]

    def group_summary(self, g: QTreeWidgetItem) -> dict:
        ims = self._images_under(g)
        return dict(
            n=len(ims),
            done=sum(1 for im in ims if im.result is not None
                     and not getattr(im, "stale", "")),
            err=sum(1 for im in ims if im.status == "error"),
            running=any(im.status in ("running", "queued") and not im.loading for im in ims),
            loading=sum(1 for im in ims if im.loading),
            no_scale=sum(1 for im in ims if not im.loading and im.readable
                         and self.state.px_for(im) <= 0))

    def _refresh_group(self, g: QTreeWidgetItem) -> None:
        s = self.group_summary(g)
        n, done, err, loading, no_scale = s["n"], s["done"], s["err"], s["loading"], \
            s["no_scale"]
        tone = ("danger" if err else "info" if s["running"] else
                "success" if n and done == n else "warning" if done else "neutral")
        g.setData(0, ROLE_TONE, tone)
        g.setData(0, ROLE_CAL, "warning" if no_scale else ("success" if n and not loading
                                                           else "neutral"))
        parts = [f"{n} image{'s' if n != 1 else ''}"]
        parts.append(f"{loading} loading" if loading else f"{done} analyzed")
        g.setData(0, ROLE_LINE2, " · ".join(parts))
        scale = ("Loading…" if loading else
                 f"{no_scale} image{'s' if no_scale != 1 else ''} without a scale" if no_scale
                 else "Every image has a scale" if n else "")
        tip = [str(g.data(0, ROLE_TITLE) or ""),
               f"{n} images · {done} analyzed" + (f" · {err} with errors" if err else ""),
               scale]
        g.setToolTip(0, "\n".join(x for x in tip if x))

    def _images_under(self, g: QTreeWidgetItem) -> list:
        out = []
        doc = self.state.session
        if doc is None:
            return out
        by_uid = {im.uid: im for im in doc.images}       # one pass, not one per child
        stack = [g]
        while stack:
            it = stack.pop()
            for i in range(it.childCount()):
                c = it.child(i)
                k = c.data(0, ROLE_KIND)
                if k == "image":
                    im = by_uid.get(c.data(0, ROLE_UID))
                    if im is not None:
                        out.append(im)
                elif k != "restore":
                    stack.append(c)
        return out

    # ------------------------------------------------------------------ updates
    def update_item(self, uid) -> None:
        it = self._items.get(uid)
        doc = self.state.session
        im = doc.image(uid) if doc is not None else None
        if it is None or im is None:
            return
        self._fill(it, im)
        p = it.parent()
        while p is not None:
            if not any(p is d for d in self._dirty_groups):
                self._dirty_groups.append(p)
            p = p.parent()
        if self._dirty_groups and not self._group_timer.isActive():
            self._group_timer.start()        # throttled: fires even mid-stream

    def flush_group_counts(self) -> None:
        """Recount the groups touched since the last pass (also for tests)."""
        self._group_timer.stop()
        dirty, self._dirty_groups = self._dirty_groups, []
        for g in dirty:
            self._refresh_group(g)

    def refresh_all(self) -> None:
        doc = self.state.session
        for uid, it in self._items.items():
            im = doc.image(uid) if doc is not None else None
            if im is not None:
                self._fill(it, im)
        self._group_timer.stop()
        self._dirty_groups = []
        for g in self._groups.values():
            self._refresh_group(g)
        self.tree.viewport().update()

    def set_current(self, uid) -> None:
        self._current = uid
        it = self._items.get(uid)
        if it is None:
            return
        self._building = True
        self.tree.setCurrentItem(it)
        self.tree.scrollToItem(it)
        self._building = False

    def item(self, uid) -> Optional[QTreeWidgetItem]:
        return self._items.get(uid)

    def image_order(self) -> List:
        """Image uids in tree (display) order."""
        out, stack = [], [self.tree.topLevelItem(i)
                          for i in reversed(range(self.tree.topLevelItemCount()))]
        while stack:
            it = stack.pop()
            if it.data(0, ROLE_KIND) == "image":
                out.append(it.data(0, ROLE_UID))
            stack.extend(it.child(i) for i in reversed(range(it.childCount())))
        return out

    def step(self, delta: int) -> None:
        """Show the next (+1) / previous (-1) image in tree order; group
        rows are skipped and a collapsed group opens."""
        order = self.image_order()
        if not order:
            return
        cur = self._current if self._current in order else None
        if cur is None:
            i = 0 if delta > 0 else len(order) - 1
        else:
            i = max(0, min(len(order) - 1, order.index(cur) + (1 if delta > 0 else -1)))
        uid = order[i]
        it = self._items[uid]
        p = it.parent()
        while p is not None:
            p.setExpanded(True)
            p = p.parent()
        if self.tree.currentItem() is it:
            if uid != self._current:          # e.g. current after a tick-box click
                self._current = uid
                self.current_changed.emit(uid)
            return
        self.tree.setCurrentItem(it)          # -> _on_current_item -> current_changed
        self.tree.scrollToItem(it)

    def group_item(self, path) -> Optional[QTreeWidgetItem]:
        return self._groups.get(Path(path))

    def groups(self) -> Dict[Path, QTreeWidgetItem]:
        return dict(self._groups)

    def restore_rows(self) -> Dict[Path, QTreeWidgetItem]:
        return dict(self._restore_rows)

    def selected_uids(self) -> List:
        return [it.data(0, ROLE_UID) for it in self.tree.selectedItems()
                if it.data(0, ROLE_KIND) == "image"]

    def _on_current_item(self, cur, _prev) -> None:
        if (self._building or self.tree._quiet_current or cur is None
                or cur.data(0, ROLE_KIND) != "image"):
            return
        uid = cur.data(0, ROLE_UID)
        self._current = uid
        self.current_changed.emit(uid)

    def _on_clicked(self, it, _col=0) -> None:
        if it is not None and it.data(0, ROLE_KIND) == "image":
            uid = it.data(0, ROLE_UID)     # the row may be current already (tick box
            if uid != self._current:       # click) without being the displayed image
                self._current = uid
                self.current_changed.emit(uid)
        if it is not None and it.data(0, ROLE_KIND) == "restore":
            self.restore_requested.emit([Path(p) for p in (it.data(0, ROLE_PATHS) or [])])

    # ------------------------------------------------------------------ menu
    def menu_actions(self, it: QTreeWidgetItem) -> List[tuple]:
        """(text, callable) entries for the right-click menu of ``it``."""
        acts = []
        if it is None:
            return acts
        kind = it.data(0, ROLE_KIND)
        if kind == "restore":
            return [(it.data(0, ROLE_TITLE), lambda i=it: self._on_clicked(i))]
        if kind == "image":
            sel = self.selected_uids()
            uid = it.data(0, ROLE_UID)
            uids = sel if uid in sel and len(sel) > 1 else [uid]
            n = len(uids)
            acts.append((f"Remove {n} images from analyzer" if n > 1 else
                         "Remove from analyzer",
                         lambda u=list(uids): self.remove_requested.emit(u)))
            return acts
        uids = [im.uid for im in self._images_under(it)]
        word = hui.kind_label(self.state.profile, kind).lower() if kind != "session" \
            else "session"
        if uids:
            acts.append((f"Remove this {word}'s {len(uids)} images from analyzer",
                         lambda u=list(uids): self.remove_requested.emit(u)))
        return acts

    def build_menu(self, it: QTreeWidgetItem) -> Optional[QMenu]:
        acts = self.menu_actions(it)
        if not acts:
            return None
        m = QMenu(self)
        for text, fn in acts:
            a = m.addAction(icons.icon("mdi6.image-remove-outline" if text.startswith("Remove")
                                       else "mdi6.image-refresh-outline"), text)
            # triggered(bool) would land in the lambdas' bound default arg
            a.triggered.connect(lambda _checked=False, f=fn: f())
        return m

    def _menu(self, pos) -> None:
        m = self.build_menu(self.tree.itemAt(pos))
        if m is not None:
            m.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_theme_changed(self) -> None:
        self.tree.viewport().update()


__all__ = ["ImageTree", "image_status", "record_levels", "ROLE_KIND", "ROLE_UID", "ROLE_PATH",
           "ROLE_PATHS"]
