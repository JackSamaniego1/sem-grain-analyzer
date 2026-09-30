"""UPDATE 4 item 5 (UI): "Resolution Profiles" in the Analyze right sidebar.

Layout (sidebar section, below the Run card):
  header   "Resolution Profiles"  ··········  [on/off switch]   (OFF by default, per PC)
  OFF      one caption: scan area & scale are set per image as usual.  Nothing else
           changes anywhere (the tile under the image shows as today).
  ON       [profile dropdown: name · ratio · scan area]            [⋮ Import / Export]
           [Apply to this image | Apply to N ticked]  [New profile…] [Rename] [Delete]
           muted hint: store warnings / read-only reason / empty-state help
  Under the image (ON and the image has a profile): "Profile: <name>" summary card
  with ratio + scan area + "Show details" / "Remove"; it replaces the
  "Scan area & scale" tile.  No profile -> that tile as today.
States: empty (no profiles -> "Create one with New profile…"), busy (Auto-find
running -> New profile button spins), error (ProfileError -> toast, message as-is),
populated.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLineEdit, QMenu, QMessageBox, QSizePolicy,
    QVBoxLayout, QWidget,
)

from data.resolution_profiles import (
    ProfileError, ProfileStore, ResolutionProfile, nm_per_px_from,
)
from ui.calibration_dialog import LENGTH_UNITS, set_length_value, split_length_um
from ui.design.tokens import SPACE
from ui.pages.filter_card import Switch
from ui.resolution_profile_ops import apply_profile, profile_of, remove_profile
from ui.widgets import AnimatedButton, Badge, Card, CollapsibleSection, IconButton, label

__all__ = ["ResolutionProfilesCard", "ProfileSummaryTile", "NewProfileDialog",
           "install_resolution_profiles", "UI_STATE_KEY"]

UI_STATE_KEY = "resolution_profiles_on"


def _rect_text(rect, w: int = 0, h: int = 0) -> str:
    if rect:
        return f"{int(rect[2])} × {int(rect[3])} px"
    return f"{w} × {h} px (full image)" if w and h else "full image"


def profile_line(p: ResolutionProfile) -> str:
    """Dropdown text: name · ratio · scan-area size."""
    return f"{p.name}  ·  {p.display_ratio()}  ·  {_rect_text(p.scan_rect, p.image_w, p.image_h)}"


def _snapshot_ratio(snap: dict) -> str:
    try:
        return ResolutionProfile.from_dict(snap).display_ratio()
    except Exception:  # noqa: BLE001 - a damaged snapshot still shows its name
        return ""


# ---------------------------------------------------------------- new profile window
class NewProfileDialog(QDialog):
    """Name + scale-bar length (number + unit) + scale-bar pixels, pre-filled
    from Auto-find.  The scan area comes from the image as it is now."""

    def __init__(self, bar_px: float, length_um: float, scan_rect, image_size,
                 read_value: float = 0.0, read_unit: str = "", suggested_name: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New resolution profile")
        self.setObjectName("newProfileDialog")
        self.scan_rect = tuple(scan_rect) if scan_rect else None
        self.image_size = tuple(image_size)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.lg, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.md)
        v.addWidget(label("New resolution profile", "h3"))
        intro = label("Auto-find has looked for the scan area and the scale bar on this image. "
                      "Check the scale-bar length, give the profile a name and save it.",
                      "caption")
        intro.setWordWrap(True)
        v.addWidget(intro)
        form = QFormLayout()
        form.setSpacing(SPACE.sm)
        self.name = QLineEdit(suggested_name)
        self.name.setPlaceholderText("e.g. Zeiss 5000x 1024")
        self.name.setToolTip("A name you will recognise, e.g. instrument and magnification")
        self.name.setAccessibleName("Profile name")
        form.addRow("Name", self.name)
        lr = QHBoxLayout()
        lr.setSpacing(SPACE.sm)
        self.length = QDoubleSpinBox()
        self.length.setObjectName("profileBarLength")
        self.length.setRange(0.0, 100000.0)
        self.length.setDecimals(3)
        self.length.setSpecialValueText("length?")
        self.length.setToolTip("The number printed next to the scale bar")
        self.length.setAccessibleName("Scale-bar length")
        self.unit = QComboBox()
        self.unit.addItems(list(LENGTH_UNITS))
        self.unit.setCurrentText("µm")
        self.unit.setToolTip("Unit printed on the scale-bar label")
        self.unit.setAccessibleName("Scale-bar length unit")
        lr.addWidget(self.length, 1)
        lr.addWidget(self.unit)
        form.addRow("Scale-bar length", lr)
        self.pixels = QDoubleSpinBox()
        self.pixels.setRange(0.0, 100000.0)
        self.pixels.setDecimals(1)
        self.pixels.setSuffix(" px")
        self.pixels.setSpecialValueText("not found")
        self.pixels.setToolTip("Length of the scale bar in pixels (found by Auto-find; "
                               "correct it if needed)")
        self.pixels.setAccessibleName("Scale-bar pixels")
        self.pixels.setValue(float(bar_px or 0.0))
        form.addRow("Scale-bar pixels", self.pixels)
        w, h = self.image_size
        form.addRow("Scan area", label(_rect_text(self.scan_rect, w, h), "body"))
        form.addRow("Image size", label(f"{w} × {h} px", "body"))
        v.addLayout(form)
        if read_value > 0 and read_unit in LENGTH_UNITS:
            self.unit.setCurrentText(read_unit)
            set_length_value(self.length, read_value)
        elif length_um > 0:
            val, unit = split_length_um(length_um)
            self.unit.setCurrentText(unit)
            set_length_value(self.length, val)
        self.error = label("", "caption", tone="danger")
        self.error.setWordWrap(True)
        self.error.hide()
        v.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._check)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.resize(460, self.sizeHint().height())

    def values(self):
        return (self.name.text().strip(), float(self.length.value()), self.unit.currentText(),
                float(self.pixels.value()))

    def show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.setVisible(bool(msg))

    def _check(self) -> None:
        name, ln, unit, px = self.values()
        if not name:
            self.show_error("Please type a name for the profile.")
            return
        try:
            nm_per_px_from(ln, unit, px)
        except ProfileError as e:
            self.show_error(str(e))
            return
        self.accept()


# ---------------------------------------------------------------- sidebar section
class ResolutionProfilesCard(CollapsibleSection):
    enabled_changed = Signal(bool)
    applied = Signal(list)                   # uids the profile was applied to

    def __init__(self, state, store: Optional[ProfileStore] = None, toasts=None,
                 checked_uids=None, parent=None) -> None:
        super().__init__("Resolution Profiles", expanded=True, parent=parent)
        self.setObjectName("resolutionProfilesCard")
        self.state = state
        self.toasts = toasts
        self._store = store
        self._checked = checked_uids or (lambda: [])
        self._new_uid = None                  # New profile… waiting for Auto-find
        self.dialog: Optional[NewProfileDialog] = None
        self.switch = Switch()
        self.switch.setToolTip("Use saved resolution profiles (scale + scan area) for images "
                               "taken with the same microscope settings")
        self.switch.setAccessibleName("Use resolution profiles")
        self.header_trailing().addWidget(self.switch)

        self.off_hint = label("Off — scan area and scale are set per image as usual.",
                              "caption", tone="secondary")
        self.off_hint.setWordWrap(True)
        self.add_widget(self.off_hint)

        self.body = QWidget()
        b = QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(SPACE.sm)
        row = QHBoxLayout()
        row.setSpacing(SPACE.xs)
        self.combo = QComboBox()
        self.combo.setObjectName("profileCombo")
        self.combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.combo.setToolTip("Saved profiles: name · pixel-to-length ratio · scan-area size")
        self.combo.setAccessibleName("Resolution profile")
        row.addWidget(self.combo, 1)
        self.btn_more = IconButton("more","Import or export profiles")
        self.menu = QMenu(self.btn_more)
        self.act_import = self.menu.addAction("Import profiles…", self._ask_import)
        self.act_export = self.menu.addAction("Export profiles…", self._ask_export)
        self.btn_more.clicked.connect(
            lambda: self.menu.popup(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft())))
        row.addWidget(self.btn_more)
        b.addLayout(row)
        self.btn_apply = AnimatedButton("Apply to this image", "check", "primary", "sm")
        self.btn_apply.setToolTip("Use this profile's scale and scan area. The image goes back "
                                  "to Not analysed, ready to analyse.")
        self.btn_apply.clicked.connect(self.apply_selected)
        b.addWidget(self.btn_apply)
        br = QHBoxLayout()
        br.setSpacing(SPACE.xs)
        self.btn_new = AnimatedButton("New profile…", "add", "secondary", "sm")
        self.btn_new.setToolTip("Auto-find the scan area and scale bar on this image, check the "
                                "scale-bar length and save it as a profile")
        self.btn_new.clicked.connect(self.start_new_profile)
        self.btn_rename = IconButton("edit", "Rename the selected profile")
        self.btn_rename.setAccessibleName("Rename profile")
        self.btn_rename.clicked.connect(self._ask_rename)
        self.btn_delete = IconButton("delete", "Delete the selected profile (images it was "
                                               "applied to keep their values)")
        self.btn_delete.setAccessibleName("Delete profile")
        self.btn_delete.clicked.connect(self._ask_delete)
        br.addWidget(self.btn_new, 1)
        br.addWidget(self.btn_rename)
        br.addWidget(self.btn_delete)
        b.addLayout(br)
        self.hint = label("", "caption", tone="secondary")
        self.hint.setObjectName("profileHint")
        self.hint.setWordWrap(True)
        b.addWidget(self.hint)
        self.add_widget(self.body)

        self.switch.setChecked(bool(state.ui_state.get(UI_STATE_KEY, False)))
        self.switch.toggled.connect(self.set_enabled)
        state.current_image_changed.connect(lambda _u: self._sync_buttons())
        state.images_changed.connect(self._sync_buttons)
        state.setup_finished.connect(self._on_setup_finished)
        self._apply_enabled_ui()

    # -- store ---------------------------------------------------------------
    @property
    def store(self) -> ProfileStore:
        if self._store is None:
            self._store = ProfileStore()
        return self._store

    def is_on(self) -> bool:
        return self.switch.isChecked()

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if self.switch.isChecked() != on:
            self.switch.setChecked(on)          # re-enters through toggled
            return
        self.state.ui_state[UI_STATE_KEY] = on
        self.state.persist_ui_state()
        self._apply_enabled_ui()
        self.enabled_changed.emit(on)

    def _apply_enabled_ui(self) -> None:
        on = self.is_on()
        self.off_hint.setVisible(not on)
        self.body.setVisible(on)
        if on:
            self.reload()

    # -- list ----------------------------------------------------------------
    def reload(self, select_id: Optional[str] = None) -> None:
        keep = select_id or self.selected_id()
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in self.store.list():
            self.combo.addItem(profile_line(p), p.id)
            self.combo.setItemData(self.combo.count() - 1,
                                   f"{p.name}\n{p.display_ratio()}\nMade on "
                                   f"{p.image_w} × {p.image_h} px images", Qt.ToolTipRole)
        if self.combo.count() == 0:
            self.combo.addItem("No profiles yet", None)
        i = self.combo.findData(keep) if keep else -1
        self.combo.setCurrentIndex(max(i, 0))
        self.combo.blockSignals(False)
        self._sync_buttons()

    def selected_id(self) -> Optional[str]:
        return self.combo.currentData() if self.combo.count() else None

    def selected(self) -> Optional[ResolutionProfile]:
        pid = self.selected_id()
        return self.store.get(pid) if pid else None

    def _sync_buttons(self) -> None:
        has = self.selected() is not None
        writable = not self.store.read_only
        im = self.state.current_image()
        targets = self.target_uids()
        self.btn_apply.setText(f"Apply to {len(targets)} ticked images" if len(targets) > 1
                               else "Apply to this image")
        self.btn_apply.setEnabled(has and bool(targets))
        self.btn_new.setEnabled(writable and im is not None and im.readable and not im.loading)
        self.btn_rename.setEnabled(has and writable)
        self.btn_delete.setEnabled(has and writable)
        self.act_export.setEnabled(bool(self.store.list()))
        self.act_import.setEnabled(writable)
        notes = list(self.store.warnings)
        if self.store.read_only:
            notes.append(self.store.read_only_reason or "Profiles are read-only on this PC.")
        if not self.store.list():
            notes.append("No profiles yet. Open an image and choose New profile… to save its "
                         "scale and scan area.")
        self.hint.setText("\n".join(n for n in notes if n))
        self.hint.setVisible(bool(self.hint.text()))

    def target_uids(self) -> List:
        """Ticked images when 2 or more are ticked, else the current image."""
        ticked = list(self._checked() or [])
        if len(ticked) > 1:
            return ticked
        return [self.state.current_uid] if self.state.current_uid is not None else []

    # -- apply ---------------------------------------------------------------
    def apply_selected(self) -> List:
        p = self.selected()
        if p is None:
            return []
        done, notes = apply_profile(self.state, p, self.target_uids())
        for n in notes[:3]:
            self._toast("Profile not applied", n, "warning")
        if len(notes) > 3:
            self._toast("Profile not applied", f"{len(notes) - 3} more images do not match.",
                        "warning")
        if done:
            k = len(done)
            doc = self.state.session
            stale = [u for u in done if doc is not None
                     and self.state.stale_reason(doc.image(u))]
            body = f"'{p.name}' on {k} image{'s' if k != 1 else ''} — ready to analyse."
            if stale:
                body += (f" {len(stale)} already analysed image"
                         f"{'s need' if len(stale) != 1 else ' needs'} re-analysis; "
                         "the old result is left out of reports until then.")
            self._toast("Profile applied", body, "success")
            self.applied.emit(done)
        return done

    # -- new -----------------------------------------------------------------
    def start_new_profile(self) -> bool:
        """Auto-find on the current image, then the New profile window."""
        im = self.state.current_image()
        if im is None or self.store.read_only:
            return False
        self._new_uid = im.uid
        if self.state.auto_setup([im.uid]) > 0:
            self.btn_new.set_loading(True)
            return True
        # nothing to find (busy / already running): use what is there now
        QTimer.singleShot(0, lambda: self.open_new_dialog(self._new_uid))
        return True

    def _on_setup_finished(self, _st: dict) -> None:
        if self._new_uid is None:
            return
        self.btn_new.set_loading(False)
        self.open_new_dialog(self._new_uid)

    def open_new_dialog(self, uid) -> Optional[NewProfileDialog]:
        self._new_uid = None
        doc = self.state.session
        im = doc.image(uid) if doc is not None and uid is not None else None
        if im is None or not im.shape:
            return None
        h, w = im.shape[:2]
        rect = self.state.scan_for(im) or (0, 0, int(w), int(h))
        rd = im.bar_read or {}
        px = float(im.bar_px or 0.0)
        length_um = float(im.bar_um or 0.0)
        if px <= 0 and self.state.px_for(im) > 0:
            # no scale bar found but the image has a scale (metadata): 10 µm of it
            length_um, px = 10.0, 10.0 * self.state.px_for(im)
        mag = getattr(getattr(im, "image_info", None), "magnification", "") or ""
        dlg = NewProfileDialog(px, length_um, rect, (w, h),
                               float(rd.get("value") or 0.0), str(rd.get("unit") or ""),
                               suggested_name=str(mag) if mag else "", parent=self.window())
        dlg.accepted.connect(lambda: self._save_from_dialog(dlg, uid))
        self.dialog = dlg
        dlg.open()
        return dlg

    def _save_from_dialog(self, dlg: NewProfileDialog, uid) -> None:
        name, ln, unit, px = dlg.values()
        self.create_profile(name, ln, unit, px, uid)

    def create_profile(self, name: str, length: float, unit: str, pixels: float,
                       uid=None) -> Optional[ResolutionProfile]:
        """Save a profile from the image ``uid`` (default: current)."""
        doc = self.state.session
        uid = self.state.current_uid if uid is None else uid
        im = doc.image(uid) if doc is not None and uid is not None else None
        if im is None or not im.shape:
            return None
        h, w = im.shape[:2]
        rect = self.state.scan_for(im) or (0, 0, int(w), int(h))
        info = getattr(im, "image_info", None)
        try:
            p = self.store.add(name, nm_per_px_from(length, unit, pixels),
                               unit=unit, scan_rect=tuple(int(v) for v in rect),
                               image_size=(int(w), int(h)),
                               instrument=str(getattr(info, "instrument", "") or ""),
                               magnification=str(getattr(info, "magnification", "") or ""))
        except ProfileError as e:
            self._toast("Profile not saved", str(e), "danger")
            return None
        self.reload(p.id)
        self._toast("Profile saved", f"'{p.name}' ({p.display_ratio()}). Choose Apply to use "
                    "it.", "success")
        return p

    # -- rename / delete ------------------------------------------------------
    def _ask_rename(self) -> None:
        p = self.selected()
        if p is None:
            return
        name, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=p.name)
        if ok and name.strip():
            self.rename_selected(name)

    def rename_selected(self, name: str) -> bool:
        p = self.selected()
        if p is None:
            return False
        try:
            self.store.rename(p.id, name)
        except ProfileError as e:
            self._toast("Profile not renamed", str(e), "danger")
            return False
        self.reload(p.id)
        return True

    def _ask_delete(self) -> None:
        p = self.selected()
        if p is None:
            return
        if QMessageBox.question(
                self, "Delete profile",
                f"Delete the profile '{p.name}'?\n\nImages it was applied to keep their scale "
                "and scan area.") == QMessageBox.Yes:
            self.delete_selected()

    def delete_selected(self) -> bool:
        p = self.selected()
        if p is None:
            return False
        try:
            ok = self.store.delete(p.id)
        except ProfileError as e:
            self._toast("Profile not deleted", str(e), "danger")
            return False
        self.reload()
        return ok

    # -- import / export ------------------------------------------------------
    def _ask_import(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, "Import resolution profiles", "",
                                               "Profiles (*.json)")
        if path:
            self.import_from(Path(path))

    def _ask_export(self) -> None:
        path, _f = QFileDialog.getSaveFileName(self, "Export resolution profiles",
                                               "resolution_profiles.json", "Profiles (*.json)")
        if path:
            self.export_to(Path(path))

    def import_from(self, path: Path):
        try:
            rep = self.store.import_from(Path(path))
        except ProfileError as e:
            self._toast("Import failed", str(e), "danger")
            return None
        self.reload()
        parts = [f"{rep.added} added"]
        if rep.renamed:
            parts.append(f"{rep.renamed} renamed (name already used)")
        if rep.skipped_existing:
            parts.append(f"{rep.skipped_existing} already here")
        if rep.skipped_damaged:
            parts.append(f"{rep.skipped_damaged} damaged, skipped")
        self._toast("Profiles imported", ", ".join(parts) + ".", "success")
        return rep

    def export_to(self, path: Path) -> int:
        try:
            n = self.store.export_to(Path(path))
        except ProfileError as e:
            self._toast("Export failed", str(e), "danger")
            return 0
        self._toast("Profiles exported", f"{n} profile{'s' if n != 1 else ''} saved to "
                    f"{Path(path).name}.", "success")
        return n

    def _toast(self, title: str, body: str, sev: str) -> None:
        if self.toasts is not None:
            self.toasts.show_toast(title, body, sev)


# ---------------------------------------------------------------- under the image
class ProfileSummaryTile(Card):
    """Replaces the "Scan area & scale" tile while the image has a profile
    and the Resolution Profiles switch is on."""

    show_details_requested = Signal()
    remove_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self.setObjectName("profileSummaryTile")
        self.layout().setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        row = QHBoxLayout()
        row.setSpacing(SPACE.sm)
        self.title = label("", "h3")
        self.title.setObjectName("profileSummaryName")
        row.addWidget(self.title)
        self.badge = Badge("Ready", "success", dot=True)
        self.badge.setToolTip("Scale and scan area come from this profile")
        row.addWidget(self.badge)
        self.detail = label("", "caption", tone="secondary")
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.detail, 1)
        self.btn_details = AnimatedButton("Scan area & scale", "scan_area", "ghost", "sm")
        self.btn_details.setToolTip("Show the usual scan area & scale box for this image")
        self.btn_details.clicked.connect(self.show_details_requested)
        row.addWidget(self.btn_details)
        self.btn_remove = AnimatedButton("Remove", "close", "ghost", "sm")
        self.btn_remove.setToolTip("Stop using the profile on this image (its scale and scan "
                                   "area stay; Ctrl+Z undoes)")
        self.btn_remove.clicked.connect(self.remove_requested)
        row.addWidget(self.btn_remove)
        self.body_layout().addLayout(row)

    def show_profile(self, snap: dict, state=None, im=None) -> None:
        self.title.setText(f"Profile: {snap.get('name', '')}")
        parts = [_snapshot_ratio(snap)]
        rect = snap.get("scan_rect")
        parts.append("scan area " + _rect_text(rect, int(snap.get("image_w") or 0),
                                               int(snap.get("image_h") or 0)))
        self.detail.setText("  ·  ".join(p for p in parts if p))
        changed = False
        if state is not None and im is not None:
            try:
                p = ResolutionProfile.from_dict(snap)
                px = state.px_for(im)
                changed = abs(px - p.px_per_um) > 1e-6 * max(px, 1e-9)
                if p.scan_rect and state.scan_for(im) and \
                        tuple(state.scan_for(im)) != tuple(p.scan_rect):
                    changed = True
            except Exception:  # noqa: BLE001
                changed = False
        self.badge.set_text("Changed by hand" if changed else "In use")
        self.badge.set_kind("warning" if changed else "success")


def install_resolution_profiles(page, store: Optional[ProfileStore] = None):
    """Hook the card and the summary tile into an AnalyzePage (additive)."""
    state = page.state
    card = ResolutionProfilesCard(state, store, page.toasts,
                                  checked_uids=lambda: page.film.checked_uids(), parent=page)
    host = page.params.parentWidget().layout()
    host.insertWidget(host.indexOf(page.params), card)
    tile = ProfileSummaryTile()
    ip = page.setup_tile.parentWidget().layout()
    ip.insertWidget(ip.indexOf(page.setup_tile) + 1, tile)
    tile.hide()
    page.profiles_card = card
    page.profile_tile = tile
    forced = {"uid": None}                    # "Scan area & scale" pressed for this image

    def refresh(*_a) -> None:
        im = state.current_image()
        snap = profile_of(im) if im is not None else None
        use = card.is_on() and snap is not None and forced["uid"] != im.uid
        if use:
            tile.show_profile(snap, state, im)
        tile.setVisible(use)
        page.setup_tile.setVisible(not use)

    def details() -> None:
        forced["uid"] = state.current_uid
        refresh()

    def on_current(_u) -> None:
        forced["uid"] = None
        refresh()

    tile.show_details_requested.connect(details)
    tile.remove_requested.connect(
        lambda: remove_profile(state, [state.current_uid]) if state.current_uid is not None
        else None)
    card.enabled_changed.connect(refresh)
    state.current_image_changed.connect(on_current)
    state.calibration_changed.connect(refresh)
    state.image_updated.connect(refresh)
    state.session_opened.connect(refresh)
    page.refresh_profile_tile = refresh
    refresh()
    return card
