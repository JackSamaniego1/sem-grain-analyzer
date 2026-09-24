"""
New Session wizard (DATA-07, Ctrl+N).

Project → Sample → Lot → Session details → Images.  Each of the first three
steps picks an existing folder or creates a new one; the last step accepts
drag-and-drop.  Last-used values are remembered (ui_state["wizard"]).  The
folders + image copies are created off the GUI thread through the data layer
(``Workspace`` + ``save_session``); ``session_created(Path)`` fires when done.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget,
)

from data.catalog import Catalog
from data.models import ImageEntry, default_operator
from data.session_io import save_session
from data.workspace import Workspace
from ui.design import icons
from ui.design.theme import ui_font
from ui.design.tokens import RADII, SPACE, TYPE, TypeStyle
from ui.pages.common import Panel
from ui.widgets import AnimatedButton, FadeStackedWidget, KeyValueList, label
from ui.widgets._base import ThemeAware, qcolor, tokens
from ui.workers import IMAGE_EXTS, IMAGE_FILTER, run_task

STEPS = [("Project", "Which project does this work belong to?"),
         ("Sample", "Which sample is being examined?"),
         ("Lot", "Which lot (heat / batch) of that sample?"),
         ("Session", "Acquisition details for this sitting at the microscope"),
         ("Images", "Add the SEM images to analyse")]

NEW = "__new__"


class StepList(ThemeAware, QWidget):
    """Vertical stepper: numbered discs joined by a line."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current = 0
        self.summaries = [""] * len(STEPS)
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
        p.drawText(QRectF(24, 24, 180, 16), Qt.AlignLeft, "NEW SESSION")
        y0, dy, x = 64, 58, 36
        for i, (name, _sub) in enumerate(STEPS):
            cy = y0 + i * dy
            if i < len(STEPS) - 1:
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
            summary = self.summaries[i] if done else ""
            p.setFont(ui_font(TYPE.body_strong if cur else TYPE.body))
            p.setPen(qcolor(t.text.primary if (cur or done) else t.text.secondary))
            p.drawText(QRectF(x + 24, cy - (17 if summary else 10), 160, 20),
                       Qt.AlignLeft | Qt.AlignVCenter, name)
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
    ws = Workspace(root)
    pp = Path(v["project_path"]) if v.get("project_path") else ws.create_project(
        v["project_name"], customer=v.get("customer", ""), description=v.get("project_description", ""))
    sp = Path(v["sample_path"]) if v.get("sample_path") else ws.create_sample(
        pp, v["sample_id"], material=v.get("material", ""), alloy_grade=v.get("grade", ""),
        heat_treatment=v.get("heat_treatment", ""))
    lp = Path(v["lot_path"]) if v.get("lot_path") else ws.create_lot(
        pp, sp, v["lot_number"], supplier=v.get("supplier", ""),
        received_date=v.get("received_date", ""), notes=v.get("lot_notes", ""))
    from version import __version__
    meta = {"operator": v.get("operator", ""), "instrument": v.get("instrument", ""),
            "magnification": v.get("magnification", ""),
            "accelerating_voltage_kv": float(v.get("kv") or 0.0),
            "working_distance_mm": float(v.get("wd") or 0.0),
            "notes": v.get("notes", ""), "software_version": __version__}
    ref = save_session(lp, meta, [ImageEntry(source_path=p) for p in images],
                       label=v.get("label", ""), catalog=Catalog(root))
    return ref.path


class NewSessionWizard(QDialog):
    session_created = Signal(object)   # Path of the new session

    def __init__(self, state, prefill: Optional[dict] = None,
                 images: Optional[List[str]] = None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("New analysis session")
        self.setModal(True)
        self.resize(900, 620)
        self.setMinimumSize(780, 560)
        self._images: List[str] = []
        self._step = 0
        self._busy = False
        self.created_path: Optional[Path] = None
        mem = dict(state.ui_state.get("wizard", {}))
        self._mem = mem
        self._build()
        self._load_projects()
        self._apply_memory(mem, prefill or {})
        if images:
            self.add_images(images)
        self._go(0)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.steps = StepList()
        root.addWidget(self.steps)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(SPACE.xxl, SPACE.xl, SPACE.xxl, SPACE.lg)
        rv.setSpacing(SPACE.md)
        self.step_lbl = label("", "overline")
        self.title = label("", "h1")
        self.subtitle = label("", tone="secondary")
        rv.addWidget(self.step_lbl)
        rv.addWidget(self.title)
        rv.addWidget(self.subtitle)
        rv.addSpacing(SPACE.sm)
        self.pages = FadeStackedWidget()
        self.pages.addWidget(self._project_page())
        self.pages.addWidget(self._sample_page())
        self.pages.addWidget(self._lot_page())
        self.pages.addWidget(self._session_page())
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

    def _picker_page(self, combo_tip: str):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.md)
        combo = QComboBox()
        combo.setToolTip(combo_tip)
        combo.setMinimumWidth(360)
        f = self._form()
        f.addRow("Choose", combo)
        v.addLayout(f)
        summary = KeyValueList()
        new_host = QWidget()
        nf = self._form()
        nf.setContentsMargins(0, 0, 0, 0)
        new_host.setLayout(nf)
        v.addWidget(summary)
        v.addWidget(new_host)
        v.addStretch(1)
        return w, combo, summary, new_host, nf

    def _project_page(self) -> QWidget:
        w, self.project_combo, self.project_summary, self.project_new, f = \
            self._picker_page("Pick an existing project or create a new one")
        self.f_project = self._line("Project name, e.g. a customer, programme or alloy study",
                                    "e.g. Alloy 718 qualification")
        self.f_customer = self._line("Customer or programme (optional)")
        self.f_pdesc = self._line("Short description (optional)")
        f.addRow("Project name  *", self.f_project)
        f.addRow("Customer", self.f_customer)
        f.addRow("Description", self.f_pdesc)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        return w

    def _sample_page(self) -> QWidget:
        w, self.sample_combo, self.sample_summary, self.sample_new, f = \
            self._picker_page("Pick an existing sample of this project or create a new one")
        self.f_sample = self._line("Sample identifier as written on the specimen", "e.g. S-014")
        self.f_material = self._line("Material (optional)", "e.g. Inconel 718")
        self.f_grade = self._line("Alloy grade (optional)", "e.g. AMS 5662")
        self.f_ht = self._line("Heat treatment condition (optional)", "e.g. solution + aged")
        f.addRow("Sample ID  *", self.f_sample)
        f.addRow("Material", self.f_material)
        f.addRow("Grade", self.f_grade)
        f.addRow("Heat treatment", self.f_ht)
        self.sample_combo.currentIndexChanged.connect(self._on_sample_changed)
        return w

    def _lot_page(self) -> QWidget:
        w, self.lot_combo, self.lot_summary, self.lot_new, f = \
            self._picker_page("Pick an existing lot of this sample or create a new one")
        self.f_lot = self._line("Lot / heat / batch number", "e.g. 2026-0917-B")
        self.f_supplier = self._line("Supplier (optional)")
        self.f_received = QDateEdit(QDate.currentDate())
        self.f_received.setCalendarPopup(True)
        self.f_received.setDisplayFormat("yyyy-MM-dd")
        self.f_received.setToolTip("Date the material was received")
        self.f_lotnotes = self._line("Notes about this lot (optional)")
        f.addRow("Lot number  *", self.f_lot)
        f.addRow("Supplier", self.f_supplier)
        f.addRow("Received", self.f_received)
        f.addRow("Notes", self.f_lotnotes)
        self.lot_combo.currentIndexChanged.connect(self._on_lot_changed)
        return w

    def _session_page(self) -> QWidget:
        w = QWidget()
        f = self._form()
        w.setLayout(f)
        self.f_label = self._line("Short label shown on the session card (optional)",
                                  "e.g. Transverse section, 500×")
        self.f_operator = self._line("Who acquired / analysed the images")
        self.f_instrument = self._line("Microscope", "e.g. Zeiss Sigma 300")
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
        self.f_notes.setFixedHeight(80)
        self.f_notes.setToolTip("Free-text notes (etchant, polishing, observations …)")
        f.addRow("Session label", self.f_label)
        f.addRow("Operator", self.f_operator)
        f.addRow("Instrument", self.f_instrument)
        f.addRow("Magnification", self.f_mag)
        f.addRow("Accelerating voltage", self.f_kv)
        f.addRow("Working distance", self.f_wd)
        f.addRow("Notes", self.f_notes)
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
        self.img_list.setToolTip("Images that will be copied into the session folder. "
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
        return w

    # ------------------------------------------------------------------ data
    def _ws(self) -> Workspace:
        return self.state.workspace

    def _load_projects(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for pm in self._ws().list_projects():
            self.project_combo.addItem(icons.icon("projects"), pm.name or Path(pm.path).name, pm.path)
        self.project_combo.addItem(icons.icon("add"), "Create a new project…", NEW)
        self.project_combo.blockSignals(False)
        self._on_project_changed()

    def _on_project_changed(self, *_a) -> None:
        d = self.project_combo.currentData()
        new = d == NEW or d is None
        self.project_new.setVisible(new)
        self.project_summary.setVisible(not new)
        self.sample_combo.blockSignals(True)
        self.sample_combo.clear()
        if not new:
            from data.models import read_json
            m = read_json(Path(d) / "project.json")
            self.project_summary.set_items([("Customer", m.get("customer") or "—"),
                                            ("Description", m.get("description") or "—"),
                                            ("Folder", str(d))])
            for sm in self._ws().list_samples(Path(d)):
                self.sample_combo.addItem(icons.icon("sample"), sm.sample_id or Path(sm.path).name, sm.path)
        self.sample_combo.addItem(icons.icon("add"), "Create a new sample…", NEW)
        self.sample_combo.blockSignals(False)
        self._on_sample_changed()

    def _on_sample_changed(self, *_a) -> None:
        d = self.sample_combo.currentData()
        new = d == NEW or d is None
        self.sample_new.setVisible(new)
        self.sample_summary.setVisible(not new)
        self.lot_combo.blockSignals(True)
        self.lot_combo.clear()
        if not new:
            from data.models import read_json
            m = read_json(Path(d) / "sample.json")
            self.sample_summary.set_items([("Material", m.get("material") or "—"),
                                           ("Grade", m.get("alloy_grade") or "—"),
                                           ("Heat treatment", m.get("heat_treatment") or "—")])
            for lm in self._ws().list_lots(Path(d).parent, Path(d)):
                self.lot_combo.addItem(icons.icon("tag"), f"Lot {lm.lot_number or Path(lm.path).name}",
                                       lm.path)
        self.lot_combo.addItem(icons.icon("add"), "Create a new lot…", NEW)
        self.lot_combo.blockSignals(False)
        self._on_lot_changed()

    def _on_lot_changed(self, *_a) -> None:
        d = self.lot_combo.currentData()
        new = d == NEW or d is None
        self.lot_new.setVisible(new)
        self.lot_summary.setVisible(not new)
        if not new:
            from data.models import read_json
            m = read_json(Path(d) / "lot.json")
            self.lot_summary.set_items([("Supplier", m.get("supplier") or "—"),
                                        ("Received", m.get("received_date") or "—"),
                                        ("Notes", m.get("notes") or "—")])

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
            self._select_text(self.lot_combo, f"Lot {mem['lot']}")
        self.f_operator.setText(mem.get("operator") or self.state.operator())
        self.f_instrument.setText(mem.get("instrument", ""))
        self.f_mag.setText(mem.get("magnification", ""))
        self.f_kv.setValue(float(mem.get("kv", 0.0) or 0.0))
        self.f_wd.setValue(float(mem.get("wd", 0.0) or 0.0))
        if prefill.get("label"):
            self.f_label.setText(prefill["label"])

    def fill(self, project: str = "", sample: str = "", lot: str = "", label_text: str = "",
             **extra) -> None:
        """Programmatic fill (tests / scripted demo). Existing names are reused."""
        if project and not self._select_text(self.project_combo, project):
            self._select_data(self.project_combo, NEW)
            self.f_project.setText(project)
        if sample and not self._select_text(self.sample_combo, sample):
            self._select_data(self.sample_combo, NEW)
            self.f_sample.setText(sample)
        if lot and not self._select_text(self.lot_combo, f"Lot {lot}"):
            self._select_data(self.lot_combo, NEW)
            self.f_lot.setText(lot)
        if label_text:
            self.f_label.setText(label_text)
        mapping = {"material": self.f_material, "grade": self.f_grade,
                   "heat_treatment": self.f_ht, "supplier": self.f_supplier,
                   "customer": self.f_customer, "operator": self.f_operator,
                   "instrument": self.f_instrument, "magnification": self.f_mag}
        for k, v in extra.items():
            if k in mapping:
                mapping[k].setText(str(v))
            elif k == "kv":
                self.f_kv.setValue(float(v))
            elif k == "wd":
                self.f_wd.setValue(float(v))
            elif k == "notes":
                self.f_notes.setPlainText(str(v))

    def add_images(self, paths: List[str]) -> None:
        for p in paths:
            if p in self._images:
                continue
            self._images.append(p)
            it = QListWidgetItem(icons.icon("image"), Path(p).name)
            it.setToolTip(p)
            self.img_list.addItem(it)
        self._update_count()

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
        self.img_count.setText(f"{n} image{'s' if n != 1 else ''} will be copied into the session."
                               if n else "No images yet — you can also add them later.")

    # ------------------------------------------------------------------ navigation
    def _go(self, i: int) -> None:
        i = max(0, min(len(STEPS) - 1, i))
        self._step = i
        self.steps.set_current(i)
        self.pages.set_current_index(i)
        name, sub = STEPS[i]
        self.step_lbl.setText(f"STEP {i + 1} OF {len(STEPS)}")
        self.title.setText(name)
        self.subtitle.setText(sub)
        self.back_btn.setVisible(i > 0)
        self._update_summaries()
        last = i == len(STEPS) - 1
        self.next_btn.setText("Create session" if last else "Next")
        self.next_btn.set_icon_name("check" if last else "chevron_right")
        self.next_btn.setToolTip("Create the folders, copy the images and open Analyze"
                                 if last else "Next step (Enter)")
        self.error.hide()

    def _names(self):
        v = self.values()
        proj = self.project_combo.currentText() if v["project_path"] else v["project_name"]
        samp = self.sample_combo.currentText() if v["sample_path"] else v["sample_id"]
        lot = self.lot_combo.currentText() if v["lot_path"] else (
            f"Lot {v['lot_number']}" if v["lot_number"] else "")
        return v, proj, samp, lot

    def _update_summaries(self) -> None:
        v, proj, samp, lot = self._names()
        sess = " · ".join(x for x in (v["label"], v["operator"]) if x)
        n = len(self._images)
        self.steps.set_summaries([proj, samp, lot, sess, f"{n} image{'s' if n != 1 else ''}"])
        self.dest.setText(f"Stored in  {proj}  ›  {samp}  ›  {lot}" if proj else "")

    def _invalid(self, w: QWidget, msg: str) -> bool:
        w.setProperty("invalid", "true")
        w.style().unpolish(w)
        w.style().polish(w)
        w.setFocus()
        self.error.setText(msg)
        self.error.show()
        return False

    def _validate(self, i: int) -> bool:
        for w in (self.f_project, self.f_sample, self.f_lot):
            if w.property("invalid") == "true":
                w.setProperty("invalid", "false")
                w.style().unpolish(w)
                w.style().polish(w)
        if i == 0 and self.project_combo.currentData() == NEW and not self.f_project.text().strip():
            return self._invalid(self.f_project, "Enter a project name.")
        if i == 1 and self.sample_combo.currentData() == NEW and not self.f_sample.text().strip():
            return self._invalid(self.f_sample, "Enter the sample ID.")
        if i == 2 and self.lot_combo.currentData() == NEW and not self.f_lot.text().strip():
            return self._invalid(self.f_lot, "Enter the lot number.")
        return True

    def _next(self) -> None:
        if self._busy:
            return
        if not self._validate(self._step):
            return
        if self._step < len(STEPS) - 1:
            self._go(self._step + 1)
        else:
            self.create_session()

    def values(self) -> dict:
        def pick(combo):
            d = combo.currentData()
            return None if d in (NEW, None) else d
        return {
            "project_path": pick(self.project_combo), "project_name": self.f_project.text().strip(),
            "customer": self.f_customer.text().strip(), "project_description": self.f_pdesc.text().strip(),
            "sample_path": pick(self.sample_combo) if pick(self.project_combo) else None,
            "sample_id": self.f_sample.text().strip(), "material": self.f_material.text().strip(),
            "grade": self.f_grade.text().strip(), "heat_treatment": self.f_ht.text().strip(),
            "lot_path": pick(self.lot_combo) if (pick(self.project_combo) and pick(self.sample_combo)) else None,
            "lot_number": self.f_lot.text().strip(), "supplier": self.f_supplier.text().strip(),
            "received_date": self.f_received.date().toString("yyyy-MM-dd"),
            "lot_notes": self.f_lotnotes.text().strip(),
            "label": self.f_label.text().strip(), "operator": self.f_operator.text().strip(),
            "instrument": self.f_instrument.text().strip(), "magnification": self.f_mag.text().strip(),
            "kv": self.f_kv.value(), "wd": self.f_wd.value(),
            "notes": self.f_notes.toPlainText().strip(),
        }

    def create_session(self) -> None:
        for i in range(3):
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
        mem = {
            "project": self.project_combo.currentText() if v["project_path"] else v["project_name"],
            "sample": self.sample_combo.currentText() if v["sample_path"] else v["sample_id"],
            "lot": (self.lot_combo.currentText()[4:] if v["lot_path"] else v["lot_number"]),
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
        self.error.setText("Could not create the session: " + msg.splitlines()[0])
        self.error.show()

    def reject(self) -> None:
        if self._busy:
            return
        super().reject()
