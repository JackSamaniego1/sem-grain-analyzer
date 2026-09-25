"""FIX-08 (calibration-check chip + tree verdict pills), FIX-09 (layout at
narrow widths: wrapping toolbars, 2x2 stat cards, wrapping card titles),
FIX-15 (lot field table: the exclusion reason is readable in full).

Offscreen; every workspace and settings file lives in tmp_path."""
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings, read_json  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.test_ui_lot_result import _Toasts, _lot, _show_lot  # noqa: E402
from tests.ui_shell_helpers import make_session  # noqa: E402

LONG_REASON = ("Scratch across the lower third of the field and out-of-focus region "
               "near the SEM info bar")


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)


def _shell(qtbot, width=1400):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    w = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(w)
    w.setMinimumWidth(900)
    w.resize(width, 900)
    w.show()
    return w


def _open(shell, qtbot, path):
    shell.open_session(path)
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    qtbot.wait(50)


def _overlaps(widgets):
    """Pairs of visible widgets whose rectangles (in window coords) intersect."""
    rects = []
    for w in widgets:
        if w.isVisible():
            tl = w.mapTo(w.window(), w.rect().topLeft())
            rects.append((w, w.rect().translated(tl)))
    return [(a, b) for i, (a, ra) in enumerate(rects) for b, rb in rects[i + 1:]
            if ra.intersects(rb)]


# ====================================================================== FIX-09 helpers
def test_wrap_elide_lines_and_ellipsis(qapp):
    from PySide6.QtGui import QFont, QFontMetrics
    from ui.widgets.layout import wrap_elide
    fm = QFontMetrics(QFont())
    width = fm.horizontalAdvance("Session A long") + 2
    assert wrap_elide("Session A", fm, 1000) == ["Session A"]
    lines = wrap_elide("Session A long name that goes on and on and on", fm, width, 2)
    assert len(lines) == 2 and lines[0] == "Session A long"
    assert lines[1].endswith("…") and fm.horizontalAdvance(lines[1]) <= width
    # an unbreakable name is broken by character, never clipped mid-glyph
    word = "L-44C-RETEST-SUPPLEMENTAL-HEAT-TREATMENT"
    parts = wrap_elide(word, fm, fm.horizontalAdvance("L-44C-RET"), 3)
    assert len(parts) == 3 and parts[0].startswith("L-44C")
    assert all(fm.horizontalAdvance(p) <= fm.horizontalAdvance("L-44C-RET") for p in parts)
    assert parts[-1].endswith("…")
    assert wrap_elide("", fm, 100) == [""]


def test_wrap_label_keeps_full_text_in_tooltip(qtbot):
    from ui.widgets.layout import WrapLabel
    lab = WrapLabel("Session A with an extremely long descriptive name", "h3", max_lines=2)
    qtbot.addWidget(lab)
    lab.resize(120, 60)
    lab.show()
    assert len(lab.shown_lines()) == 2
    assert lab.toolTip() == lab.full_text() == "Session A with an extremely long descriptive name"
    assert lab.shown_lines()[0].startswith("Session A")    # not "Ses…n A"
    lab.resize(900, 30)
    assert lab.shown_lines() == ["Session A with an extremely long descriptive name"]


def test_responsive_toolbar_wraps_without_overlap(qtbot):
    from PySide6.QtWidgets import QLabel, QPushButton
    from ui.widgets.layout import ResponsiveToolbar, group
    groups = [group(*[QPushButton(f"Button {g}{i}") for i in range(3)]) for g in range(3)]
    tb = ResponsiveToolbar(QLabel("SEM_0001"), groups)
    qtbot.addWidget(tb)
    tb.resize(2000, 40)
    tb.show()
    qtbot.wait(10)
    assert tb.row_count() == 1
    one_row_h = tb.height()
    tb.resize(420, 40)
    qtbot.wait(10)
    assert tb.row_count() >= 2 and tb.height() > one_row_h
    assert not _overlaps([tb.lead] + groups)
    for g in groups:                         # nothing pushed out of the bar
        assert g.geometry().right() <= tb.width() and g.geometry().left() >= 0
    assert tb.minimumSizeHint().width() <= max(g.sizeHint().width() for g in groups) + 150


# ====================================================================== FIX-09 pages
def test_review_and_analyze_toolbars_never_overlap(env, qtbot):
    path = make_session(env, 1)
    shell = _shell(qtbot, 1400)
    _open(shell, qtbot, path)
    for width in (1100, 1400, 1920):
        shell.resize(width, 900)
        for key, page in (("review", shell.review), ("analyze", shell.analyze)):
            shell.go(key)
            qtbot.wait(30)
            tb = page.toolbar
            assert not _overlaps([tb.lead] + tb.groups), (key, width)
            assert page.view_seg.isVisible()
            for g in tb.groups:
                assert g.geometry().right() <= tb.width(), (key, width)
    shell.resize(1920, 900)
    shell.go("review")
    qtbot.wait(30)
    assert shell.review.toolbar.row_count() == 1     # wide: one row, as before
    shell.close()


def test_analyze_stat_cards_reflow_instead_of_clipping(env, qtbot):
    path = make_session(env, 1)
    shell = _shell(qtbot, 1100)
    _open(shell, qtbot, path)
    shell.go("analyze")
    qtbot.wait(30)
    grid = shell.analyze.stats_grid
    assert grid.columns() == 2                        # 2 x 2 in a narrow window
    for c in (shell.analyze.st_images, shell.analyze.st_diam):
        lab = c._label
        assert lab.width() >= lab.sizeHint().width(), "overline label clipped"
    shell.resize(1920, 900)
    qtbot.wait(30)
    assert grid.columns() == 4
    shell.close()


def test_metric_card_redraws_number_after_text(qtbot):
    from ui.pages.common import MetricCard
    c = MetricCard("Mean ASTM grain size", 0, "G", 1)
    qtbot.addWidget(c)
    c.set_metric(7.3, "G", 1, animate=False)
    c.set_text("24 Sep 2026")
    c.set_metric(7.3, "G", 1, animate=False)        # same value as before the text
    assert c.displayed_text() == "7.3"


def test_projects_card_title_wraps_and_badge_moves(env, qtbot):
    from ui.pages.projects_page import NodeCard
    item = {"kind": "session", "path": env, "meta": {"label": "Session A second re-test"},
            "status": ("Partly analysed", "warning"), "n_images": 2, "thumbs": []}
    card = NodeCard(item)
    qtbot.addWidget(card)
    card.resize(260, 260)
    card.show()
    qtbot.wait(10)
    full = card.title_text()
    assert card.title_lbl.toolTip() == full
    shown = " ".join(card.title_lbl.shown_lines())
    assert shown.startswith("Session A")               # wraps; no "Ses…n A"
    assert card.status_badge is not None and card.status_badge.text() == "Partly analysed"
    assert card.status_badge.isVisible()


# ====================================================================== FIX-15
@pytest.fixture
def page(env, qtbot):
    from ui.app_state import AppState
    from ui.pages.projects_page import ProjectsPage
    w = ProjectsPage(AppState(), _Toasts())
    qtbot.addWidget(w)
    w.resize(1400, 1000)
    w.show()
    yield w
    w.close()


def _exclude(ref_path: Path, reason: str) -> None:
    from data.models import write_json_atomic
    mp = Path(ref_path) / "manifest.json"
    m = read_json(mp)
    m["images"][1]["included"] = False
    m["images"][1]["exclusion_reason"] = reason
    write_json_atomic(mp, m)


def test_field_table_note_shows_full_exclusion_reason(page, env, qtbot):
    from ui.pages.lot_results import IMAGE_COL, NOTE_COL, NOTE_MIN, ROW_H, SESSION_COL
    lot, refs = _lot(env, [("A", [7.3, 7.4, 7.5])])
    _exclude(refs[0].path, LONG_REASON)
    lp = _show_lot(page, qtbot, lot)
    t = lp.table
    qtbot.waitUntil(lambda: t.rowCount() == 3, timeout=10000)
    qtbot.wait(30)
    row = next(r for r, f in enumerate(t.fields()) if not f.included)
    it = t.item(row, NOTE_COL)
    assert LONG_REASON in it.text() and LONG_REASON in it.toolTip()
    assert t.wordWrap()
    assert t.isColumnHidden(SESSION_COL)             # one session: no repeated column
    assert t.columnWidth(NOTE_COL) >= NOTE_MIN
    # the row is tall enough for the wrapped reason
    need = t.sizeHintForRow(row)
    assert t.rowHeight(row) >= need and t.rowHeight(row) >= ROW_H
    from PySide6.QtCore import QRect, Qt
    fm = t.fontMetrics()
    text_h = fm.boundingRect(QRect(0, 0, t.columnWidth(NOTE_COL) - 12, 10000),
                             int(Qt.TextWordWrap), it.text()).height()
    assert t.rowHeight(row) >= text_h                 # the whole wrapped reason fits
    # every row fits without an inner vertical scrollbar
    assert not t.verticalScrollBar().isVisible()
    # narrow: Note keeps a readable width (sideways scroll) instead of 1 word per line
    lp.resize(460, lp.height())
    t.resize(440, t.height())
    qtbot.wait(30)
    assert t.columnWidth(NOTE_COL) >= NOTE_MIN and t.columnWidth(IMAGE_COL) >= 100


def test_field_table_keeps_session_column_for_two_sessions(page, env, qtbot):
    from ui.pages.lot_results import SESSION_COL
    lot, _refs = _lot(env, [("A", [7.3, 7.4]), ("B", [7.5, 7.6])])
    lp = _show_lot(page, qtbot, lot)
    qtbot.waitUntil(lambda: lp.table.rowCount() == 4, timeout=10000)
    assert not lp.table.isColumnHidden(SESSION_COL)


# ====================================================================== FIX-08
def _record_check(root, instrument="SEM-1", mag="500×", measured=10.02):
    from data.cal_records import CalibrationStandard, CalibrationStore, build_check
    store = CalibrationStore(root, enabled=True)
    std = store.add_standard(CalibrationStandard(name="Grid 10 µm", certified_pitch_um=10.0))
    return store.record_check(build_check(standard=std, instrument=instrument,
                                          magnification=mag, measured_pitch_um=measured,
                                          operator="Tester"))


def _set_acquisition(session_path, instrument="SEM-1", mag="500×"):
    from data.session_io import update_session
    update_session(session_path, meta_updates={"instrument": instrument, "magnification": mag})


def test_status_bar_cal_check_chip_hidden_when_unused(env, qtbot):
    path = make_session(env, 1)
    _set_acquisition(path)
    shell = _shell(qtbot)
    assert shell.chip_calcheck.parent() is shell.statusBar() or \
        shell.statusBar().isAncestorOf(shell.chip_calcheck)
    _open(shell, qtbot, path)
    assert shell.chip_calcheck.isHidden()
    m = shell.state.session.meta
    assert m.calibration_status == "" and m.calibration_check_id is None
    assert not shell.state.is_dirty()                   # feature off: nothing written
    shell.close()


def test_status_bar_cal_check_chip_reflects_session(env, qtbot):
    path = make_session(env, 1)
    _set_acquisition(path)
    check = _record_check(env)
    s = AppSettings(workspace_root=str(env), operator="Tester", theme="dark",
                    calibration_verification_enabled=True)
    save_settings(s)
    shell = _shell(qtbot)
    _open(shell, qtbot, path)
    chip = shell.chip_calcheck
    qtbot.waitUntil(lambda: chip.isVisible(), timeout=5000)
    assert chip.kind() == "success" and "SEM-1" in chip.text()
    assert "scale is verified" in chip.toolTip()
    m = shell.state.session.meta
    assert m.calibration_status == "verified" and m.calibration_check_id == check.id
    # apply_to_session's stamp is saved with the session
    shell.state.save_now()
    shell.state.flush()
    saved = read_json(Path(path) / "manifest.json")
    assert saved["calibration_check_id"] == check.id
    assert saved["calibration_status"] == "verified"
    # a different magnification: instrument OK but this session not verified
    shell.state.session.meta.magnification = "5000×"
    shell._update_calcheck_chip()
    assert chip.kind() == "warning" and "not verified" in chip.toolTip()
    assert shell.state.session.meta.calibration_status == "not verified"
    shell.state.close_session()
    qtbot.wait(10)
    assert chip.isHidden()
    shell.close()


def test_tree_shows_verdict_pill_only_with_spec(env, qtbot):
    from data.specs import Rule, Spec
    from ui.dialogs.spec_editor_dialog import save_specs
    from ui.pages.projects_page import VERDICT_ROLE, scan_tree

    lot, _ = _lot(env, [("A", [7.3, 7.35, 7.4, 7.45, 7.5])])

    def lot_nodes():
        return [ln for p in scan_tree(str(env)) for s in p["children"] for ln in s["children"]]

    assert [n["verdict"] for n in lot_nodes()] == [""]           # no spec: no pill
    save_specs(lot.parent.parent, [Spec(id="s1", name="Alloy 718", decision_rule="simple",
                                        rules=[Rule("G_mean", 6.0, 9.0)])])
    assert [n["verdict"] for n in lot_nodes()] == ["pass"]
    # the tree item carries it (drawn by the delegate) and says so in its tooltip
    from ui.app_state import AppState
    from ui.pages.projects_page import ProjectsPage
    page = ProjectsPage(AppState(), _Toasts())
    qtbot.addWidget(page)
    page.resize(1400, 900)
    page.show()
    page.reload()
    qtbot.waitUntil(lambda: page._find_item(lot) is not None, timeout=10000)
    it = page._find_item(lot)
    assert it.data(VERDICT_ROLE) == "pass" and "PASS" in it.toolTip()
    page.tree.expandAll()
    page.tree.viewport().grab()                 # delegate paints without error
    page.close()


def test_tree_no_pill_for_empty_lot_with_spec(env):
    from data.specs import Rule, Spec
    from data.workspace import Workspace
    from ui.dialogs.spec_editor_dialog import save_specs
    from ui.pages.projects_page import scan_tree
    ws = Workspace(env)
    pp = ws.create_project("P1")
    sp = ws.create_sample(pp, "S1")
    ws.create_lot(pp, sp, "L-empty")
    save_specs(pp, [Spec(id="s1", name="X", rules=[Rule("G_mean", 6.0, 9.0)])])
    lots = [ln for p in scan_tree(str(env)) for s in p["children"] for ln in s["children"]]
    assert [n["verdict"] for n in lots] == [""]
