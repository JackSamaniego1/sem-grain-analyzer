"""
About dialog (UI-08) — Help › About.

Layout (≈ 560 px wide, window-modal via ``open()``, non-blocking):
  header   app icon (64 px) · app name (display) · version badge · tagline
           optional organisation line (branding.ORGANIZATION_NAME, D-11)
  body     description · privacy card (shield icon + offline statement)
           · details list (version, developer, Python, Qt for Python, build)
           · "Third-party licences" collapsible → read-only text of the
             bundled THIRD_PARTY_LICENSES.txt (no web links anywhere)
  footer   Copy details (ghost) ………………………………… Close (primary, default)
States: licences file missing → the viewer says "please reinstall" (never a
download link).  Everything is local; the dialog does not touch the network.
"""
from __future__ import annotations

import platform
import sys

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout,
    QWidget,
)

from ui.design import branding, icons
from ui.design.theme import current_tokens
from ui.design.tokens import SPACE
from ui.pages.settings_page import PRIVACY, licences_text
from ui.widgets import AnimatedButton, Badge, Card, CollapsibleSection, KeyValueList, label
from version import APP_NAME, APP_PUBLISHER, __version__


def build_kind() -> str:
    return "Installed build" if getattr(sys, "frozen", False) else "Source checkout"


def about_details() -> list:
    """(key, value) rows shown in the dialog and copied by "Copy details"."""
    rows = [("Application", APP_NAME), ("Version", __version__)]
    if branding.ORGANIZATION_NAME:
        rows.append(("Organisation", branding.ORGANIZATION_NAME))
    rows += [("Developer", APP_PUBLISHER),
             ("Python", sys.version.split()[0]),
             ("Qt for Python", PYSIDE_VERSION),
             ("System", f"{platform.system()} {platform.version()}"),
             ("Build", build_kind())]
    return rows


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AboutDialog")
        self.setWindowTitle(f"About {APP_NAME}")
        self.setWindowIcon(branding.app_icon())
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE.xl, SPACE.xl, SPACE.xl, SPACE.lg)
        v.setSpacing(SPACE.lg)

        # header
        head = QHBoxLayout()
        head.setSpacing(SPACE.lg)
        self.logo = QLabel()
        self.logo.setFixedSize(64, 64)
        self.logo.setAccessibleName(f"{APP_NAME} icon")
        self._update_logo()
        head.addWidget(self.logo, 0, Qt.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(SPACE.xs)
        row = QHBoxLayout()
        row.setSpacing(SPACE.sm)
        self.title = label(APP_NAME, "display")
        row.addWidget(self.title)
        self.version_badge = Badge(f"v{__version__}", "accent")
        self.version_badge.setToolTip("Application version")
        row.addWidget(self.version_badge, 0, Qt.AlignVCenter)
        row.addStretch(1)
        titles.addLayout(row)
        tag = label(branding.APP_TAGLINE, tone="secondary")
        titles.addWidget(tag)
        self.org = label(branding.ORGANIZATION_NAME, "caption")
        self.org.setVisible(bool(branding.ORGANIZATION_NAME))
        titles.addWidget(self.org)
        head.addLayout(titles, 1)
        v.addLayout(head)

        # scrollable body — on short screens (1080p at 150–200 %) it scrolls
        # instead of squeezing the text
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body_scroll.setFocusPolicy(Qt.NoFocus)
        self._body = QWidget()
        bv = QVBoxLayout(self._body)
        bv.setContentsMargins(0, 0, SPACE.xs, 0)
        bv.setSpacing(SPACE.lg)
        self.body_scroll.setWidget(self._body)
        v.addWidget(self.body_scroll, 1)
        outer, v = v, bv

        desc = label(branding.APP_DESCRIPTION)
        desc.setWordWrap(True)
        v.addWidget(desc)

        # privacy
        priv = Card(elevation=0)
        priv.setObjectName("AboutPrivacy")
        pr = QHBoxLayout()
        pr.setSpacing(SPACE.md)
        shield = QLabel()
        shield.setPixmap(icons.icon("shield", current_tokens().success.fg).pixmap(24, 24))
        shield.setFixedSize(24, 24)
        pr.addWidget(shield, 0, Qt.AlignTop)
        pcol = QVBoxLayout()
        pcol.setSpacing(SPACE.xs)
        pcol.addWidget(label("Works fully offline", "h3"))
        self.privacy = label(PRIVACY, tone="secondary")
        self.privacy.setWordWrap(True)
        pcol.addWidget(self.privacy)
        pr.addLayout(pcol, 1)
        priv.body_layout().addLayout(pr)
        v.addWidget(priv)

        # details
        self.details = KeyValueList(about_details(), mono_keys=("Version", "Python", "Qt for Python"))
        v.addWidget(self.details)

        # licences
        self.lic_section = CollapsibleSection("Third-party licences", expanded=False)
        self.lic_section.setToolTip("Open-source components bundled with this application")
        self.licences = QPlainTextEdit(licences_text())
        self.licences.setReadOnly(True)
        self.licences.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.licences.setProperty("role", "mono")
        self.licences.setMinimumHeight(200)
        self.licences.setAccessibleName("Third-party licence texts")
        self.licences.setToolTip("Licence texts bundled with the application (read-only)")
        self.lic_section.add_widget(self.licences)
        v.addWidget(self.lic_section)
        v.addStretch(1)
        v = outer
        self.lic_section.toggled.connect(lambda _on: QTimer.singleShot(0, self._fit_to_screen))

        # footer
        foot = QHBoxLayout()
        self.btn_copy = AnimatedButton("Copy details", "copy", "ghost")
        self.btn_copy.setToolTip("Copy version and system details to the clipboard "
                                 "(useful when reporting a problem)")
        self.btn_close = AnimatedButton("Close", None, "primary")
        self.btn_close.setToolTip("Close this window (Esc)")
        self.btn_close.setDefault(True)
        foot.addWidget(self.btn_copy)
        foot.addStretch(1)
        foot.addWidget(self.btn_close)
        v.addLayout(foot)
        self.btn_copy.clicked.connect(self.copy_details)
        self.btn_close.clicked.connect(self.accept)
        self.btn_close.setFocus()

    def _update_logo(self) -> None:
        dpr = self.devicePixelRatioF() if self.windowHandle() else \
            (QApplication.instance().devicePixelRatio() if QApplication.instance() else 1.0)
        self.logo.setPixmap(branding.app_icon_pixmap(64, dpr))

    def _fit_to_screen(self) -> None:
        """Size to the content, capped at 90 % of the available screen height
        (the body scrolls beyond that)."""
        self._body.adjustSize()
        chrome = self.sizeHint().height() - self.body_scroll.sizeHint().height()
        want = chrome + self._body.sizeHint().height() + 2
        scr = self.screen() or QApplication.primaryScreen()
        cap = int(scr.availableGeometry().height() * 0.9) if scr else want
        self.resize(max(self.width(), 600), max(360, min(want, cap)))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._fit_to_screen()
        self._update_logo()  # re-render at the screen's real DPR (crisp at 150/200 %)

    def details_text(self) -> str:
        return "\n".join(f"{k}: {val}" for k, val in about_details())

    def copy_details(self) -> None:
        QApplication.clipboard().setText(self.details_text())
        self.btn_copy.setText("Copied")


__all__ = ["AboutDialog", "about_details", "build_kind"]
