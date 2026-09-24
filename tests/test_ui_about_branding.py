"""
UI-08: About dialog, app icon / branding, DPI-friendly minimum window size.
Offscreen; nothing touches the network or the real settings folder.
"""
import struct

import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings  # noqa: E402
from data.settings import save_settings  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)


# ---------------------------------------------------------------- icon
def test_app_icon_has_native_sizes(qapp):
    from ui.design import branding
    ic = branding.app_icon()
    assert not ic.isNull()
    sizes = {s.width() for s in ic.availableSizes()}
    assert {16, 32, 48, 256} <= sizes


def test_app_icon_is_drawn_not_blank(qapp):
    from ui.design.branding import app_icon_image
    for n in (16, 32, 256):
        img = app_icon_image(n)
        assert img.width() == n
        # corner is transparent (rounded tile), centre is the opaque accent grain
        assert img.pixelColor(0, 0).alpha() == 0 or n < 24
        c = img.pixelColor(n // 2, n // 2)
        assert c.alpha() == 255 and c.blue() > c.red()


def test_hidpi_pixmap_is_rendered_at_device_pixels(qapp):
    from ui.design.branding import app_icon_pixmap
    pm = app_icon_pixmap(64, 2.0)
    assert pm.width() == 128 and pm.devicePixelRatio() == 2.0


def test_write_ico_is_valid(qapp, tmp_path):
    from ui.design.branding import write_ico
    p = write_ico(tmp_path / "out" / "icon.ico", sizes=(16, 32, 256))
    data = p.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, 3)
    w0, h0 = data[6], data[7]
    assert (w0, h0) == (16, 16)
    last = struct.unpack("<BBBBHHII", data[6 + 32:6 + 48])
    assert last[0] == 0                        # 256 px is stored as 0
    size, offset = last[6], last[7]
    assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
    assert offset + size == len(data)


def test_org_name_is_single_pending_constant():
    from ui.design import branding
    assert branding.ORGANIZATION_NAME == ""    # D-11 pending — no company branding yet


# ---------------------------------------------------------------- About dialog
def test_about_dialog_contents(env, qtbot):
    from ui.dialogs.about_dialog import AboutDialog
    from version import APP_NAME, __version__
    d = AboutDialog()
    qtbot.addWidget(d)
    d.show()
    assert APP_NAME in d.windowTitle()
    assert d.title.text() == APP_NAME
    assert __version__ in d.version_badge.text()
    assert d.details.value("Version") == __version__
    assert "offline" in d.privacy.text().lower()
    assert "no update checks" in d.privacy.text().lower()
    lic = d.licences.toPlainText()
    assert "PySide6" in lic and "LGPL" in lic
    assert d.licences.isReadOnly()
    assert not d.lic_section.is_expanded()
    assert not d.org.isVisible()
    # no hyperlinks anywhere in the dialog (offline; nothing opens a browser)
    from PySide6.QtWidgets import QLabel
    for lab in d.findChildren(QLabel):
        assert not lab.openExternalLinks()
        assert "<a " not in lab.text().lower()


def test_about_copy_details(env, qtbot, qapp):
    from ui.dialogs.about_dialog import AboutDialog
    from version import __version__
    d = AboutDialog()
    qtbot.addWidget(d)
    d.btn_copy.click()
    assert f"Version: {__version__}" in qapp.clipboard().text()


def test_about_shows_org_when_set(env, qtbot, monkeypatch):
    from ui.design import branding
    from ui.dialogs.about_dialog import AboutDialog
    monkeypatch.setattr(branding, "ORGANIZATION_NAME", "Acme Materials Lab")
    d = AboutDialog()
    qtbot.addWidget(d)
    d.show()
    assert d.org.isVisible() and d.org.text() == "Acme Materials Lab"
    assert d.details.value("Organisation") == "Acme Materials Lab"


def test_missing_licence_file_says_reinstall(env, qtbot, monkeypatch):
    import ui.dialogs.about_dialog as mod
    monkeypatch.setattr(mod, "licences_text",
                        lambda: "THIRD_PARTY_LICENSES.txt is missing from this installation "
                                "— please reinstall.")
    d = mod.AboutDialog()
    qtbot.addWidget(d)
    t = d.licences.toPlainText()
    assert "reinstall" in t and "http" not in t


# ---------------------------------------------------------------- shell wiring
@pytest.fixture
def shell(env, qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    w = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.close()


def test_shell_help_about_opens_dialog(shell, qtbot, qapp):
    assert not shell.windowIcon().isNull()
    assert not qapp.windowIcon().isNull()
    assert "About" in shell.act_about.text()
    shell.act_about.trigger()
    qtbot.waitUntil(lambda: getattr(shell, "about_dialog", None) is not None
                    and shell.about_dialog.isVisible())
    assert shell.current_page() != "settings"      # no longer just jumps to Settings
    shell.about_dialog.btn_close.click()
    qtbot.waitUntil(lambda: not shell.about_dialog.isVisible())


def test_shell_minimum_size_fits_1080p_at_150_percent(shell):
    # 1920x1080 at 150 % = 1280x720 logical, minus the taskbar
    m = shell.minimumSize()
    assert m.width() <= 1280 and m.height() <= 680


def test_shortcut_sheet_lists_grain_edit_keys():
    from ui.app_shell import SHORTCUTS
    rows = {k: d for group in SHORTCUTS.values() for k, d in group}
    assert "Lasso" in rows["L"] and "Merge" in rows["M"]
    assert "Cut" in rows["C"] and "select tool" in rows["V / Esc"]
