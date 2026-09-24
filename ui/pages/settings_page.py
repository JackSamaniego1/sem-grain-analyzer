"""Settings page: workspace, operator, appearance, detection defaults, about,
privacy statement and third-party licences (read from the bundled file)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QSpinBox, QVBoxLayout, QWidget,
)

from data.catalog import Catalog
from ui.app_state import params_from_dict, params_to_dict
from ui.design.theme import current_mode, reduced_motion, set_reduced_motion
from ui.design.tokens import SPACE
from ui.pages.common import PageHeader, scroll
from ui.pages.naming_settings import NamingCard
from ui.widgets import AnimatedButton, Card, KeyValueList, SegmentedControl, label
from ui.workers import run_task
from version import APP_NAME, APP_PUBLISHER, __version__

PRIVACY = ("This application works fully offline; no data leaves this computer. "
           "Images, results and reports are stored only in the workspace folder you choose. "
           "There are no update checks, no telemetry and no cloud services.")

MODE_NAMES = {"auto": "Automatic", "boundary": "Boundary", "threshold": "Threshold",
              "sam_astm": "AI-assisted (SAM)"}


def licences_text() -> str:
    here = Path(__file__).resolve().parents[2]
    for base in (Path(getattr(sys, "_MEIPASS", here)), here):
        p = base / "THIRD_PARTY_LICENSES.txt"
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return "THIRD_PARTY_LICENSES.txt is missing from this installation — please reinstall."


class SettingsPage(QWidget):
    theme_requested = Signal(str)
    workspace_changed = Signal()
    defaults_from_analyze_requested = Signal()

    def __init__(self, state, toasts=None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.toasts = toasts
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.xl)
        v.setSpacing(SPACE.lg)
        v.addWidget(PageHeader("Settings", "Settings",
                               "Stored on this computer only"))
        grid = QGridLayout()
        grid.setSpacing(SPACE.lg)

        # workspace
        ws = Card("Workspace folder", "Where projects, samples, lots and sessions are stored")
        self.ws_path = QLineEdit(str(state.root))
        self.ws_path.setReadOnly(True)
        self.ws_path.setToolTip("Current workspace folder")
        ws.add_widget(self.ws_path)
        r = QHBoxLayout()
        b = AnimatedButton("Change folder…", "open", "secondary")
        b.setToolTip("Use a different folder (existing data stays where it is)")
        b.clicked.connect(self.choose_workspace)
        self.rebuild_btn = AnimatedButton("Rebuild search index", "refresh", "ghost")
        self.rebuild_btn.setToolTip("Rescan every session so search finds everything")
        self.rebuild_btn.clicked.connect(self.rebuild_index)
        r.addWidget(b)
        r.addWidget(self.rebuild_btn)
        r.addStretch(1)
        ws.body_layout().addLayout(r)
        ws.body_layout().addStretch(1)
        grid.addWidget(ws, 0, 0)

        # operator + appearance
        ap = Card("Operator & appearance")
        ap.add_widget(label("Operator name (recorded with every session)", tone="secondary"))
        self.operator = QLineEdit(state.settings.operator)
        self.operator.setPlaceholderText(state.operator())
        self.operator.setToolTip("Shown in the top bar and stored in new sessions")
        self.operator.editingFinished.connect(self._save_operator)
        ap.add_widget(self.operator)
        ap.add_widget(label("Theme", tone="secondary"))
        self.theme = SegmentedControl(["Dark", "Light"], 0 if current_mode() == "dark" else 1)
        self.theme.setToolTip("Dark suits dim microscope rooms; light suits offices and printouts")
        self.theme.current_text_changed.connect(lambda m: self.theme_requested.emit(m.lower()))
        ap.add_widget(self.theme)
        self.motion = QCheckBox("Reduce motion (animations complete instantly)")
        self.motion.setChecked(reduced_motion())
        self.motion.setToolTip("Turn off transitions and animated counters")
        self.motion.toggled.connect(self._set_motion)
        ap.add_widget(self.motion)
        ap.body_layout().addStretch(1)
        grid.addWidget(ap, 0, 1)

        # detection defaults
        dd = Card("Default detection parameters", "Used for new sessions")
        self.defaults_kv = KeyValueList()
        dd.add_widget(self.defaults_kv)
        r2 = QHBoxLayout()
        b1 = AnimatedButton("Use current Analyze settings", "check", "secondary")
        b1.setToolTip("Make the parameters currently set on the Analyze page the default")
        b1.clicked.connect(self.defaults_from_analyze_requested)
        b2 = AnimatedButton("Factory defaults", "refresh", "ghost")
        b2.setToolTip("Automatic mode with the built-in parameters")
        b2.clicked.connect(self.reset_defaults)
        r2.addWidget(b1)
        r2.addWidget(b2)
        r2.addStretch(1)
        dd.body_layout().addLayout(r2)
        dd.body_layout().addStretch(1)
        grid.addWidget(dd, 1, 0)

        # about + privacy
        ab = Card("About")
        ab.add_widget(KeyValueList([("Application", APP_NAME), ("Version", __version__),
                                    ("Developer", APP_PUBLISHER),
                                    ("Python", sys.version.split()[0]),
                                    ("Qt for Python", PYSIDE_VERSION)]))
        ab.add_widget(label("Privacy", "h3"))
        p = label(PRIVACY, tone="secondary")
        p.setWordWrap(True)
        ab.add_widget(p)
        ab.body_layout().addStretch(1)
        grid.addWidget(ab, 1, 1)

        # INN-27 lot statistics
        ls = Card("Lot statistics", "ASTM E112 §15 — how many fields a lot result needs")
        lg = QGridLayout()
        lg.setHorizontalSpacing(SPACE.md)
        self.req_fields = QSpinBox()
        self.req_fields.setRange(2, 100)
        self.req_fields.setValue(int(getattr(state.settings, "required_fields", 5) or 5))
        self.req_fields.setSuffix(" fields")
        self.req_fields.setAccessibleName("Required fields")
        self.req_fields.setToolTip("Minimum number of analysed images (fields) per lot "
                                   "(ASTM E112: at least 5)")
        self.target_ra = QDoubleSpinBox()
        self.target_ra.setRange(1.0, 50.0)
        self.target_ra.setSingleStep(0.5)
        self.target_ra.setDecimals(1)
        self.target_ra.setPrefix("≤ ")
        self.target_ra.setSuffix(" %")
        self.target_ra.setValue(float(getattr(state.settings, "target_RA_pct", 10.0) or 10.0))
        self.target_ra.setAccessibleName("Target relative accuracy")
        self.target_ra.setToolTip("Target relative accuracy %RA of the lot mean "
                                  "(ASTM E112: 10 % or less is generally acceptable)")
        lg.addWidget(label("Required fields", tone="secondary"), 0, 0)
        lg.addWidget(self.req_fields, 0, 1)
        lg.addWidget(label("Target %RA", tone="secondary"), 1, 0)
        lg.addWidget(self.target_ra, 1, 1)
        lg.setColumnStretch(2, 1)
        ls.body_layout().addLayout(lg)
        self.req_fields.valueChanged.connect(self._save_lot_stats)
        self.target_ra.valueChanged.connect(self._save_lot_stats)
        rs = AnimatedButton("Defaults (5 fields, 10 %)", "refresh", "ghost")
        rs.setToolTip("Restore the ASTM E112 defaults")
        rs.clicked.connect(lambda: (self.req_fields.setValue(5), self.target_ra.setValue(10.0)))
        ls.add_widget(rs)
        ls.body_layout().addStretch(1)
        grid.addWidget(ls, 2, 0)

        # INN-29 calibration verification (optional, default off)
        cv = Card("Calibration verification", "Optional — verify the image scale against a "
                  "certified reference standard")
        self.cal_toggle = QCheckBox("Record calibration checks")
        self.cal_toggle.setChecked(bool(getattr(state.settings,
                                                "calibration_verification_enabled", False)))
        self.cal_toggle.setToolTip("When on, reports state whether the scale was verified. "
                                   "Off: nothing about calibration checks is shown.")
        self.cal_toggle.toggled.connect(self._set_cal_enabled)
        cv.add_widget(self.cal_toggle)
        self.cal_box = QWidget()
        cb = QVBoxLayout(self.cal_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(SPACE.sm)
        from ui.dialogs.cal_check_dialog import CalStatusChip
        self.cal_chips = QVBoxLayout()
        self.cal_chips.setSpacing(SPACE.xs)
        self._chips = []
        cb.addLayout(self.cal_chips)
        self.cal_hint = label("Image your reference grating at the magnification you use, then "
                              "run a check. Reports cite the latest passing check.", "caption")
        self.cal_hint.setWordWrap(True)
        cb.addWidget(self.cal_hint)
        self.cal_btn = AnimatedButton("Check calibration…", "calibrate", "secondary")
        self.cal_btn.setToolTip("Measure a reference-standard image and record the check")
        self.cal_btn.clicked.connect(self.open_cal_check)
        cb.addWidget(self.cal_btn, 0, Qt.AlignLeft)
        self._CalStatusChip = CalStatusChip
        cv.add_widget(self.cal_box)
        cv.body_layout().addStretch(1)
        grid.addWidget(cv, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)

        # HIER-01: folder structure & naming (full width)
        self.naming = NamingCard(state, toasts)
        self.naming.rename_requested.connect(self.open_rename_dialog)
        v.addWidget(self.naming)

        lic = Card("Third-party licences", "Open-source components bundled with this application")
        self.lic = QPlainTextEdit(licences_text())
        self.lic.setReadOnly(True)
        self.lic.setMinimumHeight(260)
        self.lic.setToolTip("Licence texts (read-only)")
        lic.add_widget(self.lic)
        v.addWidget(lic)
        v.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))
        self._refresh_defaults()
        self._refresh_cal()
        state.settings_changed.connect(self._refresh)

    def _refresh(self) -> None:
        self.ws_path.setText(str(self.state.root))
        self.operator.setPlaceholderText(self.state.operator())
        st = self.state.settings
        for w, v in ((self.req_fields, int(st.required_fields or 5)),
                     (self.target_ra, float(st.target_RA_pct or 10.0))):
            if w.value() != v:
                w.blockSignals(True)
                w.setValue(v)
                w.blockSignals(False)
        if self.cal_toggle.isChecked() != bool(st.calibration_verification_enabled):
            self.cal_toggle.blockSignals(True)
            self.cal_toggle.setChecked(bool(st.calibration_verification_enabled))
            self.cal_toggle.blockSignals(False)
        self._refresh_cal()

    # ------------------------------------------------------------------ INN-27 / INN-29
    def _save_lot_stats(self, *_a) -> None:
        s = self.state.settings
        n, ra = int(self.req_fields.value()), float(self.target_ra.value())
        if (s.required_fields, s.target_RA_pct) != (n, ra):
            s.required_fields, s.target_RA_pct = n, ra
            self.state.save_settings()

    def _set_cal_enabled(self, on: bool) -> None:
        self.state.settings.calibration_verification_enabled = bool(on)
        self.state.save_settings()          # -> _refresh -> _refresh_cal

    def _refresh_cal(self) -> None:
        """Calibration box only when the feature is on; one chip per
        instrument that has at least one recorded check."""
        on = bool(getattr(self.state.settings, "calibration_verification_enabled", False))
        self.cal_box.setVisible(on)
        for c in self._chips:
            c.setParent(None)
            c.deleteLater()
        self._chips = []
        if not on:
            return
        from ui.dialogs.cal_check_dialog import cal_store, known_instruments
        store = cal_store(self.state)
        try:
            names = known_instruments(self.state, store)
        except (OSError, ValueError):
            names = []
        for n in names:
            chip = self._CalStatusChip()
            chip.refresh(store, n, getattr(self.state.settings, "instruments", None))
            if chip.state != "off":
                self.cal_chips.addWidget(chip, 0, Qt.AlignLeft)
                self._chips.append(chip)
            else:
                chip.deleteLater()

    def open_cal_check(self):
        from ui.dialogs.cal_check_dialog import CalCheckDialog
        inst = ""
        s = self.state.session
        if s is not None:
            inst = str(getattr(getattr(s, "meta", None), "instrument", "") or "")
        dlg = CalCheckDialog(self.state, self.toasts, self, instrument=inst)
        dlg.saved.connect(lambda _c: self._refresh_cal())
        self._cal_dlg = dlg
        dlg.open()
        return dlg

    def _refresh_defaults(self) -> None:
        p = self.state.default_params()
        self.defaults_kv.set_items([
            ("Detection mode", MODE_NAMES.get(p.detection_mode, p.detection_mode)),
            ("Min grain area", f"{p.min_grain_size_px} px²"),
            ("Black level (excluded)", f"≤ {getattr(p, 'invalid_intensity_threshold', 12)}"),
            ("Blur σ / edge sensitivity", f"{p.blur_sigma:g} / {p.edge_sensitivity:g}"),
        ])

    def set_defaults(self, params) -> None:
        self.state.ui_state["default_params"] = params_to_dict(params)
        self.state.persist_ui_state()
        self._refresh_defaults()
        self._toast("Defaults saved", "New sessions start with these parameters.", "success")

    def reset_defaults(self) -> None:
        self.state.ui_state.pop("default_params", None)
        self.state.persist_ui_state()
        self._refresh_defaults()
        self._toast("Factory defaults restored", "", "success")

    def sync_theme(self, mode: str) -> None:
        self.theme.blockSignals(True)
        self.theme.set_current_index(0 if mode == "dark" else 1, animate=False)
        self.theme.blockSignals(False)

    def _set_motion(self, on: bool) -> None:
        set_reduced_motion(on)
        self.state.ui_state["reduced_motion"] = bool(on)
        self.state.persist_ui_state()

    def _save_operator(self) -> None:
        name = self.operator.text().strip()
        if name != self.state.settings.operator:
            self.state.settings.operator = name
            self.state.save_settings()

    def choose_workspace(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the workspace folder", str(self.state.root))
        if d:
            self.state.set_workspace_root(d)
            self.workspace_changed.emit()
            self._toast("Workspace changed", d, "success")
            self.rebuild_index()

    def rebuild_index(self) -> None:
        self.rebuild_btn.set_loading(True)
        root = self.state.root

        def done(n):
            self.rebuild_btn.set_loading(False)
            self._toast("Search index rebuilt", f"{n} session{'s' if n != 1 else ''} indexed.",
                        "success")

        def failed(msg):
            self.rebuild_btn.set_loading(False)
            self._toast("Could not rebuild the index", msg.splitlines()[0], "danger")

        run_task(lambda: Catalog(root).rebuild(), on_done=done, on_error=failed)

    # ------------------------------------------------------------------ HIER-01 renames
    def open_rename_dialog(self) -> None:
        from ui.dialogs.rename_folders_dialog import RenameFoldersDialog
        if self.naming.is_dirty():
            self._toast("Save the folder settings first",
                        "Renaming uses the saved folder-name settings.", "warning")
            return
        dlg = RenameFoldersDialog(self.state, self)
        dlg.renamed.connect(lambda applied: self._after_rename(applied, undo=False))
        self._rename_dlg = dlg
        dlg.open()

    def _after_rename(self, applied, undo: bool) -> None:
        """Keep an open session pointing at its (moved) folder, refresh the
        tree, and offer Undo (the reverse plan)."""
        from ui.dialogs.rename_folders_dialog import apply_plan, remap
        st = self.state
        if st.session is not None and applied:
            new = remap(st.session.path, applied)
            if new != st.session.path:
                st.close_session()
                st.open_session(new)
        st.workspace_changed.emit()
        n = len(applied)
        if undo:
            self._toast("Rename undone", f"{n} folder{'s' if n != 1 else ''} restored.", "info")
            return
        if not n:
            return
        reverse = [(new, old) for old, new in applied]

        def do_undo():
            if st.session is not None:
                st.flush()
            run_task(apply_plan, st.root, reverse,
                     on_done=lambda back: self._after_rename(back, undo=True),
                     on_error=lambda m: self._toast("Could not undo", m.splitlines()[0],
                                                    "danger"))
        if self.toasts is not None:
            self.toasts.show_toast("Folders renamed",
                                   f"{n} folder{'s' if n != 1 else ''} now match the naming "
                                   "settings.", "success", "Undo", do_undo, timeout_ms=10000)

    def _toast(self, title, body="", sev="info") -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev)
