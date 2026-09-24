"""
Design system for SEM Grain Analyzer v3.

- ``ui.design.tokens``  pure-Python tokens (colours, spacing, type, motion)
- ``ui.design.theme``   ``apply_theme(app, mode)``, ``current_tokens()``, live theme signal
- ``ui.design.icons``   semantic icon set (qtawesome, bundled fonts, offline)

This package init stays Qt-free so ``from ui.design.tokens import ...`` works
without a GUI toolkit.
"""
