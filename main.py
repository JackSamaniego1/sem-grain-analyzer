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
from PySide6.QtGui import QPixmap, QFont, QPainter, QColor
from PySide6.QtCore import Qt, QTimer
from ui.main_window import MainWindow
from ui.theme import apply_dark_theme
from version import __version__, APP_NAME


def _create_splash():
    """Create a simple splash screen with developer info."""
    pixmap = QPixmap(480, 260)
    pixmap.fill(QColor(26, 43, 74))
    p = QPainter(pixmap)
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Arial", 22, QFont.Weight.Bold))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
               f"\n\n{APP_NAME}")
    p.setFont(QFont("Arial", 12))
    p.setPen(QColor(180, 180, 210))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               f"v{__version__}")
    p.setFont(QFont("Arial", 11))
    p.setPen(QColor(0, 200, 255))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
               "\n\n")
    p.end()
    return QSplashScreen(pixmap)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    apply_dark_theme(app)

    splash = _create_splash()
    splash.show()
    app.processEvents()

    window = MainWindow()

    # Show splash for 2 seconds then close
    QTimer.singleShot(2000, lambda: (splash.close(), window.show()))

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
