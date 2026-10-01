"""Round 3: the Reports inspector column is never clipped horizontally —
at any window width every visible button / line edit (Document fields and
the Exports rows' open / show-in-folder buttons) lies inside the column's
visible viewport; long file names elide instead of widening the column."""
import os

import pytest

pytest.importorskip("pytestqt")

from tests.test_ui_reports_designer import _build, _export, _idle, analysed, env  # noqa: E402,F401

SHOT_DIR = os.environ.get("GA_SHOT_DIR", "")


def _clipped(rp):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit
    vp = rp.inspector_scroll.viewport()
    vis = QRect(0, 0, vp.width(), 10 ** 6)
    bad = []
    for cls in (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox):
        for w in rp.inspector.findChildren(cls):
            if not w.isVisibleTo(rp.inspector) or w.width() == 0:
                continue
            top_left = w.mapTo(vp, QPoint(0, 0))
            r = QRect(top_left, w.size())
            if r.left() < 0 or r.right() > vis.right():
                bad.append((type(w).__name__, w.toolTip()[:30], r.left(), r.right(), vp.width()))
    return bad


def test_inspector_never_clipped_horizontally(analysed, qtbot):
    shell, _ = analysed
    rp = _build(shell, qtbot)
    _idle(shell, qtbot)
    _export(rp, qtbot, ["xlsx"])          # long auto file name in the Exports section
    _idle(shell, qtbot)
    assert rp.inspector.sec_exports.findChildren(type(rp.btn_more))   # rows present
    for w, h in [(1100, 700), (1366, 768), (1600, 900), (1920, 1080), (1240, 720)]:
        shell.resize(w, h)
        qtbot.wait(120)
        assert _clipped(rp) == [], (w, h)
        assert rp.inspector.width() <= rp.inspector_scroll.viewport().width()
        if SHOT_DIR and (w, h) == (1366, 768):
            shell.grab().save(os.path.join(SHOT_DIR, "reports_1366x768.png"))
