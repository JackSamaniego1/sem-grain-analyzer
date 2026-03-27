import sys
import os

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    os.chdir(os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.theme import apply_dark_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SEM Grain Analyzer")
    app.setApplicationVersion("1.0.0")
    app.setStyle("Fusion")
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
