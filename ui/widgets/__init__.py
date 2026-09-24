"""
Animated component library for SEM Grain Analyzer v3.

All widgets read colours from ``ui.design.theme.current_tokens()`` and
repaint on ``theme_manager().theme_changed``; all motion honours
``ui.design.theme.set_reduced_motion(True)`` (animations complete instantly).
"""
from ui.widgets.badges import STATUS_KINDS, Badge, Chip
from ui.widgets.buttons import AnimatedButton, IconButton
from ui.widgets.cards import Card, Sparkline, StatCard, label
from ui.widgets.collapsible import CollapsibleSection
from ui.widgets.display import Divider, EmptyState, KeyValueList
from ui.widgets.inputs import SearchBox
from ui.widgets.loading import ProgressRing, Skeleton, Spinner
from ui.widgets.navigation import Breadcrumb, FadeStackedWidget, NavRail
from ui.widgets.overlay import ShortcutOverlay
from ui.widgets.segmented import SegmentedControl
from ui.widgets.toast import Toast, ToastManager

__all__ = [
    "AnimatedButton", "IconButton", "Card", "StatCard", "Sparkline", "label",
    "Badge", "Chip", "STATUS_KINDS", "SegmentedControl", "CollapsibleSection",
    "Toast", "ToastManager", "Skeleton", "Spinner", "ProgressRing",
    "Breadcrumb", "SearchBox", "EmptyState", "NavRail", "FadeStackedWidget",
    "Divider", "KeyValueList", "ShortcutOverlay",
]
