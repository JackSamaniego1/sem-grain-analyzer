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

from PySide6.QtWidgets import QApplication
from version import __version__, APP_NAME, APP_PUBLISHER


_SPLASH_HEAD_START_MS = 280


def _create_splash():
    """Animated grain-microstructure splash (ui/widgets/splash.py, D-40).
    It animates while the main window loads and closes itself once the
    window is ready and one animation pass has played."""
    from ui.widgets.splash import GrainSplash
    return GrainSplash(APP_NAME, __version__, APP_PUBLISHER)


def configure_runtime() -> dict:
    """UPDATE 4 item 7, run once before any analysis or info-bar reading:

    * crash log (faulthandler + sys/threading excepthooks -> a size-capped
      log in %LOCALAPPDATA%\\GrainAnalyzer\\logs; nothing is uploaded),
    * thread caps for OpenCV / torch / OCR (core.perf) so the window stays
      responsive on weak PCs.  Never raises; does not import torch.
    Returns the thread caps."""
    import logging
    log = logging.getLogger("grain_analyzer")
    try:
        from ui import crash_log
        path = crash_log.install()
        if path is not None:
            log.debug("crash log: %s", path)
    except Exception:          # the app must start even without a log
        pass
    caps = {}
    try:
        from core.perf import configure_threads
        caps = configure_threads()
        log.info("thread caps: %s", caps)
    except Exception:
        log.warning("thread caps could not be applied", exc_info=True)
    return caps


def main():
    configure_runtime()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    from ui import crash_log
    crash_log.prepare_gui()

    from ui.design.theme import apply_theme
    from data.settings import load_settings
    theme = load_settings().theme
    apply_theme(app, theme if theme in ("dark", "light") else "dark")

    splash = _create_splash()
    splash.set_progress(0.2)
    splash.show()
    app.processEvents()

    holder = {}

    def _build_window():
        # The splash clock counts only rendered time, so the GUI-thread block
        # below pauses (not skips) the animation; processEvents at the
        # checkpoints lets a few frames paint in between.
        try:
            splash.set_message("Loading workspace…")
            splash.set_progress(0.45)
            app.processEvents()
            from ui.app_shell import AppShell
            splash.set_progress(0.7)
            app.processEvents()
            window = AppShell()
            holder["window"] = window
            app.processEvents()
        except BaseException:
            splash.close()
            sys.excepthook(*sys.exc_info())
            app.exit(1)
            return
        # closes once the window is ready AND one rendered pass has played
        splash.set_message("Ready")
        splash.finish(window.show)

    # head start: let the first ~0.28 s of the animation actually paint before
    # the heavy window build blocks the GUI thread
    from PySide6.QtCore import QTimer
    QTimer.singleShot(_SPLASH_HEAD_START_MS, _build_window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
