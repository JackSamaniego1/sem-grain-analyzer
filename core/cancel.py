"""
Cooperative cancellation for long-running analysis (UX-07).

The GUI owns a cancel token (a ``threading.Event`` or any zero-argument
callable returning ``True`` once cancel was requested) and passes it into
``GrainDetector.analyze(..., cancel=token)``.  The detector calls the check
between pipeline stages, inside per-region / per-mask loops and between SAM
point batches and encoder blocks; when the token fires it raises
:class:`AnalysisCancelled`, which unwinds the whole analysis without
producing (or saving) a partial result.

No Qt here: ``core/`` stays GUI-free.
"""
from __future__ import annotations

from typing import Any, Callable


class AnalysisCancelled(Exception):
    """The caller cancelled the analysis; no result was produced."""


def _never() -> None:
    return None


def make_cancel_check(cancel: Any = None) -> Callable[[], None]:
    """Normalise a cancel token into ``check()`` that raises
    :class:`AnalysisCancelled` once cancellation was requested.

    ``cancel`` may be ``None`` (never cancels), an Event-like object with
    ``is_set()``, or a zero-argument callable returning truthy on cancel.
    """
    if cancel is None:
        return _never
    is_set = getattr(cancel, "is_set", None)
    probe = is_set if callable(is_set) else cancel
    if not callable(probe):
        raise TypeError("cancel must be None, an Event-like object or a callable")

    def check() -> None:
        if probe():
            raise AnalysisCancelled("Analysis cancelled")

    return check


__all__ = ["AnalysisCancelled", "make_cancel_check"]
