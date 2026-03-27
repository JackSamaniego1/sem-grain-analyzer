"""Dark theme for SEM Grain Analyzer."""
from PyQt6.QtGui import QPalette, QColor

def apply_dark_theme(app):
    palette = QPalette()
    dark_bg = QColor(30, 30, 35)
    mid_bg  = QColor(45, 45, 52)
    light_bg= QColor(58, 58, 68)
    text    = QColor(220, 220, 230)
    accent  = QColor(0, 140, 200)
    palette.setColor(QPalette.ColorRole.Window, dark_bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, mid_bg)
    palette.setColor(QPalette.ColorRole.AlternateBase, light_bg)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, mid_bg)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255,255,255))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(110,110,130))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(110,110,130))
    app.setPalette(palette)
    app.setStyleSheet("""
        QMainWindow, QWidget { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
        QPushButton {
            background-color: #2d2d34; color: #dcdce6;
            border: 1px solid #46465555; border-radius: 6px; padding: 7px 16px; font-weight: 500;
        }
        QPushButton:hover { background-color: #3a3a45; border-color: #008cc8; }
        QPushButton:pressed { background-color: #008cc8; color: white; }
        QPushButton:disabled { background-color: #232328; color: #6e6e82; }
        QPushButton#primary { background-color: #008cc8; color: white; font-weight: 600; }
        QPushButton#primary:hover { background-color: #009fe0; }
        QPushButton#primary:pressed { background-color: #0070a0; }
        QPushButton#success { background-color: #2d7d46; color: white; font-weight: 600; }
        QPushButton#success:hover { background-color: #35924f; }
        QGroupBox {
            border: 1px solid #3c3c48; border-radius: 8px;
            margin-top: 12px; padding-top: 8px; font-weight: 600; color: #aaaacc;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
        QLabel { color: #dcdce6; }
        QLabel#header { font-size: 16px; font-weight: 700; color: #ffffff; }
        QLabel#subheader { font-size: 14px; font-weight: 600; color: #aaaacc; }
        QLabel#status_ok { color: #4caf76; font-weight: 600; }
        QLabel#status_warn { color: #f0a030; font-weight: 600; }
        QLabel#status_err { color: #f05050; font-weight: 600; }
        QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
            background-color: #2d2d34; border: 1px solid #46465555;
            border-radius: 5px; padding: 5px 8px; color: #dcdce6;
        }
        QSpinBox:focus, QDoubleSpinBox:focus { border-color: #008cc8; }
        QTabWidget::pane { border: 1px solid #3c3c48; border-radius: 6px; background: #1e1e23; }
        QTabBar::tab {
            background: #2d2d34; color: #aaaacc; border: 1px solid #3c3c48;
            border-bottom: none; border-radius: 6px 6px 0 0; padding: 8px 20px; margin-right: 2px;
        }
        QTabBar::tab:selected { background: #1e1e23; color: #ffffff; }
        QTabBar::tab:hover:!selected { background: #38383f; }
        QScrollBar:vertical { background: #1e1e23; width: 10px; border-radius: 5px; }
        QScrollBar::handle:vertical { background: #3c3c48; border-radius: 5px; min-height: 30px; }
        QScrollBar::handle:vertical:hover { background: #008cc8; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        QTableWidget {
            background: #252530; alternate-background-color: #2a2a36;
            gridline-color: #3c3c48; border: 1px solid #3c3c48; border-radius: 6px;
        }
        QTableWidget::item { padding: 6px; color: #dcdce6; }
        QTableWidget::item:selected { background: #008cc840; }
        QHeaderView::section {
            background: #2d2d34; color: #aaaacc; padding: 8px;
            border: none; border-right: 1px solid #3c3c48; border-bottom: 1px solid #3c3c48; font-weight: 600;
        }
        QProgressBar {
            background: #2d2d34; border-radius: 6px; text-align: center; color: white; font-weight: 600;
        }
        QProgressBar::chunk { background: #008cc8; border-radius: 6px; }
        QStatusBar { background: #14141a; color: #aaaacc; border-top: 1px solid #3c3c48; }
        QCheckBox { color: #dcdce6; }
        QCheckBox::indicator {
            width: 16px; height: 16px; border-radius: 4px;
            border: 1px solid #46465555; background: #2d2d34;
        }
        QCheckBox::indicator:checked { background: #008cc8; border-color: #008cc8; }
        QSplitter::handle { background: #3c3c48; }
        QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #3c3c48; }
        QScrollArea { border: none; }
        QToolTip { background: #2d2d34; color: #dcdce6; border: 1px solid #46465555; border-radius: 4px; padding: 4px 8px; }
    """)
