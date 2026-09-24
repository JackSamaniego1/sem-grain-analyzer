"""
Semantic icon set.

Wraps qtawesome (MIT; icon fonts are bundled inside the package, so this is
fully offline) behind a fixed vocabulary of semantic names.  Screens ask for
``icon("analyze")`` - never a raw glyph id - so the set can be swapped in one
place.  Colours default to theme tokens.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPixmap

# Material Design Icons 6 (Apache-2.0 glyphs, OFL font) via qtawesome's "mdi6" prefix.
ICONS: Dict[str, str] = {
    # navigation
    "projects": "mdi6.folder-multiple-outline",
    "analyze": "mdi6.microscope",
    "review": "mdi6.clipboard-check-outline",
    "reports": "mdi6.file-document-outline",
    "settings": "mdi6.cog-outline",
    "dashboard": "mdi6.view-dashboard-outline",
    "menu": "mdi6.menu",
    "home": "mdi6.home-outline",
    # actions
    "search": "mdi6.magnify",
    "close": "mdi6.close",
    "clear": "mdi6.close-circle",
    "add": "mdi6.plus",
    "remove": "mdi6.minus",
    "edit": "mdi6.pencil-outline",
    "delete": "mdi6.delete-outline",
    "save": "mdi6.content-save-outline",
    "open": "mdi6.folder-open-outline",
    "new_folder": "mdi6.folder-plus-outline",
    "import": "mdi6.import",
    "export": "mdi6.export-variant",
    "upload": "mdi6.upload-outline",
    "download": "mdi6.download-outline",
    "refresh": "mdi6.refresh",
    "undo": "mdi6.undo",
    "redo": "mdi6.redo",
    "run": "mdi6.play",
    "stop": "mdi6.stop",
    "more": "mdi6.dots-vertical",
    "filter": "mdi6.filter-variant",
    "tune": "mdi6.tune-variant",
    "show": "mdi6.eye-outline",
    "hide": "mdi6.eye-off-outline",
    "lock": "mdi6.lock-outline",
    "shield": "mdi6.shield-lock-outline",
    "copy": "mdi6.content-copy",
    "history": "mdi6.history",
    # canvas / measurement
    "calibrate": "mdi6.ruler",
    "crop": "mdi6.crop",
    "scan_area": "mdi6.vector-selection",
    "lasso": "mdi6.lasso",
    "merge": "mdi6.merge",
    "split": "mdi6.call-split",
    "zoom_in": "mdi6.magnify-plus-outline",
    "zoom_out": "mdi6.magnify-minus-outline",
    "fit": "mdi6.fit-to-screen-outline",
    "pointer": "mdi6.cursor-default-outline",
    "target": "mdi6.crosshairs-gps",
    "grains": "mdi6.hexagon-multiple-outline",
    "layers": "mdi6.layers-outline",
    "image": "mdi6.image-outline",
    "images": "mdi6.image-multiple-outline",
    # data / reports
    "histogram": "mdi6.chart-histogram",
    "chart": "mdi6.chart-box-outline",
    "table": "mdi6.table",
    "excel": "mdi6.file-excel-outline",
    "powerpoint": "mdi6.file-powerpoint-outline",
    "database": "mdi6.database-outline",
    "sample": "mdi6.flask-outline",
    "tag": "mdi6.tag-outline",
    "calendar": "mdi6.calendar-outline",
    # status / system
    "success": "mdi6.check-circle-outline",
    "warning": "mdi6.alert-outline",
    "danger": "mdi6.alert-circle-outline",
    "info": "mdi6.information-outline",
    "neutral": "mdi6.information-outline",
    "check": "mdi6.check",
    "help": "mdi6.help-circle-outline",
    "keyboard": "mdi6.keyboard-outline",
    "notifications": "mdi6.bell-outline",
    "user": "mdi6.account-outline",
    "cpu": "mdi6.chip",
    "gpu": "mdi6.memory",
    "trend_up": "mdi6.trending-up",
    "trend_down": "mdi6.trending-down",
    "arrow_up": "mdi6.arrow-up",
    "arrow_down": "mdi6.arrow-down",
    "chevron_right": "mdi6.chevron-right",
    "chevron_left": "mdi6.chevron-left",
    "chevron_down": "mdi6.chevron-down",
    "theme_dark": "mdi6.weather-night",
    "theme_light": "mdi6.white-balance-sunny",
}

_cache: Dict[Tuple[str, str, str], QIcon] = {}


def glyph(name: str) -> str:
    """Return the qtawesome glyph id for a semantic name (raw ids pass through)."""
    if name in ICONS:
        return ICONS[name]
    if "." in name:
        return name
    raise KeyError(f"unknown icon {name!r}; add it to ui.design.icons.ICONS")


def _hex(color) -> str:
    return QColor(color).name(QColor.HexArgb) if color is not None else ""


def icon(name: str, color=None, active_color=None) -> QIcon:
    """Themed QIcon for a semantic name; colour defaults to text.secondary."""
    import qtawesome as qta
    from ui.design.theme import current_tokens

    t = current_tokens()
    c = _hex(color) or t.text.secondary
    a = _hex(active_color) or c
    key = (name, c, a)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ic = qta.icon(glyph(name), color=c, color_active=a, color_selected=a,
                  color_disabled=t.text.disabled)
    _cache[key] = ic
    return ic


def pixmap(name: str, size: int = 16, color=None) -> QPixmap:
    """Themed pixmap for a semantic name at ``size`` px."""
    return icon(name, color).pixmap(QSize(size, size))


def clear_cache() -> None:
    """Drop cached icons (called on theme change)."""
    _cache.clear()


__all__ = ["ICONS", "glyph", "icon", "pixmap", "clear_cache"]
