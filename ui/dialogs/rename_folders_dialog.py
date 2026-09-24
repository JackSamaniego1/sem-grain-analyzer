"""
"Rename existing folders to match" (HIER-01) — never automatic.

Layout
  header   what will happen, in plain words
  list     one row per folder: level · current name → new name (tick to
           include); empty state "Every folder already matches"
  footer   n selected · Cancel · Rename n folders (primary)

The preview is computed off the GUI thread (``data.workspace.rename_to_template``);
applying uses ``apply_renames`` (collision-safe, catalog re-indexed) and the
caller gets the applied list so the toast can offer Undo (reverse plan).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ui import hierarchy_ui as hui
from ui.design.tokens import SPACE
from ui.widgets import AnimatedButton, EmptyState, FadeStackedWidget, label
from ui.workers import run_task

Plan = List[Tuple[Path, Path]]


def level_of(path: Path) -> str:
    for kind in ("project", "sample", "lot"):
        if (Path(path) / hui.META_FILES[kind]).exists():
            return kind
    return ""


def compute_plan(root: Path, profile) -> Plan:
    """Worker-thread: every project/sample/lot folder whose name differs
    from its level's folder-name template."""
    from data.workspace import Workspace, rename_to_template
    ws = Workspace(root)
    plan: Plan = []
    for proj in sorted(p for p in Path(root).iterdir() if (p / "project.json").exists()):
        plan += rename_to_template(ws, proj, profile)
    return plan


def apply_plan(root: Path, plan: Plan) -> Plan:
    """Worker-thread: apply (or undo, with a reversed plan)."""
    from data.catalog import Catalog
    from data.workspace import Workspace, apply_renames
    return apply_renames(Workspace(root), plan, catalog=Catalog(root))


def remap(path: Path, applied: Plan) -> Path:
    """Where ``path`` lives after ``applied`` renames (deepest first)."""
    p = Path(path)
    for old, new in sorted(applied, key=lambda x: len(Path(x[0]).parts)):
        try:
            rel = p.relative_to(old)
        except ValueError:
            continue
        p = Path(new) / rel
    return p


class RenameFoldersDialog(QDialog):
    renamed = Signal(object)          # applied plan [(old, new), ...]

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.plan: Plan = []
        self.setWindowTitle("Rename existing folders to match")
        self.setMinimumSize(760, 480)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        v.addWidget(label("Rename existing folders to match", "h2"))
        intro = label("These folders were named before the current folder-name settings. "
                      "Tick the ones to rename — their contents, results and reports move "
                      "with them, and you can undo it right after.", tone="secondary")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.stack = FadeStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Level", "Current name", "New name"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.setToolTip("Untick a folder to leave its name as it is")
        self.empty = EmptyState("check", "Every folder already matches",
                                "Nothing to rename — new folders use the current settings.")
        self.loading = EmptyState("refresh", "Checking folder names…")
        self.stack.addWidget(self.loading)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.empty)
        v.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        self.count_lbl = label("", "caption")
        foot.addWidget(self.count_lbl)
        foot.addStretch(1)
        self.btn_cancel = AnimatedButton("Cancel", None, "ghost")
        self.btn_apply = AnimatedButton("Rename", "edit", "primary")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip("Rename the ticked folders (Undo is offered afterwards)")
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_apply)
        v.addLayout(foot)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply.clicked.connect(self.apply)
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.stack.set_current_index(0)
        run_task(compute_plan, self.state.root, self.state.profile, on_done=self.set_plan,
                 on_error=lambda m: (self.set_plan([]),
                                     self.count_lbl.setText(m.splitlines()[0])))

    def set_plan(self, plan: Plan) -> None:
        self.plan = list(plan or [])
        prof = self.state.profile
        root = Path(self.state.root)
        self.tree.blockSignals(True)
        self.tree.clear()
        for old, new in self.plan:
            try:
                rel = str(Path(old).parent.relative_to(root))
            except ValueError:
                rel = str(Path(old).parent)
            where = "" if rel in ("", ".") else rel.replace("\\", " › ") + " › "
            it = QTreeWidgetItem([hui.kind_label(prof, level_of(old)), where + Path(old).name,
                                  Path(new).name])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
            it.setToolTip(1, str(old))
            it.setToolTip(2, str(new))
            it.setData(0, Qt.UserRole, (str(old), str(new)))
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        self.stack.set_current_index(1 if self.plan else 2)
        self._update_count()

    def selected_plan(self) -> Plan:
        out: Plan = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                o, n = it.data(0, Qt.UserRole)
                out.append((Path(o), Path(n)))
        return out

    def _update_count(self) -> None:
        n = len(self.selected_plan())
        self.count_lbl.setText(f"{n} of {len(self.plan)} folder{'s' if len(self.plan) != 1 else ''}"
                               " selected" if self.plan else "")
        self.btn_apply.setText(f"Rename {n} folder{'s' if n != 1 else ''}" if n else "Rename")
        self.btn_apply.setEnabled(n > 0)

    def apply(self) -> None:
        plan = self.selected_plan()
        if not plan:
            return
        self.btn_apply.set_loading(True)
        self.btn_cancel.setEnabled(False)
        run_task(apply_plan, self.state.root, plan, on_done=self._applied,
                 on_error=self._failed)

    def _applied(self, applied: Plan) -> None:
        self.btn_apply.set_loading(False)
        self.renamed.emit(list(applied or []))
        self.accept()

    def _failed(self, msg: str) -> None:
        self.btn_apply.set_loading(False)
        self.btn_cancel.setEnabled(True)
        self.count_lbl.setText("Could not rename: " + msg.splitlines()[0])


__all__ = ["RenameFoldersDialog", "compute_plan", "apply_plan", "remap", "level_of"]
