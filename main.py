import sys
import os

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    os.chdir(os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

# Offline guard MUST run before any heavy/networked import (PySide6, torch,
# cv2, etc.) so it can set env vars and monkeypatch socket before those
# libraries have a chance to open a connection. See HARD CONSTRAINT #1 in
# CLAUDE.md: this app must never touch the network.
from core import offline_guard
offline_guard.install()

from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtGui import QPixmap, QPainter, QColor, QLinearGradient, QPen
from PySide6.QtCore import Qt, QTimer, QRectF, QElapsedTimer
from version import __version__, APP_NAME, APP_PUBLISHER


def _create_splash():
    """Splash painted from the design tokens (dark instrument look)."""
    from ui.design.theme import ui_font
    from ui.design.tokens import DARK, TypeStyle
    from ui.design import icons

    t = DARK
    w, h = 560, 300
    pm = QPixmap(w, h)
    pm.fill(QColor(t.surface.bg))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0.0, QColor(t.surface.surface1))
    g.setColorAt(1.0, QColor(t.surface.bg))
    p.fillRect(pm.rect(), g)
    p.setPen(QPen(QColor(t.border.strong), 1))
    p.drawRect(QRectF(0.5, 0.5, w - 1, h - 1))
    # accent rule + icon
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(t.accent.base))
    p.drawRoundedRect(QRectF(40, 52, 4, 64), 2, 2)
    icons.icon("grains", t.accent.text).paint(p, w - 40 - 56, 52, 56, 56)
    p.setPen(QColor(t.text.primary))
    p.setFont(ui_font(TypeStyle(30, 600, 38, -0.5)))
    p.drawText(QRectF(60, 48, 400, 44), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)
    p.setPen(QColor(t.text.secondary))
    p.setFont(ui_font(TypeStyle(14, 400, 20)))
    p.drawText(QRectF(60, 92, 440, 24), Qt.AlignLeft | Qt.AlignVCenter,
               "SEM grain detection · measurement · reporting")
    p.setPen(QColor(t.text.tertiary))
    p.setFont(ui_font(TypeStyle(12, 400, 16)))
    p.drawText(QRectF(40, h - 64, 480, 18), Qt.AlignLeft | Qt.AlignVCenter,
               f"Version {__version__}  ·  {APP_PUBLISHER}")
    p.drawText(QRectF(40, h - 44, 480, 18), Qt.AlignLeft | Qt.AlignVCenter,
               "Works fully offline — your data stays on this computer")
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(t.surface.surface3))
    p.drawRoundedRect(QRectF(40, h - 20, w - 80, 3), 1.5, 1.5)
    p.setBrush(QColor(t.accent.base))
    p.drawRoundedRect(QRectF(40, h - 20, (w - 80) * 0.42, 3), 1.5, 1.5)
    p.end()
    return QSplashScreen(pm)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    from ui.design.theme import apply_theme
    from data.settings import load_settings
    theme = load_settings().theme
    apply_theme(app, theme if theme in ("dark", "light") else "dark")

    splash = _create_splash()
    splash.show()
    app.processEvents()
    clock = QElapsedTimer()
    clock.start()

    from ui.app_shell import AppShell
    window = AppShell()

    # keep the splash up for at least ~1.2 s so it reads as intentional
    delay = max(0, 1200 - clock.elapsed())
    QTimer.singleShot(delay, lambda: (splash.finish(window), window.show()))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
