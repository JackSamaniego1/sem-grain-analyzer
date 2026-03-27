import sys
import os

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    os.chdir(os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QPixmap, QFont, QPainter, QColor
from PyQt6.QtCore import Qt, QTimer
from ui.main_window import MainWindow
from ui.theme import apply_dark_theme


def _create_splash():
    """Create a simple splash screen with developer info."""
    pixmap = QPixmap(480, 260)
    pixmap.fill(QColor(26, 43, 74))
    p = QPainter(pixmap)
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Arial", 22, QFont.Weight.Bold))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
               "\n\nGrain Analyzer")
    p.setFont(QFont("Arial", 12))
    p.setPen(QColor(180, 180, 210))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
               "v2.3")
    p.setFont(QFont("Arial", 11))
    p.setPen(QColor(0, 200, 255))
    p.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
               "\nDeveloped by Jack Samaniego\n\n")
    p.end()
    return QSplashScreen(pixmap)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Grain Analyzer")
    app.setApplicationVersion("2.3")
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
