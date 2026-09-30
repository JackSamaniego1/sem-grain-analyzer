"""UPDATE 4 item 15 (UI): Lot Summary preview in the report editor."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_report_lot_summary import _img, _job, _lots, _model  # noqa: E402


@pytest.fixture
def themed(qapp):
    from ui.design.theme import apply_theme
    apply_theme(qapp, "dark")
    yield qapp
    apply_theme(qapp, "dark")


def _preview(qtbot, model):
    from ui.pages.report_preview import make_preview
    page = SimpleNamespace(model=model, state=SimpleNamespace(profile=None))
    w = make_preview(page, ("section", "lot_summary"))
    qtbot.addWidget(w)
    w.resize(1100, 900)
    w.show()
    return w


def test_powerpoint_target_no_longer_says_not_included():
    from ui.pages.report_builder import SECTION_TARGETS
    xl, pp = SECTION_TARGETS["lot_summary"]
    assert "Not included" not in pp and "slide" in pp.lower()


def test_table_rows_and_charts_match_the_data_api(themed, qtbot):
    from reports.lot_summary import NUMERIC_KEYS, id_cells, lot_summary_data, table_headers
    from ui.pages.report_preview import LotSummaryPreview
    from ui.pages.report_widgets import LotTrendChart, fmt_num
    m = _model(_job(parts=2, lots=3))
    w = _preview(qtbot, m)
    assert isinstance(w, LotSummaryPreview)
    assert "NOT INCLUDED" not in w.where.text()
    d = lot_summary_data(m)
    t = w.table
    assert t.rowCount() == len(d["rows"]) and t.columnCount() == len(table_headers(d))
    assert [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())] == table_headers(d)
    assert w.row_kinds() == [r["kind"] for r in d["rows"]]
    assert w.row_kinds().count("part") == 2 and w.row_kinds()[-1] == "total"
    last = d["rows"][-1]
    assert t.item(len(d["rows"]) - 1, 0).text() == id_cells(last)[0] == "JOB TOTAL"
    assert t.item(len(d["rows"]) - 1, 0).font().bold()
    assert not t.item(0, 0).font().bold()                          # plain lot row
    gi = 2 + NUMERIC_KEYS.index("n_grains")
    assert t.item(len(d["rows"]) - 1, gi).text() == fmt_num(last["n_grains"], "#,##0")
    # total row styled distinctly from lot and part rows
    bg = lambda r: t.item(r, 0).background().color().name()        # noqa: E731
    kinds = w.row_kinds()
    assert bg(kinds.index("total")) != bg(kinds.index("part")) != bg(0)
    charts = w.findChildren(LotTrendChart)
    assert [c.chart["id"] for c in charts] == [c["id"] for c in d["charts"]]
    ch = charts[0]
    assert ch.chart["trend"] is not None and "(µm)" in ch.chart["y_title"]
    assert not w.empty.isVisible() and w.table_card.isVisible()
    img = ch.grab().toImage()
    assert img.width() > 100 and img.height() > 100


def test_chart_hover_tooltip_names_the_lot(themed, qtbot):
    from PySide6.QtCore import QPoint
    from ui.pages.report_widgets import LotTrendChart
    w = _preview(qtbot, _model(_lots(3)))
    ch = w.findChildren(LotTrendChart)[0]
    r = ch._plot()
    x = int(r.left() + r.width() / 6)                              # first of 3 bars
    assert ch.index_at(x) == 0
    qtbot.mouseMove(ch, QPoint(x, int(r.center().y())))
    qtbot.waitUntil(lambda: "Lot-1" in ch.toolTip(), timeout=2000)
    assert "Lot value" in ch.toolTip()


def test_footnotes_shown_under_the_table(themed, qtbot, monkeypatch):
    import reports.lot_summary as ls
    m = _model(_job(parts=2, lots=3))
    real = ls.lot_summary_data(m)
    w = _preview(qtbot, m)
    notes = real.get("footnotes") or []
    assert w.footnotes.isVisible() == bool(notes)
    for n in notes:
        assert n in w.footnotes.text()
    # explicit footnotes -> shown as small muted text inside the table card
    monkeypatch.setattr(ls, "lot_summary_data",
                        lambda model: dict(real, footnotes=["* First note.", "* Second."]))
    w.refresh()
    assert w.footnotes.isVisible() and w.footnotes.parent() is not None
    assert w.footnotes.text() == "* First note.\n* Second."
    assert w.table_card.isAncestorOf(w.footnotes)
    # none -> hidden
    monkeypatch.setattr(ls, "lot_summary_data", lambda model: dict(real, footnotes=[]))
    w.refresh()
    assert not w.footnotes.isVisible() and w.footnotes.text() == ""


def test_empty_state_without_lots(themed, qtbot):
    imgs = [_img("a", "", "", [1.0, 2.0, 3.0]), _img("b", "", "", [2.0, 2.5])]
    w = _preview(qtbot, _model(imgs))
    assert w.empty.isVisible() and not w.table_card.isVisible()
    assert not w.chart_cards


def test_light_theme_restyles_rows(themed, qtbot):
    from ui.design.theme import apply_theme
    w = _preview(qtbot, _model(_job(parts=1, lots=2)))
    row = w.row_kinds().index("total")
    dark = w.table.item(row, 0).background().color().name()
    apply_theme(themed, "light")
    light = w.table.item(row, 0).background().color().name()
    assert dark != light
