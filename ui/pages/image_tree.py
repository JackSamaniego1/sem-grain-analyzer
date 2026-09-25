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
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
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
from ui.widgets import IconButton, label
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
IMAGE_ROW_H = 46
GROUP_ROW_H = 40
RESTORE_ROW_H = 28

_ISSUE_TEXT = {"scan": "scan area", "scale": "scale"}


def image_status(im) -> tuple:
    """(semantic kind, short text) for an image row."""
    if getattr(im, "loading", False):
        return "neutral", "Loading…"
    s = im.status
    if s == "done" and im.result is not None:
        return "success", f"{fmt_int(im.result.grain_count)} grains"
    if s == "running":
        return "info", f"Analysing {im.progress} %"
    if s == "queued":
        return "warning", "Queued"
    if s == "error":
        return "danger", "Error"
    return "neutral", "Not analysed"


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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

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

    def __init__(self, state, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self._items: Dict[object, QTreeWidgetItem] = {}
        self._groups: Dict[Path, QTreeWidgetItem] = {}
        self._restore_rows: Dict[Path, QTreeWidgetItem] = {}
        self._pm: Dict[object, tuple] = {}
        self._current = None
        self._building = False
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.md, SPACE.md, SPACE.sm, SPACE.md)
        v.setSpacing(SPACE.sm)
        head = QHBoxLayout()
        self.title = label("IMAGES", "overline")
        self.count = label("", "caption")
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addStretch(1)
        self.restore_btn = IconButton("mdi6.image-refresh-outline",
                                      "Put every removed image back into the analyzer "
                                      "(nothing was deleted)", size=26)
        self.restore_btn.clicked.connect(lambda: self.restore_requested.emit(None))
        self.restore_btn.hide()
        head.addWidget(self.restore_btn)
        self.add_btn = IconButton("add", "Add images to this lot (Ctrl+O). "
                                         "You can also drop files here.", size=26)
        self.add_btn.clicked.connect(self.add_requested)
        head.addWidget(self.add_btn)
        v.addLayout(head)
        self.tree = _Tree()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(10)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setMouseTracking(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setItemDelegate(_Delegate(self))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.setToolTip("Images in the analyzer, by folder. Click to view; right-click "
                             "to remove an image from the analyzer (the lot keeps it).")
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

    def sizeHint(self) -> QSize:
        return QSize(260, 600)

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
        # removed images: their group stays, with an "Add back" row
        removed: Dict[Path, List] = {}
        leaf: Dict[Path, QTreeWidgetItem] = {}
        for im in (doc.removed if doc is not None else []):
            rec = doc.record_for(im)
            if rec is None:
                continue
            parent = None
            for kind, path, cap in self._chain(rec, cache, multi_session):
                parent = self._group(kind, path, cap, parent)
            gp = Path(parent.data(0, ROLE_PATH))
            leaf[gp] = parent
            removed.setdefault(gp, [])
            if Path(rec.path) not in removed[gp]:
                removed[gp].append(Path(rec.path))
            removed[gp].append(im.uid)
        for gp, stuff in removed.items():
            recs = [x for x in stuff if isinstance(x, Path)]
            n = len(stuff) - len(recs)
            row = QTreeWidgetItem()
            row.setData(0, ROLE_KIND, "restore")
            row.setData(0, ROLE_PATHS, [str(p) for p in recs])
            row.setData(0, ROLE_TITLE, f"Add back {n} removed image{'s' if n != 1 else ''}")
            row.setToolTip(0, "Put the images you removed from the analyzer back "
                              "(they were never deleted from the lot)")
            row.setFlags(Qt.ItemIsEnabled)
            leaf[gp].addChild(row)
            self._restore_rows[gp] = row
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
        n_removed = len(doc.removed) if doc is not None else 0
        self.restore_btn.setVisible(bool(n_removed))
        if n_removed:
            self.restore_btn.setToolTip(f"Put the {n_removed} removed image"
                                        f"{'s' if n_removed != 1 else ''} back into the "
                                        "analyzer (nothing was deleted)")
        if self._current in self._items:
            self.set_current(self._current)

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
            done=sum(1 for im in ims if im.result is not None),
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
        parts.append(f"{loading} loading" if loading else f"{done} analysed")
        g.setData(0, ROLE_LINE2, " · ".join(parts))
        scale = ("Loading…" if loading else
                 f"{no_scale} image{'s' if no_scale != 1 else ''} without a scale" if no_scale
                 else "Every image has a scale" if n else "")
        tip = [str(g.data(0, ROLE_TITLE) or ""),
               f"{n} images · {done} analysed" + (f" · {err} with errors" if err else ""),
               scale]
        g.setToolTip(0, "\n".join(x for x in tip if x))

    def _images_under(self, g: QTreeWidgetItem) -> list:
        out = []
        doc = self.state.session
        if doc is None:
            return out
        stack = [g]
        while stack:
            it = stack.pop()
            for i in range(it.childCount()):
                c = it.child(i)
                k = c.data(0, ROLE_KIND)
                if k == "image":
                    im = doc.image(c.data(0, ROLE_UID))
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
            self._refresh_group(p)
            p = p.parent()

    def refresh_all(self) -> None:
        doc = self.state.session
        for uid, it in self._items.items():
            im = doc.image(uid) if doc is not None else None
            if im is not None:
                self._fill(it, im)
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
        if self._building or cur is None or cur.data(0, ROLE_KIND) != "image":
            return
        uid = cur.data(0, ROLE_UID)
        self._current = uid
        self.current_changed.emit(uid)

    def _on_clicked(self, it, _col=0) -> None:
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
        path = Path(it.data(0, ROLE_PATH))
        uids = [im.uid for im in self._images_under(it)]
        word = hui.kind_label(self.state.profile, kind).lower() if kind != "session" \
            else "session"
        if uids:
            acts.append((f"Remove this {word}'s {len(uids)} images from analyzer",
                         lambda u=list(uids): self.remove_requested.emit(u)))
        doc = self.state.session
        recs = [Path(r.path) for r in (doc.records if doc is not None else [])
                if Path(r.path) == path or path in Path(r.path).parents]
        n_back = len(doc.removed_for(recs)) if doc is not None and recs else 0
        if n_back:
            acts.append((f"Add all {n_back} removed images back",
                         lambda r=list(recs): self.restore_requested.emit(r)))
        return acts

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        acts = self.menu_actions(it)
        if not acts:
            return
        m = QMenu(self)
        for text, fn in acts:
            a = m.addAction(icons.icon("mdi6.image-remove-outline" if text.startswith("Remove")
                                       else "mdi6.image-refresh-outline"), text)
            a.triggered.connect(fn)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_theme_changed(self) -> None:
        self.tree.viewport().update()


__all__ = ["ImageTree", "image_status", "record_levels", "ROLE_KIND", "ROLE_UID", "ROLE_PATH",
           "ROLE_PATHS"]
