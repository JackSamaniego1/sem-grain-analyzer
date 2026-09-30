"""
Crash log (UPDATE 4 item 7): nothing may fail silently.

The installed app has no console, so an unexpected Python error or a hard
crash used to vanish without a trace.  :func:`install` sets up

* ``faulthandler`` -> ``crash_native.log`` (hard crashes: access violations,
  Qt aborts; the Python stack of every thread is written),
* ``sys.excepthook`` and ``threading.excepthook`` -> ``grain_analyzer.log``
  (size-capped, rotating), plus warnings logged by the app itself,
* a friendly, non-technical message on screen that says where the log is.

Logs stay in ``%LOCALAPPDATA%\\GrainAnalyzer\\logs`` on this PC.  Nothing is
uploaded or sent anywhere (offline rule, CLAUDE.md).  An exception in a slot
or a worker thread is logged and reported; the app keeps running.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

LOG_NAME = "grain_analyzer.log"
NATIVE_NAME = "crash_native.log"
MAX_BYTES = 1_000_000          # per file; 3 backups -> at most ~4 MB
BACKUPS = 3
NOTIFY_MIN_INTERVAL_S = 15.0   # at most one on-screen message per 15 s

_log = logging.getLogger("grain_analyzer.crash")
_state = {"installed": False, "dir": None, "native": None, "handler": None,
          "prev_sys": None, "prev_thread": None, "last_notice": 0.0, "notifier": None,
          "fault_was_on": False}

#: ``notify(title, text)`` shows the friendly message; tests replace it.
#: None = a QMessageBox on the GUI thread (skipped when no QApplication).
notify: Optional[Callable[[str, str], None]] = None


def log_dir() -> Path:
    """``%LOCALAPPDATA%\\GrainAnalyzer\\logs`` (home fallback off Windows)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "GrainAnalyzer" if base else Path.home() / ".grainanalyzer"
    return root / "logs"


def log_path() -> Path:
    d = _state["dir"]
    return (Path(d) if d else log_dir()) / LOG_NAME


def _cap_file(path: Path, max_bytes: int = MAX_BYTES) -> None:
    """Keep the native crash file small: roll it over to ``.1`` when big."""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            old = path.with_suffix(path.suffix + ".1")
            if old.exists():
                old.unlink()
            path.rename(old)
    except OSError:
        pass


def install(directory: Optional[Path] = None) -> Optional[Path]:
    """Idempotent.  Returns the log file path, or None if the folder cannot
    be written -- the hooks are installed anyway (the on-screen message and
    stderr need no file), the app just runs without a log file."""
    if _state["installed"]:
        return log_path() if _state["handler"] is not None else None
    d = Path(directory) if directory else log_dir()
    handler = None
    try:
        d.mkdir(parents=True, exist_ok=True)
        _probe_writable(d)
        handler = logging.handlers.RotatingFileHandler(
            d / LOG_NAME, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8",
            delay=True)
    except OSError:
        handler = None
    if handler is not None:
        _state["dir"] = d
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level > logging.WARNING or root.level == logging.NOTSET:
            root.setLevel(logging.WARNING)
        _state["handler"] = handler
        native = d / NATIVE_NAME
        _cap_file(native)
        _state["fault_was_on"] = faulthandler.is_enabled()
        try:
            fh = open(native, "a", encoding="utf-8")      # kept open for faulthandler
            fh.write(f"\n--- session started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                     f"pid {os.getpid()} ---\n")
            fh.flush()
            faulthandler.enable(file=fh, all_threads=True)
            _state["native"] = fh
        except (OSError, RuntimeError, ValueError):
            pass
    _state["prev_sys"] = sys.excepthook
    _state["prev_thread"] = threading.excepthook
    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    _state["installed"] = True
    return log_path() if handler is not None else None


def _probe_writable(d: Path) -> None:
    """Raise OSError when files cannot be created in ``d`` (a read-only or
    locked-down folder: the rotating handler only fails on first write)."""
    probe = d / f".write_test_{os.getpid()}"
    with open(probe, "w", encoding="utf-8") as f:
        f.write("")
    try:
        probe.unlink()
    except OSError:
        pass


def uninstall() -> None:
    """Undo :func:`install` (tests)."""
    if not _state["installed"]:
        return
    sys.excepthook = _state["prev_sys"] or sys.__excepthook__
    threading.excepthook = _state["prev_thread"] or threading.__excepthook__
    h = _state["handler"]
    if h is not None:
        logging.getLogger().removeHandler(h)
        h.close()
    fh = _state["native"]
    if fh is not None:
        try:
            faulthandler.disable()
            fh.close()
            if _state["fault_was_on"] and sys.stderr is not None:
                faulthandler.enable(file=sys.stderr, all_threads=True)
        except (OSError, ValueError, AttributeError):
            pass
    _state.update(installed=False, dir=None, native=None, handler=None,
                  prev_sys=None, prev_thread=None, last_notice=0.0, fault_was_on=False)


def is_installed() -> bool:
    return bool(_state["installed"])


# ---------------------------------------------------------------- hooks
def _sys_hook(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        (_state["prev_sys"] or sys.__excepthook__)(exc_type, exc, tb)
        return
    report(exc_type, exc, tb, where="main")


def _thread_hook(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "worker")
    report(args.exc_type, args.exc_value, args.exc_traceback, where=f"thread {name}")


def report(exc_type, exc, tb, where: str = "main") -> None:
    """Log an unexpected error and tell the operator (never raises)."""
    try:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _log.error("Unexpected error (%s):\n%s", where, text)
        h = _state["handler"]
        if h is not None:
            h.flush()
    except Exception:
        pass
    try:
        if sys.stderr is not None:                 # console=False builds: None
            sys.stderr.write(f"Unexpected error ({where}): {exc_type.__name__}: {exc}\n")
    except Exception:
        pass
    _notify_user(exc_type)


def friendly_text(path: Optional[Path] = None) -> str:
    head = ("Something went wrong inside Grain Analyzer. Your saved work is not affected, "
            "and the program keeps running. If something looks wrong, save your work and "
            "restart the program.\n\n")
    if path is None and _state["installed"] and _state["handler"] is None:
        return head + ("The details could not be written to a log file on this computer "
                       "(the log folder is not writable). Nothing is sent anywhere.")
    p = path or log_path()
    return head + ("Details were written to a log file on this computer (nothing is sent "
                   f"anywhere):\n{p}\n\n"
                   "If this happens again, please give that file to your support contact.")


def _notify_user(exc_type) -> None:
    now = time.monotonic()
    if now - _state["last_notice"] < NOTIFY_MIN_INTERVAL_S:
        return
    _state["last_notice"] = now
    title = "Unexpected problem"
    text = friendly_text()
    if notify is not None:
        try:
            notify(title, text)
        except Exception:
            pass
        return
    try:
        n = _get_notifier()
        if n is not None:
            n.show.emit(title, text)               # queued to the GUI thread
    except Exception:
        pass


_notifier_lock = threading.Lock()


def prepare_gui() -> None:
    """Create the on-screen notifier now (call on the GUI thread once the
    QApplication exists).  Optional: :func:`_get_notifier` creates it on
    first use from any thread and moves it to the GUI thread."""
    try:
        _get_notifier()
    except Exception:
        pass


def _get_notifier():
    """The notifier, living on the GUI thread whichever thread reports
    first (created under a lock; ``moveToThread`` pushes it from the
    creating thread to the GUI thread before any signal is emitted)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return None
    with _notifier_lock:
        n = _state["notifier"]
        if n is None:
            n = _make_notifier(app)
            _state["notifier"] = n
    return n if n.thread() is app.thread() else None


def notice_visible() -> bool:
    """A crash message is on screen now."""
    n = _state["notifier"]
    return bool(n is not None and n.is_showing())


def _make_notifier(app):
    from PySide6.QtCore import QObject, Qt, Signal

    class _Notifier(QObject):
        show = Signal(str, str)

        def __init__(self) -> None:
            super().__init__()
            self._box = None
            self.shown = 0                         # boxes opened (tests)

        def is_showing(self) -> bool:
            try:
                return self._box is not None and self._box.isVisible()
            except RuntimeError:                   # deleted on close
                return False

        def _closed(self, *_a) -> None:
            self._box = None

        def _show(self, title: str, text: str) -> None:
            # an error repeating (e.g. in a timer slot) shows ONE box, not a
            # new one every NOTIFY_MIN_INTERVAL_S while the first is open
            if self.is_showing():
                return
            from PySide6.QtWidgets import QApplication, QMessageBox
            box = QMessageBox(QApplication.activeWindow())
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(title)
            box.setText(text)
            box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            box.setAttribute(Qt.WA_DeleteOnClose)
            box.finished.connect(self._closed)
            self._box = box
            self.shown += 1
            box.open()                             # non-blocking

    n = _Notifier()
    if n.thread() is not app.thread():
        n.moveToThread(app.thread())               # push from the creating thread
    n.show.connect(n._show, Qt.QueuedConnection)
    return n


__all__ = ["install", "uninstall", "is_installed", "log_dir", "log_path", "report", "prepare_gui",
           "friendly_text", "notify", "notice_visible"]
