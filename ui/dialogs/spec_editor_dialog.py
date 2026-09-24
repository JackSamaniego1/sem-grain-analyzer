"""
Spec limits editor (INN-02) - OPTIONAL feature, reached from a project's (or
sample's) context menu: "Spec limits (optional)…".

Layout spec
  regions : header (title + one-line purpose) · left: list of the project's
            specs + Add / Remove · right: editor (name, revision, decision
            rule, applies-to, rules table with Add / Remove rule, statement,
            inline problems) · footer Cancel / Save.
  primary : Save (writes ``project.json`` ``specs``; nothing else changes).
  states  : empty (no specs: explanation, "Add a spec" - lots show no
            verdict, exactly as before) · populated · invalid (inline
            problems, Save refuses with a message; nothing is written).
  A sample-level spec (``applies_to.sample_ids``) overrides the project-wide
  one for those samples (``data.specs.select_spec``).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLineEdit,
    QListWidget, QListWidgetItem, QStackedWidget, QTableWidget, QVBoxLayout, QWidget,
)

from data.models import read_json, write_json_atomic
from data.specs import (
    DECISION_RULES, Rule, Spec, known_metrics, specs_from_project_dict, specs_to_project_dict,
)
from ui.design.tokens import SPACE
from ui.widgets import AnimatedButton, EmptyState, SegmentedControl, label

METRIC_TEXT = {
    "G_mean": "ASTM grain size G (mean)",
    "ecd_mean_um": "Mean ECD",
    "RA_pct": "Relative accuracy %RA",
    "n_fields": "Number of fields",
    "invalid_area_pct_max": "Max invalid area per field",
    "ala_G": "ALA grain size G",
}
METRIC_UNIT = {"G_mean": "", "ecd_mean_um": "µm", "RA_pct": "%", "n_fields": "",
               "invalid_area_pct_max": "%", "ala_G": ""}
RULE_TEXT = {"guarded": "Guarded (95 % CI)", "simple": "Simple (mean)"}
STATEMENT = {
    "guarded": "PASS only when the whole 95 % confidence interval lies within the limits; "
               "an interval straddling a limit is INCONCLUSIVE (ILAC-G8).",
    "simple": "PASS when the mean lies within the limits (ILAC-G8 simple acceptance).",
}


def sample_ids_of(project_dir: Path) -> List[str]:
    """Sample IDs under a project folder (same rule as the catalog)."""
    out = []
    try:
        dirs = sorted(p for p in Path(project_dir).iterdir() if p.is_dir())
    except OSError:
        return out
    for d in dirs:
        mp = d / "sample.json"
        if not mp.exists():
            continue
        try:
            sid = str(read_json(mp).get("sample_id") or d.name)
        except (OSError, ValueError):
            sid = d.name
        out.append(sid)
    return out


def load_specs(project_dir: Path) -> List[Spec]:
    p = Path(project_dir) / "project.json"
    try:
        return specs_from_project_dict(read_json(p)) if p.exists() else []
    except (OSError, ValueError):
        return []


def save_specs(project_dir: Path, specs: Sequence[Spec]) -> None:
    """Replace ``specs`` in project.json, keeping every other key."""
    p = Path(project_dir) / "project.json"
    d = read_json(p) if p.exists() else {}
    write_json_atomic(p, specs_to_project_dict(d, specs))


def _fmt(v: Optional[float]) -> str:
    return "" if v is None else f"{v:g}"


def _parse(text: str) -> Optional[float]:
    t = text.strip().replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


class SpecEditorDialog(QDialog):
    """Edit the spec limits of one project. ``saved(list[Spec])`` after Save."""

    saved = Signal(list)

    def __init__(self, project_dir: Path, project_name: str = "",
                 focus_sample: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.project_dir = Path(project_dir)
        self.focus_sample = focus_sample
        self.specs: List[Spec] = load_specs(self.project_dir)
        self.sample_ids = sample_ids_of(self.project_dir)
        self._cur = -1
        self._filling = False
        name = project_name or self.project_dir.name
        self.setWindowTitle(f"Spec limits — {name}")
        self.setMinimumSize(880, 560)

        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        v.addWidget(label("Spec limits (optional)", "h2"))
        hint = label("Lots of this project are checked against these limits and show PASS / "
                     "FAIL / INCONCLUSIVE on the Lot result and in reports. Without a spec "
                     "nothing is shown.", tone="secondary")
        hint.setWordWrap(True)
        v.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(SPACE.lg)
        # ---- left: spec list
        left = QVBoxLayout()
        left.setSpacing(SPACE.sm)
        left.addWidget(label("SPECIFICATIONS", "overline"))
        self.list = QListWidget()
        self.list.setAccessibleName("Specifications")
        self.list.setToolTip("Specs of this project. A spec for selected samples overrides "
                             "the project-wide spec for those samples.")
        self.list.setFixedWidth(250)
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        lr = QHBoxLayout()
        self.btn_add = AnimatedButton("Add spec", "add", "secondary")
        self.btn_add.setToolTip("Add a specification")
        self.btn_add.clicked.connect(self.add_spec)
        self.btn_remove = AnimatedButton("Remove", "delete", "ghost")
        self.btn_remove.setToolTip("Remove the selected specification")
        self.btn_remove.clicked.connect(self.remove_spec)
        lr.addWidget(self.btn_add)
        lr.addWidget(self.btn_remove)
        lr.addStretch(1)
        left.addLayout(lr)
        body.addLayout(left)

        # ---- right: editor / empty state
        self.right = QStackedWidget()
        self.empty = EmptyState("tune", "No spec limits",
                                "This project has no specification, so lots show no verdict. "
                                "Add one to judge every lot against your limits.",
                                "Add a spec", "add")
        self.empty.action_triggered.connect(self.add_spec)
        self.right.addWidget(self.empty)
        self.right.addWidget(self._build_editor())
        body.addWidget(self.right, 1)
        v.addLayout(body, 1)

        self.error = label("", tone="danger")
        self.error.setWordWrap(True)
        self.error.hide()
        v.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = AnimatedButton("Cancel", None, "ghost")
        self.btn_cancel.setToolTip("Close without saving (Esc)")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save = AnimatedButton("Save", "check", "primary")
        self.btn_save.setToolTip("Save the spec limits to the project")
        self.btn_save.clicked.connect(self.save)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_save)
        v.addLayout(row)

        self._reload_list()
        start = 0
        if focus_sample:
            for i, s in enumerate(self.specs):
                if focus_sample in ((s.applies_to or {}).get("sample_ids") or []):
                    start = i
        self._set_current(start if self.specs else -1)

    # ------------------------------------------------------------------ editor
    def _build_editor(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(SPACE.md)
        g.setVerticalSpacing(SPACE.sm)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Alloy 718 bar")
        self.name.setAccessibleName("Spec name")
        self.name.setToolTip("Name printed in reports next to the verdict")
        self.revision = QLineEdit()
        self.revision.setPlaceholderText("e.g. Rev B")
        self.revision.setAccessibleName("Spec revision")
        self.revision.setToolTip("Revision of the specification document")
        g.addWidget(label("Name", tone="secondary"), 0, 0)
        g.addWidget(self.name, 0, 1)
        g.addWidget(label("Revision", tone="secondary"), 0, 2)
        g.addWidget(self.revision, 0, 3)

        self.rule = SegmentedControl([RULE_TEXT[r] for r in ("guarded", "simple")], 0)
        self.rule.setToolTip("How measurement uncertainty is treated (ISO/IEC 17025 §7.8.6)")
        self.statement = label("", "caption")
        self.statement.setWordWrap(True)
        g.addWidget(label("Decision rule", tone="secondary"), 1, 0)
        g.addWidget(self.rule, 1, 1, 1, 3, Qt.AlignLeft)
        g.addWidget(self.statement, 2, 1, 1, 3)

        self.scope = SegmentedControl(["All samples", "Selected samples"], 0)
        self.scope.setToolTip("A spec for selected samples overrides the project-wide spec "
                              "for those samples")
        g.addWidget(label("Applies to", tone="secondary"), 3, 0)
        g.addWidget(self.scope, 3, 1, 1, 3, Qt.AlignLeft)
        self.samples = QListWidget()
        self.samples.setAccessibleName("Samples this spec applies to")
        self.samples.setToolTip("Tick the samples this spec applies to")
        self.samples.setMaximumHeight(96)
        for sid in self.sample_ids:
            it = QListWidgetItem(sid)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.samples.addItem(it)
        g.addWidget(self.samples, 4, 1, 1, 3)

        g.addWidget(label("RULES", "overline"), 5, 0, 1, 4)
        self.rules = QTableWidget(0, 4)
        self.rules.setHorizontalHeaderLabels(["Metric", "Lower limit", "Upper limit", "Unit"])
        self.rules.setAccessibleName("Rules")
        self.rules.setToolTip("Leave a limit empty for a one-sided rule")
        self.rules.verticalHeader().hide()
        self.rules.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rules.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.rules.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, wdt in ((1, 120), (2, 120), (3, 84)):
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            self.rules.setColumnWidth(c, wdt)
        self.rules.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rules.verticalHeader().setDefaultSectionSize(36)
        g.addWidget(self.rules, 6, 0, 1, 4)
        rr = QHBoxLayout()
        self.btn_add_rule = AnimatedButton("Add rule", "add", "secondary")
        self.btn_add_rule.setToolTip("Add a limit on another metric")
        self.btn_add_rule.clicked.connect(lambda: self._add_rule_row(Rule(metric="G_mean")))
        self.btn_del_rule = AnimatedButton("Remove rule", "delete", "ghost")
        self.btn_del_rule.setToolTip("Remove the selected rule")
        self.btn_del_rule.clicked.connect(self._remove_rule_row)
        rr.addWidget(self.btn_add_rule)
        rr.addWidget(self.btn_del_rule)
        rr.addStretch(1)
        g.addLayout(rr, 7, 0, 1, 4)
        self.problems = label("", "caption", "warning")
        self.problems.setWordWrap(True)
        g.addWidget(self.problems, 8, 0, 1, 4)
        g.setRowStretch(6, 1)
        g.setColumnStretch(1, 2)
        g.setColumnStretch(3, 1)

        self.name.textEdited.connect(self._changed)
        self.revision.textEdited.connect(self._changed)
        self.rule.current_text_changed.connect(lambda _t: self._changed())
        self.scope.current_text_changed.connect(lambda _t: self._changed())
        self.samples.itemChanged.connect(lambda _i: self._changed())
        return w

    def _add_rule_row(self, rule: Rule) -> None:
        r = self.rules.rowCount()
        self.rules.insertRow(r)
        combo = QComboBox()
        combo.setAccessibleName("Metric")
        for m in known_metrics():
            combo.addItem(METRIC_TEXT.get(m, m), m)
        i = combo.findData(rule.metric)
        combo.setCurrentIndex(max(i, 0))
        self.rules.setCellWidget(r, 0, combo)
        val = QDoubleValidator(self)
        val.setNotation(QDoubleValidator.StandardNotation)
        edits = []
        for c, v in ((1, rule.lower), (2, rule.upper)):
            e = QLineEdit(_fmt(v))
            e.setValidator(val)
            e.setPlaceholderText("none")
            e.setAccessibleName("Lower limit" if c == 1 else "Upper limit")
            e.setToolTip("Leave empty for no limit on this side")
            e.textEdited.connect(self._changed)
            self.rules.setCellWidget(r, c, e)
            edits.append(e)
        unit = QLineEdit(rule.unit or METRIC_UNIT.get(rule.metric, ""))
        unit.setAccessibleName("Unit")
        unit.setToolTip("Unit shown in reports")
        unit.textEdited.connect(self._changed)
        self.rules.setCellWidget(r, 3, unit)

        def on_metric(_i, combo=combo, unit=unit):
            unit.setText(METRIC_UNIT.get(combo.currentData(), ""))
            self._changed()
        combo.currentIndexChanged.connect(on_metric)
        if not self._filling:
            self._changed()

    def _remove_rule_row(self) -> None:
        r = self.rules.currentRow()
        if r < 0:
            r = self.rules.rowCount() - 1
        if r >= 0:
            self.rules.removeRow(r)
            self._changed()

    def _rules_from_table(self) -> List[Rule]:
        out = []
        for r in range(self.rules.rowCount()):
            combo = self.rules.cellWidget(r, 0)
            out.append(Rule(metric=combo.currentData(),
                            lower=_parse(self.rules.cellWidget(r, 1).text()),
                            upper=_parse(self.rules.cellWidget(r, 2).text()),
                            unit=self.rules.cellWidget(r, 3).text().strip()))
        return out

    # ------------------------------------------------------------------ list
    def _spec_title(self, s: Spec) -> str:
        t = s.name or "Untitled spec"
        if s.revision:
            t += f"  ·  {s.revision}"
        ids = (s.applies_to or {}).get("sample_ids") or []
        t += "\n" + (("Samples: " + ", ".join(ids)) if ids else "All samples")
        return t

    def _reload_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.specs:
            self.list.addItem(self._spec_title(s))
        self.list.blockSignals(False)
        self.btn_remove.setEnabled(bool(self.specs))

    def _set_current(self, i: int) -> None:
        self._cur = i
        if i < 0:
            self.right.setCurrentIndex(0)
            return
        self.right.setCurrentIndex(1)
        self.list.blockSignals(True)
        self.list.setCurrentRow(i)
        self.list.blockSignals(False)
        self._fill(self.specs[i])

    def _select(self, i: int) -> None:
        self._commit()
        self._set_current(i)

    def _fill(self, s: Spec) -> None:
        self._filling = True
        self.name.setText(s.name)
        self.revision.setText(s.revision)
        self.rule.set_current_index(1 if s.decision_rule == "simple" else 0, animate=False)
        ids = (s.applies_to or {}).get("sample_ids") or []
        self.scope.set_current_index(1 if ids else 0, animate=False)
        for k in range(self.samples.count()):
            it = self.samples.item(k)
            it.setCheckState(Qt.Checked if it.text() in ids else Qt.Unchecked)
        self.rules.setRowCount(0)
        for r in s.rules:
            self._add_rule_row(r)
        self._filling = False
        self._update_derived(s)

    def _commit(self) -> None:
        """Form -> ``self.specs[self._cur]``."""
        if self._cur < 0 or self._cur >= len(self.specs):
            return
        s = self.specs[self._cur]
        s.name = self.name.text().strip()
        s.revision = self.revision.text().strip()
        s.decision_rule = "simple" if self.rule.current_index() == 1 else "guarded"
        if self.scope.current_index() == 1:
            ids = [self.samples.item(k).text() for k in range(self.samples.count())
                   if self.samples.item(k).checkState() == Qt.Checked]
            s.applies_to = {"sample_ids": ids}
        else:
            s.applies_to = {}
        s.rules = self._rules_from_table()

    def _changed(self, *_a) -> None:
        if self._filling:
            return
        self._commit()
        if 0 <= self._cur < len(self.specs):
            s = self.specs[self._cur]
            item = self.list.item(self._cur)
            if item is not None:
                item.setText(self._spec_title(s))
            self._update_derived(s)
        self.error.hide()

    def _update_derived(self, s: Spec) -> None:
        self.statement.setText(STATEMENT.get(s.decision_rule, ""))
        self.samples.setVisible(self.scope.current_index() == 1)
        probs = self.problems_for(s)
        self.problems.setText(" · ".join(probs))
        self.problems.setVisible(bool(probs))

    def problems_for(self, s: Spec) -> List[str]:
        out = list(s.problems())
        if (s.applies_to or {}).get("sample_ids") == []:
            out.append("Tick at least one sample, or apply the spec to all samples.")
        if s.decision_rule not in DECISION_RULES:
            out.append("Unknown decision rule.")
        for r in s.rules:
            if r.lower is not None and r.upper is not None and r.lower > r.upper:
                out.append(f"{METRIC_TEXT.get(r.metric, r.metric)}: lower limit is above "
                           "the upper limit.")
        return out

    # ------------------------------------------------------------------ actions
    def add_spec(self) -> None:
        self._commit()
        s = Spec(id="spec-" + uuid.uuid4().hex[:8], name="",
                 rules=[Rule(metric="G_mean"), Rule(metric="n_fields", lower=5.0)])
        if self.focus_sample:
            s.applies_to = {"sample_ids": [self.focus_sample]}
        self.specs.append(s)
        self._reload_list()
        self._set_current(len(self.specs) - 1)
        self.name.setFocus()

    def remove_spec(self) -> None:
        if not (0 <= self._cur < len(self.specs)):
            return
        del self.specs[self._cur]
        self._cur = -1
        self._reload_list()
        self._set_current(min(self.list.count() - 1, max(0, self.list.currentRow()))
                          if self.specs else -1)

    def save(self) -> bool:
        self._commit()
        for i, s in enumerate(self.specs):
            probs = self.problems_for(s)
            if probs:
                self._set_current(i)
                self.error.setText(f"“{s.name or 'Untitled spec'}” is not complete: {probs[0]}")
                self.error.show()
                return False
        try:
            save_specs(self.project_dir, self.specs)
        except (OSError, ValueError) as e:
            self.error.setText(f"Could not save the project file: {e}")
            self.error.show()
            return False
        self.saved.emit(list(self.specs))
        self.accept()
        return True


__all__ = ["SpecEditorDialog", "load_specs", "save_specs", "sample_ids_of"]
