"""
Theme engine: builds a QPalette and a complete Qt stylesheet from tokens.

Usage::

    from ui.design.theme import apply_theme, theme_manager, current_tokens
    apply_theme(app, "dark")              # or "light"
    theme_manager().theme_changed.connect(my_widget.update)

Custom-painted widgets read colours from ``current_tokens()`` at paint time
and repaint on ``theme_changed``; standard Qt widgets are styled by the QSS
generated here.  No other module should hardcode colours.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from typing import Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from ui.design.tokens import (
    RADII, SIZES, SPACE, TYPE, ThemeTokens, mix, tokens_for,
)

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------


class ThemeManager(QObject):
    """Holds the active theme mode + reduced-motion flag and broadcasts changes."""

    theme_changed = Signal(str)
    reduced_motion_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._mode = "dark"
        self._reduced = os.environ.get("GRAIN_REDUCED_MOTION", "") in ("1", "true", "yes")

    @property
    def mode(self) -> str:
        """Active theme mode, ``"dark"`` or ``"light"``."""
        return self._mode

    @property
    def tokens(self) -> ThemeTokens:
        """Active colour tokens."""
        return tokens_for(self._mode)

    @property
    def reduced_motion(self) -> bool:
        """True when animations must complete instantly."""
        return self._reduced

    def set_reduced_motion(self, on: bool) -> None:
        """Enable/disable reduced motion globally."""
        on = bool(on)
        if on != self._reduced:
            self._reduced = on
            self.reduced_motion_changed.emit(on)

    def _set_mode(self, mode: str) -> None:
        tokens_for(mode)  # validate
        self._mode = mode


_manager: Optional[ThemeManager] = None


def theme_manager() -> ThemeManager:
    """Return the process-wide ThemeManager."""
    global _manager
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def current_tokens() -> ThemeTokens:
    """Colour tokens of the active theme (dark until ``apply_theme`` says otherwise)."""
    return theme_manager().tokens


def current_mode() -> str:
    """Active theme mode string."""
    return theme_manager().mode


def reduced_motion() -> bool:
    """Global reduced-motion flag."""
    return theme_manager().reduced_motion


def set_reduced_motion(on: bool) -> None:
    """Set the global reduced-motion flag."""
    theme_manager().set_reduced_motion(on)


# ---------------------------------------------------------------------------
# Fonts (system fonts only - never downloaded)
# ---------------------------------------------------------------------------

_WIN_FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "segoeuil.ttf",
                   "SegUIVar.ttf", "consola.ttf", "consolab.ttf", "CascadiaMono.ttf",
                   "arial.ttf", "arialbd.ttf")
_fonts_checked = False


def ensure_fonts() -> None:
    """Register local Windows fonts if the platform plugin (e.g. offscreen) lacks them."""
    global _fonts_checked
    if _fonts_checked:
        return
    _fonts_checked = True
    families = set(QFontDatabase.families())
    if "Segoe UI" in families or sys.platform != "win32":
        return
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in _WIN_FONT_FILES:
        path = os.path.join(fonts_dir, name)
        if os.path.isfile(path):
            QFontDatabase.addApplicationFont(path)


def _available(stack) -> list:
    fams = set(QFontDatabase.families())
    found = [f for f in stack if f in fams]
    return found or list(stack)


def ui_font(style=None) -> QFont:
    """QFont for a TYPE scale step (default body) using the system font stack."""
    style = style or TYPE.body
    f = QFont()
    f.setFamilies(_available(TYPE.family))
    f.setPixelSize(style.size)
    f.setWeight(QFont.Weight(style.weight))
    if style.letter:
        f.setLetterSpacing(QFont.PercentageSpacing, 100.0 + style.letter)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def mono_font(size: Optional[int] = None) -> QFont:
    """Monospaced QFont for numerals/metadata."""
    f = QFont()
    f.setFamilies(_available(TYPE.mono_family))
    f.setStyleHint(QFont.Monospace)
    f.setPixelSize(size or TYPE.mono.size)
    return f


def _fam(stack) -> str:
    return ", ".join(f'"{x}"' for x in _available(stack))


# ---------------------------------------------------------------------------
# Tiny PNG assets for QSS sub-controls (arrows, check).  Painted from
# tokens into a per-process temp dir, removed at exit.
# ---------------------------------------------------------------------------

_asset_dir: Optional[str] = None
# Polylines in a 16x16 design grid: (points, stroke width).
_GLYPHS = {
    "chevron_down": (((4, 6), (8, 10), (12, 6)), 1.6),
    "chevron_up": (((4, 10), (8, 6), (12, 10)), 1.6),
    "check": (((3.5, 8.5), (6.5, 11.5), (12.5, 4.5)), 2.0),
    "dash": (((4, 8), (12, 8)), 2.0),
}
_ASSET_SCALE = 4  # rendered at 64x64 so QSS downscaling stays crisp on HiDPI


def _cleanup_assets() -> None:
    if _asset_dir and os.path.isdir(_asset_dir):
        shutil.rmtree(_asset_dir, ignore_errors=True)


def _asset(name: str, color: str) -> str:
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = tempfile.mkdtemp(prefix="grain_theme_")
        atexit.register(_cleanup_assets)
    path = os.path.join(_asset_dir, f"{name}_{color.lstrip('#')}.png")
    if not os.path.isfile(path):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen

        k = _ASSET_SCALE
        img = QImage(16 * k, 16 * k, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        pts, width = _GLYPHS[name]
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color), width * k)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        pp = QPainterPath(QPointF(pts[0][0] * k, pts[0][1] * k))
        for x, y in pts[1:]:
            pp.lineTo(QPointF(x * k, y * k))
        p.drawPath(pp)
        p.end()
        img.save(path, "PNG")
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# Button colours (shared by QSS and AnimatedButton)
# ---------------------------------------------------------------------------

BUTTON_VARIANTS = ("primary", "secondary", "ghost", "danger", "success")


def button_colors(t: ThemeTokens, variant: str) -> Dict[str, str]:
    """Colours for a button variant: bg/bg_hover/bg_pressed/fg/fg_hover/border/border_hover."""
    s, x, b, a = t.surface, t.text, t.border, t.accent
    if variant == "primary":
        return dict(bg=a.base, bg_hover=a.hover, bg_pressed=a.pressed, fg=a.fg, fg_hover=a.fg,
                    border=a.base, border_hover=a.hover)
    if variant in ("danger", "success"):
        sem = t.semantic(variant)
        return dict(bg=sem.solid, bg_hover=mix(sem.solid, "#000000", 0.10),
                    bg_pressed=mix(sem.solid, "#000000", 0.22), fg=sem.on_solid,
                    fg_hover=sem.on_solid, border=sem.solid,
                    border_hover=mix(sem.solid, "#000000", 0.10))
    if variant == "ghost":
        return dict(bg="#00000000", bg_hover=s.surface2, bg_pressed=s.surface3, fg=x.secondary,
                    fg_hover=x.primary, border="#00000000", border_hover="#00000000")
    # secondary (default)
    return dict(bg=s.surface2, bg_hover=s.surface3, bg_pressed=mix(s.surface3, b.strong, 0.5),
                fg=x.primary, fg_hover=x.primary, border=b.strong,
                border_hover=mix(b.strong, x.tertiary, 0.35))


# ---------------------------------------------------------------------------
# Palette + stylesheet
# ---------------------------------------------------------------------------


def build_palette(t: ThemeTokens) -> QPalette:
    """QPalette mirroring the tokens (used by native-drawn parts and custom widgets)."""
    p = QPalette()
    s, x, a = t.surface, t.text, t.accent
    C = QColor
    roles = {
        QPalette.Window: s.bg, QPalette.WindowText: x.primary,
        QPalette.Base: s.surface1, QPalette.AlternateBase: s.surface2,
        QPalette.Text: x.primary, QPalette.Button: s.surface2, QPalette.ButtonText: x.primary,
        QPalette.BrightText: x.inverse, QPalette.Highlight: a.base,
        QPalette.HighlightedText: a.fg, QPalette.ToolTipBase: s.elevated,
        QPalette.ToolTipText: x.primary, QPalette.PlaceholderText: x.tertiary,
        QPalette.Link: a.text, QPalette.LinkVisited: a.text,
        QPalette.Light: s.surface3, QPalette.Midlight: s.surface2,
        QPalette.Mid: t.border.strong, QPalette.Dark: t.border.subtle, QPalette.Shadow: "#000000",
    }
    for role, col in roles.items():
        p.setColor(role, C(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, C(x.disabled))
    p.setColor(QPalette.Disabled, QPalette.Base, C(s.surface2))
    p.setColor(QPalette.Disabled, QPalette.Highlight, C(t.border.strong))
    return p


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(alpha * 255)})"


def build_stylesheet(t: ThemeTokens) -> str:
    """Complete application QSS generated from a token set."""
    s, x, b, a = t.surface, t.text, t.border, t.accent
    r = RADII
    h_md = SIZES.control_md
    fam = _fam(TYPE.family)
    mono = _fam(TYPE.mono_family)
    chev = _asset("chevron_down", x.secondary)
    chev_dis = _asset("chevron_down", x.disabled)
    up = _asset("chevron_up", x.secondary)
    check = _asset("check", a.fg)
    dash = _asset("dash", a.fg)
    dark = t.mode == "dark"
    tip_bg = s.elevated if dark else "#1C2430"
    tip_fg = x.primary if dark else "#F2F4F7"
    tip_border = b.strong if dark else "#1C2430"

    btn_rules = []
    for v in BUTTON_VARIANTS:
        c = button_colors(t, v)
        sel = f'QPushButton[variant="{v}"]'
        btn_rules.append(f"""
{sel} {{ background: {c['bg']}; color: {c['fg']}; border: 1px solid {c['border']}; }}
{sel}:hover {{ background: {c['bg_hover']}; color: {c['fg_hover']}; border-color: {c['border_hover']}; }}
{sel}:pressed {{ background: {c['bg_pressed']}; }}""")
    btn_rules.append(f"""
QPushButton[variant="primary"]:default, QPushButton:default {{ border-color: {a.hover}; }}""")

    type_rules = []
    for role in ("display", "h1", "h2", "h3", "body", "body_strong", "caption", "overline", "stat"):
        st = getattr(TYPE, role)
        family = _fam(TYPE.display_family) if role in ("display", "h1", "stat") else fam
        type_rules.append(
            f'QLabel[role="{role}"] {{ font-family: {family}; font-size: {st.size}px; '
            f'font-weight: {st.weight}; }}')

    return f"""
/* ---- generated by ui/design/theme.py ({t.mode}) - do not edit ---- */
QWidget {{ color: {x.primary}; font-family: {fam}; font-size: {TYPE.body.size}px; }}
QWidget:disabled {{ color: {x.disabled}; }}
QMainWindow, QDialog, QMessageBox {{ background: {s.bg}; }}
QDialog, QMessageBox {{ background: {s.surface1}; }}
QMainWindow::separator {{ background: {b.subtle}; width: 1px; height: 1px; }}
QLabel {{ background: transparent; }}
QAbstractScrollArea {{ background: transparent; }}

/* ---- typography roles: QLabel[role=...] ---- */
{chr(10).join(type_rules)}
QLabel[role="overline"] {{ color: {x.tertiary}; }}
QLabel[role="caption"] {{ color: {x.secondary}; }}
QLabel[tone="secondary"] {{ color: {x.secondary}; }}
QLabel[tone="tertiary"] {{ color: {x.tertiary}; }}
QLabel[tone="accent"] {{ color: {a.text}; }}
QLabel[tone="success"] {{ color: {t.success.fg}; }}
QLabel[tone="warning"] {{ color: {t.warning.fg}; }}
QLabel[tone="danger"] {{ color: {t.danger.fg}; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: {TYPE.mono.size}px; }}
QPlainTextEdit[role="mono"], QTextEdit[role="mono"] {{ font-family: {mono}; font-size: 12px; }}
QLabel[role="kbd"] {{ font-family: {mono}; font-size: 11px; color: {x.primary};
    background: {s.surface2}; border: 1px solid {b.strong}; border-bottom-width: 2px;
    border-radius: {r.sm}px; padding: 1px 6px; }}

/* ---- buttons ---- */
QPushButton {{
    background: {s.surface2}; color: {x.primary}; border: 1px solid {b.strong};
    border-radius: {r.md}px; padding: 0 {SPACE.lg}px; min-height: {h_md - 2}px; font-weight: 600;
}}
QPushButton:hover {{ background: {s.surface3}; }}
QPushButton:pressed {{ background: {mix(s.surface3, b.strong, 0.5)}; }}
QPushButton:focus {{ border-color: {b.focus}; }}
QPushButton:disabled {{ background: {s.surface2}; color: {x.disabled}; border-color: {b.subtle}; }}
QPushButton:checked {{ background: {a.subtle}; color: {a.text}; border-color: {a.subtle_border}; }}
QPushButton::menu-indicator {{ image: url({chev}); subcontrol-position: right center;
    subcontrol-origin: padding; width: 14px; right: 6px; }}
{''.join(btn_rules)}
QPushButton[variant]:disabled {{ background: {s.surface2}; color: {x.disabled}; border-color: {b.subtle}; }}

QToolButton {{ background: transparent; color: {x.secondary}; border: 1px solid transparent;
    border-radius: {r.md}px; padding: 4px; }}
QToolButton:hover {{ background: {s.surface2}; color: {x.primary}; }}
QToolButton:pressed {{ background: {s.surface3}; }}
QToolButton:checked {{ background: {a.subtle}; color: {a.text}; border-color: {a.subtle_border}; }}
QToolButton:focus {{ border-color: {b.focus}; }}
QToolButton:disabled {{ color: {x.disabled}; }}
QToolButton[popupMode="1"], QToolButton[popupMode="2"] {{ padding-right: 16px; }}
QToolButton::menu-indicator {{ image: url({chev}); width: 12px; }}
QToolButton[role="crumb"] {{ color: {x.secondary}; padding: 2px 6px; font-size: {TYPE.body.size}px; }}
QToolButton[role="crumb"]:hover {{ color: {x.primary}; background: {s.surface2}; }}
QToolButton[role="crumb"][current="true"] {{ color: {x.primary}; font-weight: 600; }}
QToolButton[role="crumb"][current="true"]:hover {{ background: transparent; }}

/* ---- text inputs ---- */
QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit {{
    background: {s.surface2}; color: {x.primary}; border: 1px solid {b.strong};
    border-radius: {r.md}px; selection-background-color: {a.base}; selection-color: {a.fg};
}}
QLineEdit, QAbstractSpinBox {{ min-height: {h_md - 2}px; padding: 0 {SPACE.sm + 2}px; }}
QTextEdit, QPlainTextEdit {{ padding: {SPACE.xs}px; }}
QLineEdit:hover, QAbstractSpinBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border-color: {mix(b.strong, x.tertiary, 0.35)}; }}
QLineEdit:focus, QAbstractSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {b.focus}; background: {s.surface1}; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background: {s.surface1}; color: {x.disabled}; border-color: {b.subtle}; }}
QLineEdit:read-only {{ background: {s.surface1}; }}
QLineEdit[invalid="true"], QAbstractSpinBox[invalid="true"] {{ border-color: {t.danger.solid}; }}
QLineEdit[role="search"] {{ border-radius: {r.md}px; padding-left: 4px; }}

QAbstractSpinBox {{ padding-right: 22px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: {r.md}px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: {r.md}px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {s.surface3}; }}
QAbstractSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({chev}); width: 10px; height: 10px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: none; }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}
/* date edits with a calendar popup draw a combo-style drop-down (HIER-03) */
QDateTimeEdit[calendarPopup="true"] {{ padding-right: 28px; }}
QDateTimeEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    width: 24px; border: none; background: transparent;
    border-top-right-radius: {r.md}px; border-bottom-right-radius: {r.md}px; }}
QDateTimeEdit::drop-down:hover {{ background: {s.surface3}; }}
QDateTimeEdit::down-arrow {{ image: url({chev}); width: 12px; height: 12px; }}
QDateTimeEdit::down-arrow:disabled {{ image: url({chev_dis}); }}

/* ---- combo box ---- */
QComboBox {{
    background: {s.surface2}; color: {x.primary}; border: 1px solid {b.strong};
    border-radius: {r.md}px; min-height: {h_md - 2}px; padding: 0 28px 0 {SPACE.sm + 2}px;
}}
QComboBox:hover {{ border-color: {mix(b.strong, x.tertiary, 0.35)}; }}
QComboBox:focus, QComboBox:on {{ border-color: {b.focus}; }}
QComboBox:disabled {{ background: {s.surface1}; color: {x.disabled}; border-color: {b.subtle}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    width: 24px; border: none; }}
QComboBox::down-arrow {{ image: url({chev}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({chev_dis}); }}
QComboBox QAbstractItemView {{
    background: {s.elevated}; color: {x.primary}; border: 1px solid {b.strong};
    border-radius: {r.md}px; padding: 4px; outline: 0;
    selection-background-color: {a.subtle}; selection-color: {x.primary};
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; border-radius: {r.sm}px; }}
QComboBox QAbstractItemView::item:hover {{ background: {s.surface3}; }}
QComboBox QAbstractItemView::item:selected {{ background: {a.subtle}; color: {x.primary}; }}

/* ---- check / radio ---- */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; background: {s.surface2}; border: 1px solid {b.strong}; }}
QCheckBox::indicator {{ border-radius: {r.sm}px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {a.hover}; }}
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border-color: {b.focus}; }}
QCheckBox::indicator:checked {{ background: {a.base}; border-color: {a.base}; image: url({check}); }}
QCheckBox::indicator:indeterminate {{ background: {a.base}; border-color: {a.base}; image: url({dash}); }}
QTableView::indicator {{ width: 16px; height: 16px; background: {s.surface2};
    border: 1px solid {b.strong}; border-radius: {r.sm}px; }}
QTableView::indicator:hover {{ border-color: {a.hover}; }}
QTableView::indicator:checked {{ background: {a.base}; border-color: {a.base}; image: url({check}); }}
QRadioButton::indicator:checked {{ border-color: {a.base};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {a.fg}, stop:0.32 {a.fg}, stop:0.42 {a.base}, stop:1 {a.base}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {s.surface1}; border-color: {b.subtle}; }}

/* ---- slider ---- */
QSlider {{ background: transparent; min-height: 20px; }}
QSlider::groove:horizontal {{ height: 4px; background: {s.surface3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {a.base}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -6px 0; border-radius: 8px;
    background: {x.primary if dark else s.surface1}; border: 2px solid {a.base}; }}
QSlider::handle:horizontal:hover {{ border-color: {a.hover}; background: {s.surface1 if not dark else '#FFFFFF'}; }}
QSlider::groove:vertical {{ width: 4px; background: {s.surface3}; border-radius: 2px; }}
QSlider::add-page:vertical {{ background: {a.base}; border-radius: 2px; }}
QSlider::handle:vertical {{ width: 14px; height: 14px; margin: 0 -6px; border-radius: 8px;
    background: {x.primary if dark else s.surface1}; border: 2px solid {a.base}; }}
QSlider::groove:disabled, QSlider::sub-page:disabled {{ background: {s.surface3}; }}
QSlider::handle:disabled {{ border-color: {b.strong}; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; border-top: 1px solid {b.subtle}; top: -1px; background: transparent; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {x.secondary}; padding: 8px 14px;
    border: none; border-bottom: 2px solid transparent; margin-right: 2px; font-weight: 600; }}
QTabBar::tab:hover {{ color: {x.primary}; border-bottom-color: {b.strong}; }}
QTabBar::tab:selected {{ color: {x.primary}; border-bottom-color: {a.base}; }}
QTabBar::tab:disabled {{ color: {x.disabled}; }}
QTabBar::close-button {{ subcontrol-position: right; }}

/* ---- item views ---- */
QAbstractItemView {{
    background: {s.surface1}; alternate-background-color: {s.surface2};
    color: {x.primary}; border: 1px solid {b.subtle}; border-radius: {r.md}px;
    selection-background-color: {a.subtle}; selection-color: {x.primary}; outline: 0;
}}
QTableView, QTableWidget {{ gridline-color: {b.subtle}; }}
QTableView::item, QTableWidget::item {{ padding: 0 8px; }}
QAbstractItemView::item:hover {{ background: {s.surface2}; }}
QAbstractItemView::item:selected {{ background: {a.subtle}; color: {x.primary}; }}
QTreeView::item, QListView::item {{ min-height: 26px; padding: 0 4px; }}
QListView::item {{ border-radius: {r.sm}px; }}
QTreeView::branch {{ background: transparent; }}
QTableCornerButton::section {{ background: {s.surface1}; border: none;
    border-bottom: 1px solid {b.strong}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {s.surface1}; color: {x.secondary}; padding: 6px 10px; border: none;
    border-bottom: 1px solid {b.strong}; border-right: 1px solid {b.subtle};
    font-weight: 600; font-size: {TYPE.caption.size + 1}px;
}}
QHeaderView::section:hover {{ color: {x.primary}; background: {s.surface2}; }}
QHeaderView::section:last {{ border-right: none; }}
QHeaderView::down-arrow {{ image: url({chev}); width: 10px; }}
QHeaderView::up-arrow {{ image: url({up}); width: 10px; }}

/* ---- scrollbars: slim, handle widens on hover ---- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {b.strong}; min-height: 32px; border-radius: 3px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: {b.strong}; min-width: 32px; border-radius: 3px; margin: 3px 2px; }}
QScrollBar:vertical:hover {{ background: {_rgba(s.surface3, 0.5)}; }}
QScrollBar:horizontal:hover {{ background: {_rgba(s.surface3, 0.5)}; }}
QScrollBar::handle:vertical:hover {{ background: {x.tertiary}; margin: 1px 1px; border-radius: 5px; }}
QScrollBar::handle:horizontal:hover {{ background: {x.tertiary}; margin: 1px 1px; border-radius: 5px; }}
QScrollBar::handle:pressed {{ background: {x.secondary}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---- progress ---- */
QProgressBar {{ background: {s.surface3}; border: none; border-radius: 4px; min-height: 8px;
    max-height: 8px; color: transparent; text-align: center; }}
QProgressBar::chunk {{ background: {a.base}; border-radius: 4px; }}
QProgressBar[labelled="true"] {{ min-height: 18px; max-height: 18px; border-radius: 9px;
    color: {x.primary}; font-size: {TYPE.caption.size}px; font-weight: 600; }}
QProgressBar[labelled="true"]::chunk {{ border-radius: 9px; }}
QProgressBar[tone="success"]::chunk {{ background: {t.success.solid}; }}
QProgressBar[tone="danger"]::chunk {{ background: {t.danger.solid}; }}

/* ---- tooltip / menu ---- */
QToolTip {{ background: {tip_bg}; color: {tip_fg}; border: 1px solid {tip_border};
    border-radius: {r.sm}px; padding: 5px 8px; font-size: {TYPE.caption.size + 1}px; }}
QMenuBar {{ background: {s.bg}; color: {x.secondary}; border-bottom: 1px solid {b.subtle}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: {r.sm}px; }}
QMenuBar::item:selected {{ background: {s.surface2}; color: {x.primary}; }}
QMenu {{ background: {s.elevated}; color: {x.primary}; border: 1px solid {b.strong};
    border-radius: {r.lg - 2}px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: {r.sm}px; background: transparent; }}
QMenu::item:selected {{ background: {a.subtle}; color: {x.primary}; }}
QMenu::item:disabled {{ color: {x.disabled}; }}
QMenu::separator {{ height: 1px; background: {b.subtle}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
QMenu::indicator:checked {{ image: url({_asset("check", a.text)}); }}

/* ---- chrome ---- */
QStatusBar {{ background: {s.surface1}; color: {x.secondary}; border-top: 1px solid {b.subtle};
    font-size: {TYPE.caption.size + 1}px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {x.secondary}; padding: 0 6px; }}
QSplitter::handle {{ background: {b.subtle}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {a.base}; }}
QGroupBox {{ background: {s.surface1}; border: 1px solid {b.subtle}; border-radius: {r.lg}px;
    margin-top: 20px; padding: {SPACE.lg}px {SPACE.md}px {SPACE.md}px {SPACE.md}px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 4px;
    padding: 0 4px; color: {x.secondary}; background: transparent; }}
QGroupBox::indicator {{ width: 14px; height: 14px; }}
QMessageBox QLabel {{ color: {x.primary}; }}
QMessageBox QPushButton {{ min-width: 84px; }}
QDialogButtonBox QPushButton {{ min-width: 84px; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {b.subtle}; background: {b.subtle}; border: none; max-height: 1px; }}
QScrollArea {{ border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply_theme(app: Optional[QApplication] = None, mode: str = "dark") -> ThemeTokens:
    """Apply palette + QSS + font for ``mode`` to ``app`` and broadcast ``theme_changed``."""
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("apply_theme() needs a QApplication")
    t = tokens_for(mode)
    mgr = theme_manager()
    mgr._set_mode(mode)
    ensure_fonts()
    from ui.design import icons
    icons.clear_cache()
    app.setStyle("Fusion")
    app.setFont(ui_font())
    app.setPalette(build_palette(t))
    app.setStyleSheet(build_stylesheet(t))
    mgr.theme_changed.emit(mode)
    return t


def toggle_theme(app: Optional[QApplication] = None) -> str:
    """Switch dark <-> light; returns the new mode."""
    new = "light" if current_mode() == "dark" else "dark"
    apply_theme(app, new)
    return new


__all__ = [
    "ThemeManager", "theme_manager", "current_tokens", "current_mode",
    "reduced_motion", "set_reduced_motion", "apply_theme", "toggle_theme",
    "build_palette", "build_stylesheet", "button_colors", "BUTTON_VARIANTS",
    "ensure_fonts", "ui_font", "mono_font",
]
