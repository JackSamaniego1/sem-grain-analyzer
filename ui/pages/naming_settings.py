"""
Settings ▸ "Folder structure & naming" (HIER-01).

Layout
  header   preset picker · "Unsaved changes" badge
  storage  where images live: in the lot folder | timestamped runs in the lot
  levels   three columns (outer → inner level): name, identifier label,
           folder-name template, metadata fields (label · type · required ·
           choices; add / remove / reorder)
  names    image name · export file name · report title · run folder name
           (each: editor + "insert field" menu + live preview + validation)
  footer   Rename existing folders to match… · Revert · Save

Nothing is written until Save; saving persists ``workspace.json`` through
``AppState.set_profile`` which relabels every page at once (no restart).
New names apply to NEW folders and exports only — renaming existing folders
is the explicit, previewed, undoable action in the footer.
"""
from __future__ import annotations

import copy
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLineEdit,
    QMenu, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from data.hierarchy import (
    FieldDef, HierarchyProfile, LevelDef, PRESETS, render_template, validate_template,
)
from ui import hierarchy_ui as hui
from ui.design.tokens import SPACE
from ui.widgets import AnimatedButton, Badge, Card, IconButton, SegmentedControl, label

PRESET_NAMES = {"job_part_lot": "Job # › Part Number › Lot (images in the lot)",
                "project_sample_lot_session": "Project › Sample › Lot › Session (timestamped runs)"}
TYPE_CHOICES = [("text", "Text"), ("number", "Number"), ("date", "Date"),
                ("choice", "Choice list"), ("multiline", "Multi-line text")]


def _profiles_equal(a: HierarchyProfile, b: HierarchyProfile) -> bool:
    return a.to_dict() == b.to_dict()


# ======================================================================
# One template editor: line edit + token menu + live preview + problems
# ======================================================================

class TemplateField(QWidget):
    changed = Signal(str)

    def __init__(self, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.xs)
        row = QHBoxLayout()
        row.setSpacing(SPACE.xs)
        self.edit = QLineEdit()
        self.edit.setToolTip(tooltip + "\nType text and {fields}; use the + button to insert one.")
        self.btn = IconButton("add", "Insert a field (e.g. {lot}, a level label, the date)")
        self.menu = QMenu(self)
        self.btn.setPopupMode(IconButton.InstantPopup)
        self.btn.setMenu(self.menu)
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn)
        v.addLayout(row)
        self.preview = label("", "caption")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.preview)
        self.problem = label("", "caption", tone="danger")
        self.problem.setWordWrap(True)
        self.problem.hide()
        v.addWidget(self.problem)
        self._ctx: dict = {}
        self._keys: List[str] = []
        self._for_filename = False
        self._suffix = ""
        self._prefix = ""
        self.edit.textChanged.connect(self._update)
        self.edit.textEdited.connect(self.changed)

    def set_tokens(self, tokens) -> None:
        self.menu.clear()
        for tok, text in tokens:
            act = self.menu.addAction(f"{text}    {tok}")
            act.triggered.connect(lambda _=False, t=tok: self.insert(t))

    def insert(self, token: str) -> None:
        self.edit.insert(token)
        self.edit.setFocus()
        self.changed.emit(self.edit.text())

    def set_context(self, ctx: dict, keys, for_filename: bool = False, suffix: str = "",
                    prefix: str = "") -> None:
        self._ctx, self._keys = dict(ctx), list(keys)
        self._for_filename, self._suffix, self._prefix = for_filename, suffix, prefix
        self._update()

    def text(self) -> str:
        return self.edit.text()

    def set_text(self, text: str) -> None:
        if self.edit.text() != text:
            self.edit.setText(text)

    def problems(self) -> List[str]:
        return validate_template(self.edit.text(), self._keys)

    def _update(self) -> None:
        probs = self.problems()
        self.problem.setText("  ·  ".join(probs))
        self.problem.setVisible(bool(probs))
        out = render_template(self.edit.text(), self._ctx, for_filename=self._for_filename)
        self.preview.setText(f"Preview:  {self._prefix}{out or '—'}{self._suffix if out else ''}")


# ======================================================================
# One level: labels, folder template, fields table
# ======================================================================

class LevelEditor(QWidget):
    changed = Signal()

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self._key = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE.sm)
        self.heading = label(f"Level {index + 1}", "overline", tone="secondary")
        v.addWidget(self.heading)
        g = QGridLayout()
        g.setHorizontalSpacing(SPACE.sm)
        g.setVerticalSpacing(SPACE.sm)
        g.setColumnStretch(1, 1)
        self.name = QLineEdit()
        self.name.setToolTip("What this level is called everywhere (tree, breadcrumb, wizard, "
                             "reports), e.g. “Job #”, “Part Number”, “Work Order”")
        self.id_name = QLineEdit()
        self.id_name.setToolTip("Label of the identifying value typed for each folder, "
                                "e.g. “Job number”")
        self.folder = TemplateField("Folder name for new folders of this level")
        g.addWidget(label("Name", tone="secondary"), 0, 0)
        g.addWidget(self.name, 0, 1)
        g.addWidget(label("Identifier", tone="secondary"), 1, 0)
        g.addWidget(self.id_name, 1, 1)
        g.addWidget(label("Folder name", tone="secondary"), 2, 0, Qt.AlignTop)
        g.addWidget(self.folder, 2, 1)
        v.addLayout(g)

        v.addWidget(label("Metadata fields", tone="secondary"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field", "Type", "Required", "Choices"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMinimumHeight(170)
        self.table.setToolTip("Extra information recorded for each folder of this level — "
                              "shown in the wizard, the details panel and the report")
        v.addWidget(self.table, 1)
        br = QHBoxLayout()
        br.setSpacing(SPACE.xs)
        self.btn_add = IconButton("add", "Add a field")
        self.btn_del = IconButton("remove", "Remove the selected field (values already "
                                            "saved in folders are kept)")
        self.btn_up = IconButton("arrow_up", "Move the selected field up")
        self.btn_down = IconButton("arrow_down", "Move the selected field down")
        for b in (self.btn_add, self.btn_del, self.btn_up, self.btn_down):
            br.addWidget(b)
        br.addStretch(1)
        v.addLayout(br)

        self.name.textEdited.connect(lambda _t: self.changed.emit())
        self.id_name.textEdited.connect(lambda _t: self.changed.emit())
        self.folder.changed.connect(lambda _t: self.changed.emit())
        self.table.itemChanged.connect(lambda _i: self.changed.emit())
        self.btn_add.clicked.connect(self.add_field)
        self.btn_del.clicked.connect(self.remove_field)
        self.btn_up.clicked.connect(lambda: self.move_field(-1))
        self.btn_down.clicked.connect(lambda: self.move_field(1))

    # -------------------------------------------------------------- rows
    def _append_row(self, fd: FieldDef) -> None:
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(fd.label)
        it.setData(Qt.UserRole, fd.key)
        it.setToolTip(f"Stored as “{fd.key}”")
        self.table.setItem(r, 0, it)
        kind = QComboBox()
        for k, text in TYPE_CHOICES:
            kind.addItem(text, k)
        kind.setCurrentIndex(max(0, kind.findData(fd.kind or "text")))
        kind.setToolTip("Type of input shown for this field")
        kind.currentIndexChanged.connect(lambda _i: self._kind_changed())
        self.table.setCellWidget(r, 1, kind)
        req = QCheckBox()
        req.setChecked(bool(fd.required))
        req.setToolTip("The wizard asks for this field before creating the folder")
        req.toggled.connect(lambda _on: self.changed.emit())
        host = QWidget()
        hl = QHBoxLayout(host)
        hl.setContentsMargins(SPACE.sm, 0, SPACE.sm, 0)
        hl.addWidget(req, 0, Qt.AlignCenter)
        self.table.setCellWidget(r, 2, host)
        ch = QTableWidgetItem(", ".join(fd.choices))
        ch.setToolTip("Choice list only: options separated by commas")
        self.table.setItem(r, 3, ch)
        self.table.blockSignals(False)
        self._kind_changed(emit=False)

    def _kind_changed(self, emit: bool = True) -> None:
        for r in range(self.table.rowCount()):
            k = self.table.cellWidget(r, 1).currentData()
            it = self.table.item(r, 3)
            if it is None:
                continue
            flags = it.flags()
            on = k == "choice"
            it.setFlags((flags | Qt.ItemIsEditable | Qt.ItemIsEnabled) if on
                        else (flags & ~Qt.ItemIsEditable & ~Qt.ItemIsEnabled))
        if emit:
            self.changed.emit()

    def _row_field(self, r: int) -> FieldDef:
        it = self.table.item(r, 0)
        kind = self.table.cellWidget(r, 1).currentData()
        req = self.table.cellWidget(r, 2).findChild(QCheckBox).isChecked()
        choices = [c.strip() for c in (self.table.item(r, 3).text() if self.table.item(r, 3)
                                       else "").split(",") if c.strip()]
        return FieldDef(key=str(it.data(Qt.UserRole) or ""), label=it.text().strip(),
                        kind=kind, choices=choices if kind == "choice" else [], required=req)

    def fields(self) -> List[FieldDef]:
        out: List[FieldDef] = []
        used = set()
        for r in range(self.table.rowCount()):
            fd = self._row_field(r)
            if not fd.label:
                continue
            key = fd.key or hui.snake(fd.label)
            base, n = key, 2
            while key in used or key == "id":
                key = f"{base}_{n}"
                n += 1
            used.add(key)
            fd.key = key
            out.append(fd)
        return out

    def add_field(self) -> None:
        self._append_row(FieldDef(key="", label="New field"))
        r = self.table.rowCount() - 1
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))
        self.changed.emit()

    def remove_field(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.changed.emit()

    def move_field(self, delta: int) -> None:
        r = self.table.currentRow()
        n = r + delta
        if r < 0 or n < 0 or n >= self.table.rowCount():
            return
        fields = [self._row_field(i) for i in range(self.table.rowCount())]
        fields[r], fields[n] = fields[n], fields[r]
        self._set_fields(fields)
        self.table.setCurrentCell(n, 0)
        self.changed.emit()

    def _set_fields(self, fields: List[FieldDef]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        for fd in fields:
            self._append_row(fd)

    # -------------------------------------------------------------- load / read
    def load(self, lv: LevelDef, fields: List[FieldDef]) -> None:
        self._key = lv.key
        self.name.setText(lv.label)
        self.id_name.setText(lv.id_label)
        self.folder.set_text(lv.folder_template)
        self._set_fields(fields)

    def level(self) -> LevelDef:
        return LevelDef(key=self._key, label=self.name.text().strip(),
                        id_label=self.id_name.text().strip(), fields=self.fields(),
                        folder_template=self.folder.text().strip() or "{id}")

    def problems(self) -> List[str]:
        out = []
        if not self.name.text().strip():
            out.append(f"Level {self.index + 1} needs a name")
        for p in self.folder.problems():
            out.append(f"{self.name.text() or 'Level'} folder name: {p}")
        return out


# ======================================================================
# The card
# ======================================================================

class NamingCard(Card):
    """Settings card editing the workspace's :class:`HierarchyProfile`."""

    rename_requested = Signal()

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__("Folder structure & naming",
                         "How jobs, parts and lots are named and nested in this workspace — "
                         "shared by everyone using it", parent=parent)
        self.state = state
        self.toasts = toasts
        self._loading = False
        self._saved: Optional[HierarchyProfile] = None

        top = QHBoxLayout()
        top.setSpacing(SPACE.sm)
        top.addWidget(label("Start from", tone="secondary"))
        self.preset = QComboBox()
        self.preset.addItem("Current settings", "")
        for key, text in PRESET_NAMES.items():
            self.preset.addItem(text, key)
        self.preset.setToolTip("Load a ready-made structure into the editor (nothing is saved "
                               "until you press Save)")
        top.addWidget(self.preset)
        top.addStretch(1)
        self.dirty = Badge("Unsaved changes", "warning", dot=True)
        self.dirty.hide()
        top.addWidget(self.dirty)
        self.body_layout().addLayout(top)

        srow = QHBoxLayout()
        srow.setSpacing(SPACE.sm)
        srow.addWidget(label("Images are stored", tone="secondary"))
        self.storage = SegmentedControl(["In the lot folder", "In timestamped runs"], 0)
        self.storage.setToolTip("In the lot folder: one continuous record per lot (re-analysis "
                                "keeps the previous results in results/_history).\n"
                                "In timestamped runs: every analysis session gets its own "
                                "dated sub-folder (v3.0 behaviour).")
        srow.addWidget(self.storage)
        srow.addStretch(1)
        self.body_layout().addLayout(srow)
        self.storage_note = label("", "caption")
        self.storage_note.setWordWrap(True)
        self.body_layout().addWidget(self.storage_note)

        lv_row = QHBoxLayout()
        lv_row.setSpacing(SPACE.xl)
        self.levels = [LevelEditor(i) for i in range(3)]
        for le in self.levels:
            lv_row.addWidget(le, 1)
        self.body_layout().addLayout(lv_row)

        self.body_layout().addWidget(label("Names", "overline", tone="secondary"))
        g = QGridLayout()
        g.setHorizontalSpacing(SPACE.md)
        g.setVerticalSpacing(SPACE.sm)
        g.setColumnStretch(1, 1)
        self.t_image = TemplateField("Name given to images copied into a lot; {original} keeps "
                                     "the microscope's file name")
        self.t_export = TemplateField("Default file name of Excel / PowerPoint exports")
        self.t_title = TemplateField("Default report title")
        self.t_session = TemplateField("Folder name of each timestamped run")
        self._rows = []
        for r, (text, w) in enumerate((("Image name", self.t_image),
                                       ("Export file name", self.t_export),
                                       ("Report title", self.t_title),
                                       ("Run folder name", self.t_session))):
            lab = label(text, tone="secondary")
            g.addWidget(lab, r, 0, Qt.AlignTop)
            g.addWidget(w, r, 1)
            self._rows.append((lab, w))
        self.body_layout().addLayout(g)

        self.problems_lbl = label("", "caption", tone="danger")
        self.problems_lbl.setWordWrap(True)
        self.problems_lbl.hide()
        self.body_layout().addWidget(self.problems_lbl)

        foot = QHBoxLayout()
        foot.setSpacing(SPACE.sm)
        self.btn_rename = AnimatedButton("Rename existing folders to match…", "edit", "ghost")
        self.btn_rename.setToolTip("Preview and apply the folder-name template to folders "
                                   "created before (undoable). New folders always use it.")
        self.btn_revert = AnimatedButton("Revert", "undo", "ghost")
        self.btn_revert.setToolTip("Discard the changes made here")
        self.btn_save = AnimatedButton("Save", "save", "primary")
        self.btn_save.setToolTip("Save for this workspace — every page updates immediately")
        foot.addWidget(self.btn_rename)
        foot.addStretch(1)
        foot.addWidget(self.btn_revert)
        foot.addWidget(self.btn_save)
        self.body_layout().addLayout(foot)

        self.preset.currentIndexChanged.connect(self._on_preset)
        self.storage.current_changed.connect(lambda _i: self._on_edit())
        for le in self.levels:
            le.changed.connect(self._on_edit)
        for t in (self.t_image, self.t_export, self.t_title, self.t_session):
            t.changed.connect(lambda _t: self._on_edit())
        self.btn_save.clicked.connect(self.save)
        self.btn_revert.clicked.connect(self.revert)
        self.btn_rename.clicked.connect(self.rename_requested)
        state.profile_changed.connect(self._on_external_change)
        state.node_changed.connect(lambda _n: self._refresh_previews())
        self.revert()

    # -------------------------------------------------------------- load
    def load(self, profile: HierarchyProfile) -> None:
        self._loading = True
        prof = copy.deepcopy(profile)
        for le, lv in zip(self.levels, prof.levels):
            le.load(lv, hui.level_fields(prof, lv.key) if hui.is_legacy_fields(prof)
                    else list(lv.fields))
        self.storage.set_current_index(0 if prof.images_location == "lot" else 1, animate=False)
        self.t_image.set_text(prof.image_name_template)
        self.t_export.set_text(prof.export_name_template)
        self.t_title.set_text(prof.report_title_template)
        self.t_session.set_text(prof.session_folder_template)
        self._loading = False
        self._on_edit()

    def revert(self) -> None:
        self._saved = copy.deepcopy(self.state.profile)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(0)
        self.preset.blockSignals(False)
        self.load(self._saved)

    def _on_external_change(self) -> None:
        if not self.is_dirty():
            self.revert()

    def _on_preset(self, _i: int) -> None:
        key = self.preset.currentData()
        self.load(PRESETS[key] if key else (self._saved or self.state.profile))

    # -------------------------------------------------------------- read
    def profile(self) -> HierarchyProfile:
        base = self._saved or self.state.profile
        key = self.preset.currentData()
        name = PRESETS[key].name if key else base.name
        levels = [le.level() for le in self.levels]
        # untouched v3.0 standard fields stay "legacy" (no fields stored)
        if all([f.to_dict() for f in lv.fields] ==
               [f.to_dict() for f in hui.LEGACY_FIELDS.get(lv.key, [])] for lv in levels):
            for lv in levels:
                lv.fields = []
        return HierarchyProfile(
            name=name or "Custom", levels=levels,
            images_location="lot" if self.storage.current_index() == 0 else "session",
            schema_version=base.schema_version,
            session_folder_template=self.t_session.text().strip() or base.session_folder_template,
            image_name_template=self.t_image.text().strip() or "{original}",
            export_name_template=self.t_export.text().strip() or base.export_name_template,
            report_title_template=self.t_title.text().strip() or base.report_title_template)

    def is_dirty(self) -> bool:
        return self._saved is not None and not _profiles_equal(self.profile(), self._saved)

    def problems(self) -> List[str]:
        out: List[str] = []
        for le in self.levels:
            out += le.problems()
        names = [le.name.text().strip().lower() for le in self.levels]
        if len(set(n for n in names if n)) < len([n for n in names if n]):
            out.append("Each level needs a different name")
        for lab, t in self._rows:
            if t is not self.t_session or self.storage.current_index() == 1:
                out += [f"{lab.text()}: {p}" for p in t.problems()]
        return out

    # -------------------------------------------------------------- live state
    def _on_edit(self) -> None:
        if self._loading:
            return
        self._refresh_previews()
        probs = self.problems()
        self.problems_lbl.setText("  ·  ".join(probs))
        self.problems_lbl.setVisible(bool(probs))
        dirty = self.is_dirty()
        self.dirty.setVisible(dirty)
        self.btn_save.setEnabled(dirty and not probs)
        self.btn_revert.setEnabled(dirty)

    def _refresh_previews(self) -> None:
        if self._loading:
            return
        prof = self.profile()
        lot = prof.images_location == "lot"
        node = self.state.current_node
        ctx = hui.context_for_path(node.path if node is not None and node.kind != "workspace"
                                   else None, prof)
        for lv in prof.levels:
            ctx[f"{lv.key}_label"] = lv.label
        keys = prof.available_keys()
        for le, lv in zip(self.levels, prof.levels):
            le.heading.setText(f"Level {le.index + 1} · {lv.label or '—'}".upper())
            fctx = {"id": ctx.get(lv.key, "")}
            for f in lv.fields:
                fctx[f.key] = ctx.get(f"{lv.key}_{f.key}", "") or f.label
            le.folder.set_tokens(hui.token_list(prof, f"folder:{lv.key}"))
            le.folder.set_context(fctx, hui.folder_keys(prof, lv.key), for_filename=True,
                                  suffix=" \\")
        gen = hui.token_list(prof)
        self.t_image.set_tokens(hui.token_list(prof, "image"))
        ictx = dict(ctx, index=1, original=ctx.get("original") or "SEM_0001")
        self.t_image.set_context(ictx, keys, for_filename=True, suffix=".tif")
        self.t_export.set_tokens(gen)
        self.t_export.set_context(ctx, keys, for_filename=True, suffix=".xlsx")
        self.t_title.set_tokens(gen)
        self.t_title.set_context(ctx, keys)
        self.t_session.set_tokens(gen)
        self.t_session.set_context(ctx, keys, for_filename=True, suffix=" \\")
        for lab, t in self._rows:
            if t is self.t_session:
                lab.setVisible(not lot)
                t.setVisible(not lot)
        L = [lv.label or "—" for lv in prof.levels]
        if lot:
            self.storage_note.setText(
                f"{L[0]} › {L[1]} › {L[2]}: images, results and reports are kept directly in "
                f"each {L[2]} folder. Existing timestamped runs stay where they are.")
        else:
            self.storage_note.setText(
                f"{L[0]} › {L[1]} › {L[2]} › Session: every analysis session is a dated "
                f"sub-folder of its {L[2]}.")

    # -------------------------------------------------------------- save
    def save(self) -> bool:
        if self.problems():
            return False
        prof = self.profile()
        try:
            self.state.set_profile(prof)
        except OSError as exc:
            if self.toasts is not None:
                self.toasts.show_toast("Could not save", str(exc).splitlines()[0], "danger")
            return False
        self._saved = copy.deepcopy(prof)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(0)
        self.preset.blockSignals(False)
        self._on_edit()
        if self.toasts is not None:
            self.toasts.show_toast("Folder structure saved",
                                   "Labels updated everywhere. New folders and exports use the "
                                   "new names; existing folders keep theirs.", "success",
                                   "Rename existing…", self.rename_requested.emit)
        return True


__all__ = ["NamingCard", "TemplateField", "LevelEditor"]
