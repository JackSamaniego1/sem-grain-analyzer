"""
New Session wizard (DATA-07, HIER-01, Ctrl+N).

The steps follow the workspace's hierarchy profile: one step per level
(e.g. Job # → Part Number → Lot), each picking an existing folder or
creating a new one with the level's own metadata fields (typed editors,
required markers).  Then:

* images stored in the lot (profile ``images_location == "lot"``): the last
  step is "<Lot> images" — the lot folder itself holds the images and
  results; picking an existing lot appends the new images to it;
* timestamped runs (legacy): a Session step (acquisition details) and an
  Images step, creating a new run folder inside the lot.

Last-used values are remembered (ui_state["wizard"]).  Folders and image
copies are created off the GUI thread through the data layer;
``session_created(Path)`` fires with the lot (or session) folder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget,
)

from data.catalog import Catalog
from data.hierarchy import FieldDef, context_for_session
from data.models import ImageEntry, read_json
from data.session_io import save_session
from data.workspace import Workspace
from ui import hierarchy_ui as hui
from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import RADII, SPACE, TYPE, TypeStyle
from ui.widgets import (
    AnimatedButton, CollapsibleSection, FadeStackedWidget, KeyValueList, label,
)
from ui.widgets._base import ThemeAware, qcolor, tokens
from ui.widgets.field_editors import editor_value, make_editor, mark_invalid, set_editor_value
from ui.workers import IMAGE_EXTS, IMAGE_FILTER, run_task

# legacy constant (v3.0 layout); the live list comes from the profile
STEPS = [("Project", "Which project does this work belong to?"),
         ("Sample", "Which sample is being examined?"),
         ("Lot", "Which lot (heat / batch) of that sample?"),
         ("Session", "Acquisition details for this sitting at the microscope"),
         ("Images", "Add the SEM images to analyse")]

NEW = "__new__"

# old ``fill(...)`` keywords → profile field keys
_ALIASES = {"grade": "alloy_grade", "material": "material", "customer": "customer",
            "supplier": "supplier", "heat_treatment": "heat_treatment"}


def wizard_steps(profile) -> List[tuple]:
    """(key, title, subtitle) per step, from the hierarchy profile."""
    L = lambda k: hui.kind_label(profile, k)  # noqa: E731
    steps = [("project", L("project"), f"Which {L('project')} does this work belong to?"),
             ("sample", L("sample"), f"Which {L('sample')} of that {L('project')}?"),
             ("lot", L("lot"), f"Which {L('lot')} of that {L('sample')}?")]
    if hui.lot_mode(profile):
        steps.append(("images", f"{L('lot')} images",
                      f"Add the SEM images — they are stored in the {L('lot')} folder itself"))
    else:
        steps.append(("session", "Session", "Acquisition details for this sitting at the "
                                             "microscope"))
        steps.append(("images", "Images", "Add the SEM images to analyse"))
    return steps


class StepList(ThemeAware, QWidget):
    """Vertical stepper: numbered discs joined by a line."""

    def __init__(self, steps=None, heading: str = "NEW SESSION", parent=None) -> None:
        super().__init__(parent)
        self.current = 0
        self.steps = [(s[-2], s[-1]) for s in (steps or STEPS)]
        self.heading = heading
        self.summaries = [""] * len(self.steps)
        self.setFixedWidth(230)
        self._connect_theme()

    def set_current(self, i: int) -> None:
        self.current = i
        self.update()

    def set_summaries(self, texts) -> None:
        self.summaries = list(texts)
        self.update()

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(t.surface.surface1))
        p.setPen(QPen(qcolor(t.border.subtle), 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.setFont(ui_font(TYPE.overline))
        p.setPen(qcolor(t.text.tertiary))
        p.drawText(QRectF(24, 24, 180, 16), Qt.AlignLeft, self.heading)
        y0, dy, x = 64, 58, 36
        n = len(self.steps)
        for i, (name, _sub) in enumerate(self.steps):
            cy = y0 + i * dy
            if i < n - 1:
                p.setPen(QPen(qcolor(t.accent.base if i < self.current else t.border.strong), 2))
                p.drawLine(x, cy + 14, x, cy + dy - 14)
            done, cur = i < self.current, i == self.current
            if done:
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(t.accent.base))
            elif cur:
                p.setPen(QPen(qcolor(t.accent.base), 2))
                p.setBrush(qcolor(t.accent.subtle))
            else:
                p.setPen(QPen(qcolor(t.border.strong), 1.5))
                p.setBrush(qcolor(t.surface.surface2))
            p.drawEllipse(QRectF(x - 13, cy - 13, 26, 26))
            if done:
                icons.icon("check", t.accent.fg).paint(p, int(x - 8), int(cy - 8), 16, 16)
            else:
                p.setFont(ui_font(TypeStyle(12, 600, 16)))
                p.setPen(qcolor(t.accent.text if cur else t.text.tertiary))
                p.drawText(QRectF(x - 13, cy - 13, 26, 26), Qt.AlignCenter, str(i + 1))
            summary = self.summaries[i] if (done and i < len(self.summaries)) else ""
            f = ui_font(TYPE.body_strong if cur else TYPE.body)
            p.setFont(f)
            p.setPen(qcolor(t.text.primary if (cur or done) else t.text.secondary))
            p.drawText(QRectF(x + 24, cy - (17 if summary else 10), 160, 20),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(f).elidedText(name, Qt.ElideRight, 160))
            if summary:
                f = ui_font(TYPE.caption)
                p.setFont(f)
                p.setPen(qcolor(t.text.tertiary))
                el = QFontMetricsF(f).elidedText(summary, Qt.ElideRight, 160)
                p.drawText(QRectF(x + 24, cy + 1, 160, 18), Qt.AlignLeft | Qt.AlignVCenter, el)


class DropZone(ThemeAware, QWidget):
    """Dashed drop target; click to browse."""

    files_added = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hot = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(128)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("Drop TIFF / PNG / JPEG / BMP images here, or click to browse")
        self._connect_theme()

    @staticmethod
    def _paths(md) -> List[str]:
        return [u.toLocalFile() for u in md.urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in IMAGE_EXTS]

    def dragEnterEvent(self, e) -> None:
        if self._paths(e.mimeData()):
            self._hot = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:
        self._hot = False
        self.update()

    def dropEvent(self, e) -> None:
        self._hot = False
        self.update()
        paths = self._paths(e.mimeData())
        if paths:
            self.files_added.emit(paths)

    def mouseReleaseEvent(self, e) -> None:
        self.browse()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.browse()
        else:
            super().keyPressEvent(e)

    def browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add SEM images", "", IMAGE_FILTER)
        if paths:
            self.files_added.emit(paths)

    def paintEvent(self, _e) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(qcolor(t.accent.subtle if self._hot else t.surface.surface2))
        p.setPen(QPen(qcolor(t.accent.base if (self._hot or self.hasFocus()) else t.border.strong),
                      1.5, Qt.DashLine))
        p.drawRoundedRect(r, RADII.lg, RADII.lg)
        icons.icon("upload", t.accent.text).paint(p, int(r.center().x() - 14), int(r.top() + 26), 28, 28)
        p.setFont(ui_font(TYPE.body_strong))
        p.setPen(qcolor(t.text.primary))
        p.drawText(QRectF(r.left(), r.top() + 62, r.width(), 20), Qt.AlignCenter,
                   "Drop SEM images here")
        p.setFont(ui_font(TYPE.caption))
        p.setPen(qcolor(t.text.secondary))
        p.drawText(QRectF(r.left(), r.top() + 84, r.width(), 18), Qt.AlignCenter,
                   "or click to browse · TIFF, PNG, JPEG, BMP")


def _create_session_worker(root: str, v: dict, images: List[str]) -> Path:
    """Pool thread: create the missing levels, then copy the images —
    into the lot folder itself (lot mode, appending) or a new run folder."""
    ws = Workspace(root)
    profile = ws.profile
    f = v.get("fields") or {}
    pp = Path(v["project_path"]) if v.get("project_path") else ws.create_project(
        v["project_name"], **(f.get("project") or {}))
    sp = Path(v["sample_path"]) if v.get("sample_path") else ws.create_sample(
        pp, v["sample_id"], **(f.get("sample") or {}))
    lp = Path(v["lot_path"]) if v.get("lot_path") else ws.create_lot(
        pp, sp, v["lot_number"], **(f.get("lot") or {}))
    from version import __version__
    meta = {"operator": v.get("operator", ""), "software_version": __version__}
    for k in ("instrument", "magnification", "notes"):
        if v.get(k):
            meta[k] = v[k]
    if float(v.get("kv") or 0.0) > 0:
        meta["accelerating_voltage_kv"] = float(v["kv"])
    if float(v.get("wd") or 0.0) > 0:
        meta["working_distance_mm"] = float(v["wd"])
    entries = [ImageEntry(source_path=p) for p in images]
    if hui.lot_mode(profile):
        ref = save_session(lp, meta, entries, in_place=True, catalog=Catalog(root),
                           image_name_template=profile.image_name_template,
                           name_context=context_for_session(lp, profile))
    else:
        ref = save_session(lp, meta, entries, label=v.get("label", ""), catalog=Catalog(root),
                           image_name_template=profile.image_name_template,
                           name_context={"project": v.get("project_name", ""),
                                         "sample": v.get("sample_id", ""),
                                         "lot": v.get("lot_number", ""),
                                         "label": v.get("label", "")})
    return ref.path


def _read_first_metadata(paths: List[str]) -> dict:
    """Pool thread: acquisition details from the first image with metadata."""
    from core.sem_metadata import read_sem_metadata
    for p in paths[:5]:
        md = read_sem_metadata(p)
        if md is not None:
            return md.to_dict()
    return {}


class _LevelPage:
    """Widgets of one hierarchy step (picker + new-folder form)."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.widget: Optional[QWidget] = None
        self.combo: Optional[QComboBox] = None
        self.summary: Optional[KeyValueList] = None
        self.new_host: Optional[QWidget] = None
        self.id_edit: Optional[QLineEdit] = None
        self.edits: Dict[str, QWidget] = {}
        self.fields: List[FieldDef] = []


class NewSessionWizard(QDialog):
    session_created = Signal(object)   # Path of the lot record / new session

    def __init__(self, state, prefill: Optional[dict] = None,
                 images: Optional[List[str]] = None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.profile = state.profile
        self.lot_mode = hui.lot_mode(self.profile)
        self.steps_def = wizard_steps(self.profile)
        L = lambda k: hui.kind_label(self.profile, k)  # noqa: E731
        self.setWindowTitle(f"Add images to a {L('lot')}" if self.lot_mode
                            else "New analysis session")
        self.setModal(True)
        self.resize(920, 640)
        self.setMinimumSize(780, 560)
        self._images: List[str] = []
        self._step = 0
        self._busy = False
        self._acq_auto = False
        self.created_path: Optional[Path] = None
        mem = dict(state.ui_state.get("wizard", {}))
        self._mem = mem
        self.levels: Dict[str, _LevelPage] = {k: _LevelPage(k) for k in hui.LEVELS}
        self._build()
        self._load_projects()
        self._apply_memory(mem, prefill or {})
        if images:
            self.add_images(images)
        self._go(0)

    # ------------------------------------------------------------------ compat
    @property
    def project_combo(self) -> QComboBox:
        return self.levels["project"].combo

    @property
    def sample_combo(self) -> QComboBox:
        return self.levels["sample"].combo

    @property
    def lot_combo(self) -> QComboBox:
        return self.levels["lot"].combo

    @property
    def f_project(self) -> QLineEdit:
        return self.levels["project"].id_edit

    @property
    def f_sample(self) -> QLineEdit:
        return self.levels["sample"].id_edit

    @property
    def f_lot(self) -> QLineEdit:
        return self.levels["lot"].id_edit

    def step_titles(self) -> List[str]:
        return [s[1] for s in self.steps_def]

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        L = lambda k: hui.kind_label(self.profile, k)  # noqa: E731
        self.steps = StepList(self.steps_def,
                              f"ADD TO A {L('lot').upper()}" if self.lot_mode else "NEW SESSION")
        root.addWidget(self.steps)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(SPACE.xxl, SPACE.xl, SPACE.xxl, SPACE.lg)
        rv.setSpacing(SPACE.md)
        self.step_lbl = label("", "overline")
        self.title = label("", "h1")
        self.subtitle = label("", tone="secondary")
        self.subtitle.setWordWrap(True)
        rv.addWidget(self.step_lbl)
        rv.addWidget(self.title)
        rv.addWidget(self.subtitle)
        rv.addSpacing(SPACE.sm)
        self.pages = FadeStackedWidget()
        for key, _t, _s in self.steps_def:
            if key in hui.LEVELS:
                self.pages.addWidget(self._level_page(key))
            elif key == "session":
                self.pages.addWidget(self._session_page())
            else:
                self.pages.addWidget(self._images_page())
        rv.addWidget(self.pages, 1)
        self.error = label("", tone="danger")
        self.error.setWordWrap(True)
        self.error.hide()
        rv.addWidget(self.error)
        foot = QHBoxLayout()
        self.cancel_btn = AnimatedButton("Cancel", None, "ghost")
        self.cancel_btn.setToolTip("Close without creating anything (Esc)")
        self.cancel_btn.clicked.connect(self.reject)
        foot.addWidget(self.cancel_btn)
        foot.addStretch(1)
        self.back_btn = AnimatedButton("Back", "chevron_left", "secondary")
        self.back_btn.setToolTip("Previous step")
        self.back_btn.clicked.connect(lambda: self._go(self._step - 1))
        self.next_btn = AnimatedButton("Next", "chevron_right", "primary")
        self.next_btn.setToolTip("Next step (Enter)")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        foot.addWidget(self.back_btn)
        foot.addWidget(self.next_btn)
        rv.addLayout(foot)
        root.addWidget(right, 1)

    @staticmethod
    def _form() -> QFormLayout:
        f = QFormLayout()
        f.setHorizontalSpacing(SPACE.lg)
        f.setVerticalSpacing(SPACE.md)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return f

    def _line(self, tip: str, placeholder: str = "") -> QLineEdit:
        e = QLineEdit()
        e.setToolTip(tip)
        e.setPlaceholderText(placeholder)
        e.returnPressed.connect(self._next)
        return e

    _PLACEHOLDERS = {"project": "e.g. 24-117", "sample": "e.g. 7718-A", "lot": "e.g. L-44A"}

    def _level_page(self, kind: str) -> QWidget:
        lp = self.levels[kind]
        L = hui.kind_label(self.profile, kind)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.md)
        combo = QComboBox()
        combo.setToolTip(f"Pick an existing {L} or create a new one")
        combo.setMinimumWidth(360)
        combo.setAccessibleName(f"{L} picker")
        f = self._form()
        f.addRow("Choose", combo)
        v.addLayout(f)
        summary = KeyValueList()
        new_host = QWidget()
        nf = self._form()
        nf.setContentsMargins(0, 0, 0, 0)
        new_host.setLayout(nf)
        idl = hui.id_label(self.profile, kind)
        lp.id_edit = self._line(f"{idl} — names the {L} folder",
                                self._PLACEHOLDERS.get(kind, "") if self.lot_mode else "")
        lp.id_edit.setAccessibleName(idl)
        nf.addRow(f"{idl}  *", lp.id_edit)
        lp.fields = hui.level_fields(self.profile, kind)
        for fd in lp.fields:
            ed = make_editor(fd, "")
            if isinstance(ed, QLineEdit):
                ed.returnPressed.connect(self._next)
            lp.edits[fd.key] = ed
            nf.addRow(fd.label + ("  *" if fd.required else ""), ed)
        v.addWidget(summary)
        v.addWidget(new_host)
        v.addStretch(1)
        lp.widget, lp.combo, lp.summary, lp.new_host = w, combo, summary, new_host
        combo.currentIndexChanged.connect(lambda _i, k=kind: self._on_level_changed(k))
        return w

    def _acq_form(self, with_label: bool) -> QFormLayout:
        f = self._form()
        self.f_label = self._line("Short label shown on the session card (optional)",
                                  "e.g. Transverse section, 500×")
        self.f_operator = self._line("Who acquired / analysed the images")
        self.f_instrument = self._line("Microscope (read from the image metadata when "
                                       "available)", "e.g. Zeiss Sigma 300")
        self.f_mag = self._line("Nominal magnification", "e.g. 1000×")
        self.f_kv = QDoubleSpinBox()
        self.f_kv.setRange(0, 60)
        self.f_kv.setDecimals(1)
        self.f_kv.setSuffix(" kV")
        self.f_kv.setToolTip("Accelerating voltage")
        self.f_wd = QDoubleSpinBox()
        self.f_wd.setRange(0, 100)
        self.f_wd.setDecimals(1)
        self.f_wd.setSuffix(" mm")
        self.f_wd.setToolTip("Working distance")
        self.f_notes = QPlainTextEdit()
        self.f_notes.setFixedHeight(64 if with_label else 48)
        self.f_notes.setToolTip("Free-text notes (etchant, polishing, observations …)")
        if with_label:
            f.addRow("Session label", self.f_label)
        f.addRow("Operator", self.f_operator)
        f.addRow("Instrument", self.f_instrument)
        f.addRow("Magnification", self.f_mag)
        f.addRow("Accelerating voltage", self.f_kv)
        f.addRow("Working distance", self.f_wd)
        f.addRow("Notes", self.f_notes)
        return f

    def _session_page(self) -> QWidget:
        w = QWidget()
        w.setLayout(self._acq_form(True))
        return w

    def _images_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.md)
        self.dest = label("", "caption")
        self.dest.setWordWrap(True)
        v.addWidget(self.dest)
        self.drop = DropZone()
        self.drop.files_added.connect(self.add_images)
        v.addWidget(self.drop)
        self.img_list = QListWidget()
        self.img_list.setToolTip("Images that will be copied into the folder. "
                                 "Select and press Delete to remove.")
        self.img_list.setSelectionMode(QListWidget.ExtendedSelection)
        v.addWidget(self.img_list, 1)
        row = QHBoxLayout()
        self.img_count = label("No images yet — you can also add them later.", "caption")
        row.addWidget(self.img_count, 1)
        rm = AnimatedButton("Remove selected", "remove", "ghost", "sm")
        rm.setToolTip("Remove the selected images from this list")
        rm.clicked.connect(self._remove_selected)
        row.addWidget(rm)
        v.addLayout(row)
        if self.lot_mode:
            self.acq = CollapsibleSection("Acquisition details", expanded=False)
            self.acq.setToolTip("Operator, instrument, kV, WD — filled from the SEM image "
                                "metadata when the images carry it")
            host = QWidget()
            host.setLayout(self._acq_form(False))
            self.acq.add_widget(host)
            v.addWidget(self.acq)
        return w

    # ------------------------------------------------------------------ data
    def _ws(self) -> Workspace:
        return self.state.workspace

    def _level_text(self, kind: str, meta: dict, path: Path) -> str:
        return hui.node_caption(self.profile, kind, meta, path)

    def _load_projects(self) -> None:
        combo = self.project_combo
        combo.blockSignals(True)
        combo.clear()
        for pm in self._ws().list_projects():
            pp = Path(pm.path)
            combo.addItem(icons.icon("projects"),
                          self._level_text("project", hui.read_meta("project", pp), pp), pm.path)
        combo.addItem(icons.icon("add"),
                      f"Create a new {hui.kind_label(self.profile, 'project')}…", NEW)
        combo.blockSignals(False)
        self._on_level_changed("project")

    def _summary_rows(self, kind: str, path: Path) -> List[tuple]:
        m = hui.read_meta(kind, path)
        rows = [(fd.label, str(m.get(fd.key) or "—")) for fd in self.levels[kind].fields]
        if kind == "lot" and self.lot_mode:
            mm = hui.read_meta("session", path)
            n = len(mm.get("images", []) or [])
            rows.append(("Images", f"{n} already in this {hui.kind_label(self.profile, 'lot')}"
                         if n else "None yet"))
        rows.append(("Folder", str(path)))
        return rows

    def _on_level_changed(self, kind: str) -> None:
        lp = self.levels[kind]
        d = lp.combo.currentData()
        new = d == NEW or d is None
        lp.new_host.setVisible(new)
        lp.summary.setVisible(not new)
        if not new:
            lp.summary.set_items(self._summary_rows(kind, Path(d)))
        idx = hui.LEVELS.index(kind)
        if idx == len(hui.LEVELS) - 1:
            if hasattr(self, "img_count"):
                self._update_count()
            return
        child = hui.LEVELS[idx + 1]
        cc = self.levels[child].combo
        cc.blockSignals(True)
        cc.clear()
        if not new:
            p = Path(d)
            if child == "sample":
                rows = self._ws().list_samples(p)
            else:
                rows = self._ws().list_lots(p.parent, p)
            for m in rows:
                cp = Path(m.path)
                cc.addItem(icons.icon(hui.KIND_ICON[child]),
                           self._level_text(child, hui.read_meta(child, cp), cp), m.path)
        cc.addItem(icons.icon("add"), f"Create a new {hui.kind_label(self.profile, child)}…", NEW)
        cc.blockSignals(False)
        self._on_level_changed(child)

    # legacy slot names (tests / callers)
    def _on_project_changed(self, *_a) -> None:
        self._on_level_changed("project")

    def _on_sample_changed(self, *_a) -> None:
        self._on_level_changed("sample")

    def _on_lot_changed(self, *_a) -> None:
        self._on_level_changed("lot")

    @staticmethod
    def _select_data(combo: QComboBox, data) -> bool:
        for i in range(combo.count()):
            if str(combo.itemData(i)) == str(data):
                combo.setCurrentIndex(i)
                return True
        return False

    @staticmethod
    def _select_text(combo: QComboBox, text: str) -> bool:
        i = combo.findText(text)
        if i >= 0:
            combo.setCurrentIndex(i)
            return True
        return False

    def _lot_text(self, lot_id: str) -> str:
        return f"{hui.kind_label(self.profile, 'lot')} {lot_id}"

    def _apply_memory(self, mem: dict, prefill: dict) -> None:
        if prefill.get("project_path"):
            self._select_data(self.project_combo, prefill["project_path"])
        elif mem.get("project"):
            self._select_text(self.project_combo, mem["project"])
        elif self.project_combo.count() > 1:
            self.project_combo.setCurrentIndex(0)
        if prefill.get("sample_path"):
            self._select_data(self.sample_combo, prefill["sample_path"])
        elif mem.get("sample"):
            self._select_text(self.sample_combo, mem["sample"])
        if prefill.get("lot_path"):
            self._select_data(self.lot_combo, prefill["lot_path"])
        elif mem.get("lot"):
            self._select_text(self.lot_combo, self._lot_text(mem["lot"]))
        self.f_operator.setText(mem.get("operator") or self.state.operator())
        self.f_instrument.setText(mem.get("instrument", ""))
        self.f_mag.setText(mem.get("magnification", ""))
        self.f_kv.setValue(float(mem.get("kv", 0.0) or 0.0))
        self.f_wd.setValue(float(mem.get("wd", 0.0) or 0.0))
        if prefill.get("label"):
            self.f_label.setText(prefill["label"])

    def _set_level_field(self, key: str, value) -> bool:
        key = _ALIASES.get(key, key)
        for kind in hui.LEVELS:
            ed = self.levels[kind].edits.get(key)
            if ed is not None:
                set_editor_value(ed, value)
                return True
        return False

    def fill(self, project: str = "", sample: str = "", lot: str = "", label_text: str = "",
             **extra) -> None:
        """Programmatic fill (tests / scripted demo). Existing names are reused.
        Extra keywords set level fields by key (``heat_number=…``, ``customer=…``)
        or acquisition fields (operator, instrument, magnification, kv, wd, notes)."""
        if project and not self._select_text(self.project_combo, project):
            self._select_data(self.project_combo, NEW)
            self.f_project.setText(project)
        if sample and not self._select_text(self.sample_combo, sample):
            self._select_data(self.sample_combo, NEW)
            self.f_sample.setText(sample)
        if lot and not self._select_text(self.lot_combo, self._lot_text(lot)):
            self._select_data(self.lot_combo, NEW)
            self.f_lot.setText(lot)
        if label_text:
            self.f_label.setText(label_text)
        mapping = {"operator": self.f_operator, "instrument": self.f_instrument,
                   "magnification": self.f_mag}
        for k, v in extra.items():
            if k in mapping:
                mapping[k].setText(str(v))
            elif k == "kv":
                self.f_kv.setValue(float(v))
            elif k == "wd":
                self.f_wd.setValue(float(v))
            elif k == "notes":
                self.f_notes.setPlainText(str(v))
            elif k == "lot_notes":
                self._set_level_field("notes", v)
            else:
                self._set_level_field(k, v)

    def add_images(self, paths: List[str]) -> None:
        new = []
        for p in paths:
            if p in self._images:
                continue
            self._images.append(p)
            new.append(p)
            it = QListWidgetItem(icons.icon("image"), Path(p).name)
            it.setToolTip(p)
            self.img_list.addItem(it)
        self._update_count()
        if new:
            run_task(_read_first_metadata, list(new), on_done=self._prefill_acquisition)

    def _prefill_acquisition(self, md: dict) -> None:
        """INN-05: instrument / kV / WD / mag from the SEM metadata (only
        fields the operator left empty)."""
        if not md:
            return
        try:
            if md.get("instrument") and not self.f_instrument.text().strip():
                self.f_instrument.setText(str(md["instrument"]))
            if md.get("accelerating_voltage_kv") and not self.f_kv.value():
                self.f_kv.setValue(float(md["accelerating_voltage_kv"]))
            if md.get("working_distance_mm") and not self.f_wd.value():
                self.f_wd.setValue(float(md["working_distance_mm"]))
            if md.get("magnification") and not self.f_mag.text().strip():
                self.f_mag.setText(f"{float(md['magnification']):g}×")
            self._acq_auto = True
            self._update_count()
        except RuntimeError:
            pass    # dialog closed meanwhile

    def images(self) -> List[str]:
        return list(self._images)

    def _remove_selected(self) -> None:
        for it in self.img_list.selectedItems():
            self._images.remove(it.toolTip())
            self.img_list.takeItem(self.img_list.row(it))
        self._update_count()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Delete and self.img_list.hasFocus():
            self._remove_selected()
            return
        super().keyPressEvent(e)

    def _update_count(self) -> None:
        n = len(self._images)
        where = "the session"
        if self.lot_mode:
            L = hui.kind_label(self.profile, "lot")
            where = f"this {L}"
            if self.lot_combo.currentData() not in (NEW, None):
                mm = hui.read_meta("session", Path(self.lot_combo.currentData()))
                have = len(mm.get("images", []) or [])
                if have:
                    where += f" (added to its {have} image{'s' if have != 1 else ''})"
        txt = (f"{n} image{'s' if n != 1 else ''} will be copied into {where}."
               if n else "No images yet — you can also add them later.")
        if self._acq_auto:
            txt += "  Acquisition details were read from the image metadata."
        self.img_count.setText(txt)

    # ------------------------------------------------------------------ navigation
    def _go(self, i: int) -> None:
        n = len(self.steps_def)
        i = max(0, min(n - 1, i))
        self._step = i
        self.steps.set_current(i)
        self.pages.set_current_index(i)
        _key, name, sub = self.steps_def[i]
        self.step_lbl.setText(f"STEP {i + 1} OF {n}")
        self.title.setText(name)
        self.subtitle.setText(sub)
        self.back_btn.setVisible(i > 0)
        self._update_summaries()
        last = i == n - 1
        L = hui.kind_label(self.profile, "lot")
        lot_exists = self.lot_combo.currentData() not in (NEW, None)
        final = (f"Add to {L}" if lot_exists else f"Create {L}") if self.lot_mode \
            else "Create session"
        self.next_btn.setText(final if last else "Next")
        self.next_btn.set_icon_name("check" if last else "chevron_right")
        self.next_btn.setToolTip("Create the folders, copy the images and open Analyze"
                                 if last else "Next step (Enter)")
        if last:
            self._update_count()
        self.error.hide()

    def _names(self):
        v = self.values()
        proj = self.project_combo.currentText() if v["project_path"] else v["project_name"]
        samp = self.sample_combo.currentText() if v["sample_path"] else v["sample_id"]
        lot = self.lot_combo.currentText() if v["lot_path"] else (
            self._lot_text(v["lot_number"]) if v["lot_number"] else "")
        return v, proj, samp, lot

    def _update_summaries(self) -> None:
        v, proj, samp, lot = self._names()
        n = len(self._images)
        texts = [proj, samp, lot]
        if not self.lot_mode:
            texts.append(" · ".join(x for x in (v["label"], v["operator"]) if x))
        texts.append(f"{n} image{'s' if n != 1 else ''}")
        self.steps.set_summaries(texts)
        if proj:
            L = lambda k: hui.kind_label(self.profile, k)  # noqa: E731
            self.dest.setText(f"Stored in  {L('project')} {proj}  ›  {L('sample')} {samp}  ›  {lot}")
        else:
            self.dest.setText("")

    def _invalid(self, w: QWidget, msg: str) -> bool:
        mark_invalid(w, True)
        w.setFocus()
        self.error.setText(msg)
        self.error.show()
        return False

    def _validate(self, i: int) -> bool:
        for kind in hui.LEVELS:
            lp = self.levels[kind]
            for w in [lp.id_edit] + list(lp.edits.values()):
                if w.property("invalid") == "true":
                    mark_invalid(w, False)
        if i >= len(hui.LEVELS):
            return True
        kind = hui.LEVELS[i]
        lp = self.levels[kind]
        if lp.combo.currentData() != NEW:
            return True
        if not lp.id_edit.text().strip():
            return self._invalid(lp.id_edit, f"Enter the {hui.id_label(self.profile, kind)}.")
        for fd in lp.fields:
            if fd.required and not editor_value(lp.edits[fd.key]):
                return self._invalid(lp.edits[fd.key], f"{fd.label} is required.")
        return True

    def _next(self) -> None:
        if self._busy:
            return
        if not self._validate(self._step):
            return
        if self._step < len(self.steps_def) - 1:
            self._go(self._step + 1)
        else:
            self.create_session()

    def values(self) -> dict:
        def pick(combo):
            d = combo.currentData()
            return None if d in (NEW, None) else d
        fields = {k: {key: editor_value(w) for key, w in self.levels[k].edits.items()}
                  for k in hui.LEVELS}
        return {
            "project_path": pick(self.project_combo), "project_name": self.f_project.text().strip(),
            "sample_path": pick(self.sample_combo) if pick(self.project_combo) else None,
            "sample_id": self.f_sample.text().strip(),
            "lot_path": pick(self.lot_combo) if (pick(self.project_combo) and pick(self.sample_combo)) else None,
            "lot_number": self.f_lot.text().strip(),
            "fields": fields,
            "label": self.f_label.text().strip() if not self.lot_mode else "",
            "operator": self.f_operator.text().strip(),
            "instrument": self.f_instrument.text().strip(), "magnification": self.f_mag.text().strip(),
            "kv": self.f_kv.value(), "wd": self.f_wd.value(),
            "notes": self.f_notes.toPlainText().strip(),
        }

    def create_session(self) -> None:
        for i in range(len(hui.LEVELS)):
            if not self._validate(i):
                self._go(i)
                self._validate(i)
                return
        v = self.values()
        self._remember(v)
        self._busy = True
        self.next_btn.set_loading(True)
        self.back_btn.setEnabled(False)
        run_task(_create_session_worker, str(self.state.root), v, list(self._images),
                 on_done=self._created, on_error=self._failed)

    def _remember(self, v: dict) -> None:
        lot_prefix = self._lot_text("")
        lot_txt = self.lot_combo.currentText()
        mem = {
            "project": self.project_combo.currentText() if v["project_path"] else v["project_name"],
            "sample": self.sample_combo.currentText() if v["sample_path"] else v["sample_id"],
            "lot": (lot_txt[len(lot_prefix):] if (v["lot_path"] and lot_txt.startswith(lot_prefix))
                    else (lot_txt if v["lot_path"] else v["lot_number"])),
            "operator": v["operator"], "instrument": v["instrument"],
            "magnification": v["magnification"], "kv": v["kv"], "wd": v["wd"],
        }
        self.state.ui_state["wizard"] = mem
        self.state.persist_ui_state()
        if v["operator"] and not self.state.settings.operator:
            self.state.settings.operator = v["operator"]
            self.state.save_settings()

    def _created(self, path: Path) -> None:
        self._busy = False
        self.next_btn.set_loading(False)
        self.created_path = Path(path)
        self.session_created.emit(self.created_path)
        self.accept()

    def _failed(self, msg: str) -> None:
        self._busy = False
        self.next_btn.set_loading(False)
        self.back_btn.setEnabled(True)
        what = hui.kind_label(self.profile, "lot") if self.lot_mode else "session"
        self.error.setText(f"Could not create the {what}: " + msg.splitlines()[0])
        self.error.show()

    def reject(self) -> None:
        if self._busy:
            return
        super().reject()


__all__ = ["NewSessionWizard", "DropZone", "StepList", "wizard_steps", "STEPS", "NEW"]
