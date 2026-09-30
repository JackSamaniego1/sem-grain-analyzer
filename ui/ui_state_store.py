"""
Debounced, off-UI-thread writer for ``ui_state.json`` (UPDATE 4 item 7).

Saving the UI state on the UI thread took 280-350 ms on a busy PC (e.g. on
release of the overlay-opacity slider).  :func:`submit` now only takes a
snapshot (a deep copy of a small dict) on the calling thread; one daemon
thread waits :data:`DEBOUNCE_S` for more changes and writes the LATEST
snapshot atomically (last write wins).  :func:`flush` writes whatever is
pending at once and waits for it -- called on app close and before the file
is read back (``load_ui_state``) so a reader never sees an older state.
"""
from __future__ import annotations

import atexit
import copy
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

_log = logging.getLogger(__name__)

#: quiet time before the pending snapshot is written
DEBOUNCE_S = 0.4


class UiStateWriter:
    """One background writer; only the newest snapshot is ever written."""

    def __init__(self, write: Callable[[Path, dict], None],
                 debounce_s: Optional[float] = None) -> None:
        self._write = write
        self._debounce = DEBOUNCE_S if debounce_s is None else float(debounce_s)
        self._cv = threading.Condition()
        self._pending: Optional[Tuple[int, Path, dict]] = None
        self._due = 0.0
        self._seq = 0                    # last submitted
        self._done = 0                   # last written (or dropped as superseded)
        self._now = False                # flush requested: skip the debounce
        self._thread: Optional[threading.Thread] = None
        self.writes = 0                  # files actually written (tests)
        self.last_thread: Optional[str] = None

    # ------------------------------------------------------------ API
    def submit(self, path: Path, state: dict) -> None:
        snap = copy.deepcopy(state)
        with self._cv:
            self._seq += 1
            self._pending = (self._seq, Path(path), snap)
            self._due = time.monotonic() + self._debounce
            self._ensure_thread()
            self._cv.notify_all()

    def pending(self) -> bool:
        with self._cv:
            return self._done < self._seq

    def flush(self, timeout: float = 10.0) -> bool:
        """Write the pending snapshot now and wait until it is on disk."""
        end = time.monotonic() + timeout
        with self._cv:
            target = self._seq
            if self._done >= target:
                return True
            self._now = True
            self._ensure_thread()
            self._cv.notify_all()
            while self._done < target:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
            if self._pending is None:
                self._now = False
            return True

    # ------------------------------------------------------------ thread
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="ui-state-writer",
                                            daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._pending is None:
                    if not self._cv.wait(30.0) and self._pending is None:
                        self._thread = None          # idle: end; restarted on demand
                        return
                while not self._now:
                    left = self._due - time.monotonic()
                    if left <= 0:
                        break
                    self._cv.wait(left)
                seq, path, snap = self._pending
                self._pending = None
                self._now = False
            try:
                self._write(path, snap)
                self.writes += 1
                self.last_thread = threading.current_thread().name
            except Exception:                        # never kill the writer
                _log.warning("could not save the window layout to %s", path, exc_info=True)
            with self._cv:
                self._done = max(self._done, seq)
                self._cv.notify_all()


_writer: Optional[UiStateWriter] = None
_writer_lock = threading.Lock()


def writer() -> UiStateWriter:
    global _writer
    with _writer_lock:
        if _writer is None:
            from data.models import write_json_atomic
            _writer = UiStateWriter(write_json_atomic)
            atexit.register(_writer.flush)
        return _writer


def submit(path: Path, state: dict) -> None:
    writer().submit(path, state)


def flush(timeout: float = 10.0) -> bool:
    w = _writer
    return True if w is None else w.flush(timeout)


__all__ = ["UiStateWriter", "submit", "flush", "writer", "DEBOUNCE_S"]
