"""Reporting package — editable ReportModel + Excel/PowerPoint renderers.

No Qt imports here (reports/ is UI-agnostic, same rule as core/ and data/).
"""
from __future__ import annotations

from data.models import sanitize_name

_LEGACY_BASENAME = "Grain_Analysis_Report"

_EXT = {"xlsx": ".xlsx", "pptx": ".pptx"}


def suggest_filename(model, kind: str) -> str:
    """Default export filename for ``model`` (``kind`` is 'xlsx' or 'pptx').

    Uses ``model.export_basename`` (already rendered by the UI from the
    workspace's hierarchy profile export_name_template) when set; otherwise
    falls back to the pre-HIER-01 default derived from the report title.
    The result is sanitized for Windows (no path separators/reserved chars).
    """
    ext = _EXT.get(kind, f".{kind}")
    base = (getattr(model, "export_basename", "") or "").strip()
    if not base:
        title = (getattr(model, "title", "") or "").strip()
        base = title.replace(" ", "_") if title else _LEGACY_BASENAME
    base = sanitize_name(base)
    return base + ext
