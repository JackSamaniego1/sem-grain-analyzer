"""v3.1.1 user bug fixes (Projects browser + Add-images wizard).

1. Double-clicking a lot (tile, tree row, or the lot's own record tile in
   the lot view) OPENS the lot in the browser and shows its images -- it
   never loads it into the Analyzer.  Loading stays on the lot's right-click
   menu and the "Open Lot" button inside the lot view.
2. The Acquisition details form on the wizard's images step never squeezes
   its rows on top of each other: every field keeps its minimum height, no
   two fields overlap, and the step scrolls instead (1 and 20 images, small
   dialog).  Offscreen; everything lives in tmp_path."""
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from tests.test_ui_file_management import _images, _lot, _open, env, page  # noqa: E402,F401
from tests.ui_shell_helpers import mosaic_png  # noqa: E402


# ====================================================================== 1. lot double-click
def _watch(page):
    hits = {"open_session": [], "load": [], "image": []}
    page.open_session_requested.connect(hits["open_session"].append)
    page.load_requested.connect(hits["load"].append)
    page.open_image_requested.connect(lambda *a: hits["image"].append(a))
    return hits


def _analyzer_hits(hits) -> int:
    return len(hits["open_session"]) + len(hits["load"]) + len(hits["image"])


def test_double_click_lot_tile_opens_lot_view_not_analyzer(page, env, qtbot):
    a = _lot(env, "A", n=2)
    sample = a.parent
    cards = _open(page, qtbot, "sample", sample, 1)
    tile = cards[0]
    assert Path(tile.item["path"]) == a
    hits = _watch(page)
    qtbot.mouseDClick(tile, Qt.LeftButton)
    qtbot.mouseRelease(tile, Qt.LeftButton)
    qtbot.waitUntil(lambda: page._node is not None and page._node.path == a
                    and not page.is_loading() and len(page.cards()) == 3, timeout=15000)
    assert page._node.kind == "lot"
    assert len(_images(page)) == 2                     # the lot's images are shown
    assert _analyzer_hits(hits) == 0
    assert "Double-click to open this Lot and see its images" in tile.toolTip()


def test_enter_on_lot_tile_also_only_opens_the_lot(page, env, qtbot):
    a = _lot(env, "A", n=2)
    tile = _open(page, qtbot, "sample", a.parent, 1)[0]
    hits = _watch(page)
    tile.setFocus()
    qtbot.keyClick(tile, Qt.Key_Return)
    qtbot.waitUntil(lambda: page._node.path == a and not page.is_loading(), timeout=15000)
    assert _analyzer_hits(hits) == 0


def test_double_click_record_tile_in_lot_view_does_not_load(page, env, qtbot):
    a = _lot(env, "A", n=2)
    cards = _open(page, qtbot, "lot", a, 3)
    record = [c for c in cards if c.item.get("record")][0]
    hits = _watch(page)
    page._card_open(record)
    qtbot.wait(50)
    assert page._node.kind == "lot" and page._node.path == a
    assert _analyzer_hits(hits) == 0


def test_double_click_lot_in_tree_opens_lot_not_analyzer(page, env, qtbot):
    from ui.app_state import NodeRef
    from ui.pages.projects_page import PATH_ROLE
    a = _lot(env, "A", n=2)
    _open(page, qtbot, "sample", a.parent, 1)
    page._select_tree_path(a)
    idx = page.tree.currentIndex()
    assert idx.isValid() and Path(idx.data(PATH_ROLE)) == a
    page.state.set_node(NodeRef("sample", a.parent))          # look at the sample
    hits = _watch(page)
    page.tree.doubleClicked.emit(idx)
    qtbot.waitUntil(lambda: page._node.path == a and not page.is_loading(), timeout=15000)
    assert page._node.kind == "lot"
    assert _analyzer_hits(hits) == 0


def test_context_menu_and_lot_view_button_still_load_into_analyzer(page, env, qtbot):
    from ui.app_state import NodeRef
    a = _lot(env, "A", n=2)
    tile = _open(page, qtbot, "sample", a.parent, 1)[0]
    hits = _watch(page)
    acts = {x[0]: x[2] for x in page.card_menu_actions(tile) if x}
    acts["Open Lot"]()                                    # right-click > Open Lot
    assert hits["open_session"] == [a]
    acts["Load this lot's images into analyzer"]()        # right-click > Load …
    assert hits["load"] == [[a]]
    # the button inside the opened lot view
    page.state.set_node(NodeRef("lot", a))
    qtbot.waitUntil(lambda: page._node.path == a and not page.is_loading(), timeout=15000)
    assert page.btn_primary.text() == "Open Lot"
    qtbot.mouseClick(page.btn_primary, Qt.LeftButton)
    assert hits["open_session"] == [a, a]


# ====================================================================== 2. acquisition form
@pytest.fixture
def wiz_env(tmp_path, monkeypatch, qapp):
    from data.models import AppSettings
    from data.settings import save_settings
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield tmp_path
    set_reduced_motion(False)


def _fields(wiz):
    return [wiz.f_operator, wiz.f_instrument, wiz.f_mag, wiz.f_kv, wiz.f_wd, wiz.f_notes]


def _min_h(w) -> int:
    # an explicit minimum (QSS min-height / fixed height) wins over the hint,
    # exactly as Qt's layouts treat it
    return w.minimumHeight() if w.minimumHeight() > 0 else w.minimumSizeHint().height()


@pytest.mark.parametrize("n_images", [1, 20])
def test_acquisition_fields_never_squeezed_or_overlapping(wiz_env, qtbot, n_images):
    from PySide6.QtCore import QPoint, QRect
    from ui.app_state import AppState
    from ui.dialogs.new_session_wizard import NewSessionWizard
    tmp = wiz_env
    imgs = [mosaic_png(tmp / f"SEM_{i:04d}.png", seed=3 + i) for i in range(n_images)]
    state = AppState()
    assert state.profile.images_location == "lot"
    wiz = NewSessionWizard(state, images=imgs)
    qtbot.addWidget(wiz)
    wiz.show()
    wiz.resize(wiz.minimumSize())                      # as small as the dialog allows
    wiz._go(len(wiz.steps_def) - 1)                    # the images step
    wiz.acq.set_expanded(True, animate=False)
    qtbot.wait(350)
    assert wiz.img_list.count() == n_images
    host = wiz.f_operator.parentWidget()
    rects = []
    for f in _fields(wiz):
        assert f.isVisible()
        assert f.height() >= _min_h(f), (f, f.height(), _min_h(f))
        assert f.width() >= 120
        lab = host.layout().labelForField(f)
        assert lab is not None and lab.isVisible()
        assert lab.height() >= lab.minimumSizeHint().height()
        assert lab.width() >= lab.minimumSizeHint().width()           # not clipped
        rects.append(QRect(f.mapTo(host, QPoint(0, 0)), f.size()))
    for i, r in enumerate(rects):
        for s in rects[i + 1:]:
            assert not r.intersects(s), (r, s)
    # the list keeps a usable height; the step scrolls instead of squeezing
    assert wiz.img_list.height() >= wiz.img_list.minimumHeight() >= 80
    sa = wiz.page_scrolls[-1]
    assert sa.widget().height() >= sa.widget().minimumSizeHint().height()


def test_wizard_minimum_fits_a_200_percent_screen(wiz_env, qtbot):
    """1920x1080 at 200 % is 960x540 logical: the dialog never demands more
    than the screen has (its steps scroll instead)."""
    from ui.app_state import AppState
    from ui.dialogs.new_session_wizard import NewSessionWizard
    wiz = NewSessionWizard(AppState())
    qtbot.addWidget(wiz)
    wiz.show()
    avail = wiz.screen().availableGeometry()
    assert wiz.minimumHeight() <= max(400, avail.height() - 60)
    assert wiz.minimumWidth() <= max(480, avail.width() - 40)
    assert len(wiz.page_scrolls) == len(wiz.steps_def)
