"""
Single source of truth for the application version.

Every other file (main.py splash, ui/main_window.py title + About box,
ui/settings_panel.py version label, grain_analyzer.spec plist,
create_nsis_script.py installer, README.md) reads from here.
Bump this on release; update CHANGELOG.md alongside it.
"""

__version__ = "3.0.0"
APP_NAME = "Grain Analyzer"
APP_PUBLISHER = "Jack Samaniego"
