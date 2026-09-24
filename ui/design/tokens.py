"""
Design tokens for SEM Grain Analyzer v3.

Pure Python (no Qt imports) so that ``reports/`` and tests can import the
palette without pulling in a GUI toolkit.  Every colour, size and duration
used by ``ui/design/theme.py`` and ``ui/widgets`` comes from here.

Visual direction: calm, precise instrument software (Keyence VHX / Zeiss ZEN
class).  Neutral blue-grey surfaces, one instrument-blue accent, semantic
colours reserved for status, motion 150-250 ms ease-out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Colour sets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    """Background layers, darkest/lowest first."""
    bg: str          # window background
    surface1: str    # panels, cards
    surface2: str    # inputs, raised rows, hover on surface1
    surface3: str    # pressed / selected rows, skeleton base
    elevated: str    # menus, popovers, toasts, dialogs
    scrim: str       # overlay behind modal-less overlays (rgba hex #AARRGGBB)


@dataclass(frozen=True)
class Text:
    """Foreground text colours."""
    primary: str
    secondary: str
    tertiary: str
    disabled: str
    inverse: str


@dataclass(frozen=True)
class Border:
    """Hairlines and focus rings."""
    subtle: str
    strong: str
    focus: str


@dataclass(frozen=True)
class Accent:
    """The single brand/instrument accent."""
    base: str        # fills (primary button, selection pill bar)
    hover: str
    pressed: str
    fg: str          # text/icon on an accent fill
    text: str        # accent used as text/link/icon on surfaces
    subtle: str      # tinted background (selected nav item, focus halo)
    subtle_border: str


@dataclass(frozen=True)
class Semantic:
    """Status colour triplet + a solid fill for buttons/indicators."""
    bg: str
    fg: str
    border: str
    solid: str
    on_solid: str


@dataclass(frozen=True)
class ThemeTokens:
    """Complete colour token set for one theme mode."""
    mode: str
    surface: Surface
    text: Text
    border: Border
    accent: Accent
    success: Semantic
    warning: Semantic
    danger: Semantic
    info: Semantic
    neutral: Semantic
    dataviz: Tuple[str, ...]

    def semantic(self, kind: str) -> Semantic:
        """Return the semantic colour set by name (success/warning/danger/info/neutral/accent)."""
        if kind == "accent":
            a = self.accent
            return Semantic(a.subtle, a.text, a.subtle_border, a.base, a.fg)
        return getattr(self, kind, self.neutral)


# Colour-blind-safe categorical palettes (Okabe-Ito derived).  Order matters:
# series 1..8.  Dark variant lifts luminance of the deep blue/green and swaps
# black for a neutral grey; light variant darkens the yellow for white paper.
DATAVIZ_DARK: Tuple[str, ...] = (
    "#56B4E9",  # sky blue
    "#E69F00",  # orange
    "#1DB58C",  # bluish green
    "#F0E442",  # yellow
    "#EA7A3B",  # vermillion
    "#D48DBA",  # reddish purple
    "#4A94DB",  # blue
    "#B7BEC8",  # neutral grey
)
DATAVIZ_LIGHT: Tuple[str, ...] = (
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#56B4E9",  # sky blue
    "#B8A800",  # dark yellow
    "#3F4650",  # graphite
)


DARK = ThemeTokens(
    mode="dark",
    surface=Surface(
        bg="#0D1117",
        surface1="#141A22",
        surface2="#1A212B",
        surface3="#232C38",
        elevated="#1C2430",
        scrim="#A6080B10",
    ),
    text=Text(
        primary="#E7EBF0",
        secondary="#A8B3C2",
        tertiary="#8C97A7",
        disabled="#5B6573",
        inverse="#0D1117",
    ),
    border=Border(subtle="#252E3A", strong="#364252", focus="#4DA3FF"),
    accent=Accent(
        base="#276CC8",
        hover="#2B74D4",
        pressed="#215DAE",
        fg="#FFFFFF",
        text="#6CB2FF",
        subtle="#16283F",
        subtle_border="#23456E",
    ),
    success=Semantic("#10281F", "#5FD3A0", "#1F4B38", "#1C8558", "#FFFFFF"),
    warning=Semantic("#2B2213", "#F2C366", "#58431C", "#D79520", "#141008"),
    danger=Semantic("#2D1618", "#FF8A8E", "#5B2429", "#CF3A41", "#FFFFFF"),
    info=Semantic("#122338", "#7DBBFF", "#224168", "#276CC8", "#FFFFFF"),
    neutral=Semantic("#1D2530", "#B7C1CE", "#323D4B", "#4A5566", "#FFFFFF"),
    dataviz=DATAVIZ_DARK,
)

LIGHT = ThemeTokens(
    mode="light",
    surface=Surface(
        bg="#F3F5F8",
        surface1="#FFFFFF",
        surface2="#F0F2F6",
        surface3="#E4E8EE",
        elevated="#FFFFFF",
        scrim="#730D1117",
    ),
    text=Text(
        primary="#131A22",
        secondary="#48525F",
        tertiary="#5F6A78",
        disabled="#A2ABB7",
        inverse="#FFFFFF",
    ),
    border=Border(subtle="#E1E5EB", strong="#C5CDD8", focus="#1668CC"),
    accent=Accent(
        base="#1668CC",
        hover="#125BB5",
        pressed="#0E4C98",
        fg="#FFFFFF",
        text="#0F5DB8",
        subtle="#E4EFFC",
        subtle_border="#B5D0F2",
    ),
    success=Semantic("#E5F5ED", "#0E6B45", "#A9DCC3", "#18865A", "#FFFFFF"),
    warning=Semantic("#FDF3E0", "#825400", "#EED29A", "#C98400", "#1A1100"),
    danger=Semantic("#FCEBEC", "#AE1F26", "#F2B8BB", "#C8323A", "#FFFFFF"),
    info=Semantic("#E4EFFC", "#0F5DB8", "#B5D0F2", "#1668CC", "#FFFFFF"),
    neutral=Semantic("#EEF1F5", "#3E4855", "#D3D9E1", "#5A6574", "#FFFFFF"),
    dataviz=DATAVIZ_LIGHT,
)

THEMES: Dict[str, ThemeTokens] = {"dark": DARK, "light": LIGHT}


def tokens_for(mode: str) -> ThemeTokens:
    """Return the token set for ``"dark"`` or ``"light"``."""
    try:
        return THEMES[mode]
    except KeyError:
        raise ValueError(f"unknown theme mode {mode!r}; expected 'dark' or 'light'")


def dataviz_palette(mode: str = "light") -> List[str]:
    """Categorical chart colours for a mode (reports use ``"light"``)."""
    return list(tokens_for(mode).dataviz)


# ---------------------------------------------------------------------------
# Mode-independent scales
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spacing:
    """4-pt spacing grid (px)."""
    xxs: int = 2
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32
    xxxl: int = 48


@dataclass(frozen=True)
class Radii:
    """Corner radii (px)."""
    sm: int = 4
    md: int = 6
    lg: int = 10
    pill: int = 999


@dataclass(frozen=True)
class TypeStyle:
    """One step of the typography scale."""
    size: int       # px
    weight: int     # CSS weight 100..900
    line: int       # line-height hint px
    letter: float = 0.0  # letter spacing, % (QFont.PercentageSpacing delta)


@dataclass(frozen=True)
class Typography:
    """Font stacks and the type scale."""
    family: Tuple[str, ...] = ("Segoe UI Variable Text", "Segoe UI Variable",
                               "Segoe UI", "Inter", "Arial")
    display_family: Tuple[str, ...] = ("Segoe UI Variable Display", "Segoe UI Variable",
                                       "Segoe UI", "Inter", "Arial")
    mono_family: Tuple[str, ...] = ("Cascadia Mono", "Consolas", "Courier New")
    display: TypeStyle = TypeStyle(28, 600, 36, -0.5)
    h1: TypeStyle = TypeStyle(22, 600, 30, -0.3)
    h2: TypeStyle = TypeStyle(17, 600, 24)
    h3: TypeStyle = TypeStyle(14, 600, 20)
    body: TypeStyle = TypeStyle(13, 400, 20)
    body_strong: TypeStyle = TypeStyle(13, 600, 20)
    caption: TypeStyle = TypeStyle(11, 400, 16)
    overline: TypeStyle = TypeStyle(10, 600, 14, 8.0)
    mono: TypeStyle = TypeStyle(13, 400, 20)
    stat: TypeStyle = TypeStyle(26, 600, 32, -0.5)


@dataclass(frozen=True)
class ShadowLevel:
    """QGraphicsDropShadowEffect parameters."""
    blur: int
    y: int
    alpha_dark: float
    alpha_light: float

    def alpha(self, mode: str) -> float:
        """Shadow opacity for a theme mode."""
        return self.alpha_dark if mode == "dark" else self.alpha_light


@dataclass(frozen=True)
class Elevation:
    """Elevation levels 0..3."""
    levels: Tuple[ShadowLevel, ...] = (
        ShadowLevel(0, 0, 0.0, 0.0),
        ShadowLevel(10, 2, 0.35, 0.08),
        ShadowLevel(20, 5, 0.45, 0.12),
        ShadowLevel(32, 10, 0.55, 0.18),
    )

    def level(self, n: int) -> ShadowLevel:
        """Clamp and return an elevation level."""
        return self.levels[max(0, min(n, len(self.levels) - 1))]


@dataclass(frozen=True)
class Motion:
    """Durations (ms) and easing names (QEasingCurve.Type member names)."""
    instant: int = 0
    fast: int = 150
    base: int = 200
    slow: int = 250
    count_up: int = 650
    shimmer: int = 1400
    spin: int = 1100
    toast_timeout: int = 4500
    ease_out: str = "OutCubic"
    ease_in: str = "InCubic"
    ease_in_out: str = "InOutCubic"
    emphasized: str = "OutQuart"
    linear: str = "Linear"


@dataclass(frozen=True)
class Sizes:
    """Control heights and fixed chrome sizes (px)."""
    control_sm: int = 28
    control_md: int = 32
    control_lg: int = 40
    icon_sm: int = 14
    icon_md: int = 16
    icon_lg: int = 20
    rail_collapsed: int = 64
    rail_expanded: int = 212
    rail_item: int = 44
    toast_width: int = 360


SPACE = Spacing()
RADII = Radii()
TYPE = Typography()
ELEVATION = Elevation()
MOTION = Motion()
SIZES = Sizes()


# ---------------------------------------------------------------------------
# Colour maths + contrast checker (WCAG 2.x)
# ---------------------------------------------------------------------------


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Parse ``#RRGGBB`` or ``#AARRGGBB`` into an (r, g, b) tuple (alpha dropped)."""
    h = hex_color.lstrip("#")
    if len(h) == 8:
        h = h[2:]
    if len(h) != 6:
        raise ValueError(f"bad colour {hex_color!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
    """Format an (r, g, b) tuple as ``#RRGGBB``."""
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """Linear blend of two hex colours (t=0 -> a, t=1 -> b)."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an sRGB hex colour."""
    def chan(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(hex_color)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 .. 21.0)."""
    la, lb = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_contrast_pairs(t: ThemeTokens) -> List[Tuple[str, str, str]]:
    """(name, fg, bg) pairs that carry body text and must meet 4.5:1."""
    s, x = t.surface, t.text
    pairs: List[Tuple[str, str, str]] = []
    for sname in ("bg", "surface1", "surface2", "elevated"):
        bg = getattr(s, sname)
        for tname in ("primary", "secondary", "tertiary"):
            pairs.append((f"text.{tname}/{sname}", getattr(x, tname), bg))
        pairs.append((f"accent.text/{sname}", t.accent.text, bg))
    pairs.append(("text.primary/surface3", x.primary, s.surface3))
    pairs.append(("text.secondary/surface3", x.secondary, s.surface3))
    pairs.append(("accent.fg/accent.base", t.accent.fg, t.accent.base))
    pairs.append(("accent.fg/accent.hover", t.accent.fg, t.accent.hover))
    pairs.append(("accent.text/accent.subtle", t.accent.text, t.accent.subtle))
    for kind in ("success", "warning", "danger", "info", "neutral"):
        sem: Semantic = getattr(t, kind)
        pairs.append((f"{kind}.fg/{kind}.bg", sem.fg, sem.bg))
        pairs.append((f"{kind}.on_solid/{kind}.solid", sem.on_solid, sem.solid))
    return pairs


def contrast_failures(t: ThemeTokens, minimum: float = 4.5) -> List[Tuple[str, float]]:
    """Return every text pair in ``t`` below ``minimum`` contrast."""
    return [(name, round(contrast_ratio(fg, bg), 2))
            for name, fg, bg in text_contrast_pairs(t)
            if contrast_ratio(fg, bg) < minimum]


__all__ = [
    "Surface", "Text", "Border", "Accent", "Semantic", "ThemeTokens",
    "DARK", "LIGHT", "THEMES", "DATAVIZ_DARK", "DATAVIZ_LIGHT",
    "tokens_for", "dataviz_palette",
    "SPACE", "RADII", "TYPE", "ELEVATION", "MOTION", "SIZES",
    "hex_to_rgb", "rgb_to_hex", "mix", "relative_luminance", "contrast_ratio",
    "text_contrast_pairs", "contrast_failures",
]
