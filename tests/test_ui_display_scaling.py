"""Round 3c: display scaling.  At the logical window sizes of a 2560x1440
screen at 200 % (1280x720) and a 1920x1080 screen at 200 % (960x540):

* every wizard step button on the Analyze page lies fully inside the
  sidebar's visible width (the sidebar only ever scrolls vertically), and
* no visible button / field on the Analyze, Review, Reports or Projects page
  is cut off horizontally by the panel (scroll area) that holds it.

Offscreen; logical sizes are what the layouts see at any QT_SCALE_FACTOR."""
import pytest

pytest.importorskip("pytestqt")

from tests.test_ui_reports_designer import _analyse_all, _build, _idle, env  # noqa: E402,F401
from tests.ui_shell_helpers import make_session  # noqa: E402

SIZES = [(1280, 720), (960, 540)]
WIZARD_BUTTONS = ("scan_all", "scan_current", "scan_edit", "scale_all", "scale_current",
                  "scale_edit", "run_all", "run_current")


@pytest.fixture
def shell(env, qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    path = make_session(env, 3, label="Run", black=True)
    w = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(w)
    w.resize(1500, 950)
    w.show()
    w.open_session(path)
    qtbot.waitUntil(lambda: w.state.session is not None and not w.state.is_loading(),
                    timeout=20000)
    yield w
    w.close()


def _hclipped(page):
    """Visible buttons / inputs whose horizontal extent leaves the viewport
    of the scroll area holding them (or the page)."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import (
        QAbstractButton, QAbstractScrollArea, QAbstractSpinBox, QComboBox, QLineEdit,
    )
    bad = []
    for cls in (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox):
        for w in page.findChildren(cls):
            if not w.isVisible() or w.width() <= 0:
                continue
            # the nearest scroll area viewport (not the widget's own)
            box, p = page, w.parentWidget()
            while p is not None and p is not page:
                if isinstance(p, QAbstractScrollArea) and p is not w:
                    box = p.viewport()
                    break
                p = p.parentWidget()
            r = QRect(w.mapTo(page, QPoint(0, 0)), w.size())
            b = QRect(box.mapTo(page, QPoint(0, 0)), box.size()) if box is not page \
                else page.rect()
            if r.left() < b.left() - 1 or r.right() > b.right() + 1:
                bad.append((type(w).__name__, w.objectName(),
                            (w.text() if hasattr(w, "text") and callable(w.text) else "")[:24],
                            r.left(), r.right(), b.left(), b.right()))
    return bad


@pytest.mark.parametrize("size", SIZES)
def test_wizard_step_buttons_inside_sidebar(shell, qtbot, size):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractButton
    shell.resize(*size)
    shell.go("analyze")
    a = shell.analyze
    a.sync_wizard()
    qtbot.wait(150)
    sa = a.side_scroll
    vp, inner = sa.viewport(), sa.widget()
    assert inner.width() <= vp.width()                       # never scrolls sideways
    seen = 0
    for name in WIZARD_BUTTONS:
        b = inner.findChild(QAbstractButton, name)
        assert b is not None, name
        if not b.isVisibleTo(inner):
            continue
        seen += 1
        tl = b.mapTo(inner, QPoint(0, 0))
        assert tl.x() >= 0 and tl.x() + b.width() <= vp.width(), (name, size, tl.x(),
                                                                  b.width(), vp.width())
        assert tl.y() >= 0 and tl.y() + b.height() <= inner.height(), (name, size)
        sa.ensureWidgetVisible(b)                            # reachable by scrolling
        qtbot.wait(10)
        y = b.mapTo(vp, QPoint(0, 0)).y()
        assert 0 <= y and y + b.height() <= vp.height() + 1, (name, size)
    assert seen >= 6


@pytest.mark.parametrize("size", SIZES)
def test_pages_not_clipped_horizontally(shell, qtbot, size):
    _analyse_all(shell, qtbot)
    rp = _build(shell, qtbot)
    _idle(shell, qtbot)
    shell.resize(*size)
    for key in ("analyze", "review", "reports", "projects"):
        shell.go(key)
        qtbot.wait(200)
        page = getattr(shell, key)
        assert _hclipped(page) == [], (key, size)
    assert rp.inspector.width() <= rp.inspector_scroll.viewport().width()
