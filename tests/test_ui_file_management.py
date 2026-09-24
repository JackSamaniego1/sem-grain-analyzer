"""UI-09 Projects browser file management: per-card checkbox, Ctrl/Shift
multi-select, selection action bar, context menus, Move to… picker (same
level only), delete → trash → Undo, rename. Offscreen; all in tmp_path."""
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from data.models import AppSettings, ImageEntry, read_json  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.ui_shell_helpers import mosaic_png  # noqa: E402


class _Toasts:
    def __init__(self):
        self.calls = []

    def show_toast(self, *a, **k):
        self.calls.append(a)

    def has(self, title):
        return any(c[0] == title for c in self.calls)

    def last(self, title):
        return [c for c in self.calls if c[0] == title][-1]


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from data.hierarchy import PRESETS, save_profile
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    root.mkdir()
    save_profile(root, PRESETS["job_part_lot"])            # lot mode: images live in the lot
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)


def _lot(root: Path, lot: str, n: int = 3, sample: str = "P-1", project: str = "24-117") -> Path:
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(root)
    try:
        pp = ws.resolve_project(project)
    except FileNotFoundError:
        pp = ws.create_project(project)
    try:
        sp = ws.resolve_sample(pp, sample)
    except FileNotFoundError:
        sp = ws.create_sample(pp, sample)
    lp = ws.create_lot(pp, sp, lot)
    src = root.parent / "src" / f"{project}_{sample}_{lot}"
    src.mkdir(parents=True, exist_ok=True)
    ents = [ImageEntry(source_path=mosaic_png(src / f"field_{i}.png", seed=11 + i))
            for i in range(n)]
    save_session(lp, {}, ents, in_place=True, catalog=Catalog(root))
    return lp


def _names(lp: Path):
    return [e["filename"] for e in read_json(lp / "manifest.json")["images"]]


@pytest.fixture
def page(env, qtbot):
    from ui.app_state import AppState
    from ui.pages.projects_page import ProjectsPage
    st = AppState()
    assert st.root == env
    toasts = _Toasts()
    w = ProjectsPage(st, toasts)
    w.toast_log = toasts
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    yield w
    w.close()


def _open(page, qtbot, kind, path, n_cards):
    from ui.app_state import NodeRef
    page.reload()
    page.state.set_node(NodeRef(kind, path))
    qtbot.waitUntil(lambda: not page.is_loading() and page._node.path == path
                    and len(page.cards()) == n_cards, timeout=15000)
    return page.cards()


def _images(page):
    return [c for c in page.cards() if c.item["kind"] == "image"]


def test_lot_view_lists_images_with_checkbox_and_trash_button(page, env, qtbot):
    a = _lot(env, "A", n=3)
    cards = _open(page, qtbot, "lot", a, 4)
    record = [c for c in cards if c.item.get("record")]
    assert len(record) == 1 and record[0].check is None and record[0].trash_btn is None
    imgs = _images(page)
    assert sorted(c.title_text() for c in imgs) == sorted(_names(a))
    for c in imgs:
        assert c.check is not None and c.check.toolTip()
        assert c.trash_btn is not None and "trash" in c.trash_btn.toolTip()
    assert "3 images" in page.count_lbl.text()
    assert not page.selbar.is_open()


def test_checkbox_ctrl_shift_selection_and_action_bar(page, env, qtbot):
    a = _lot(env, "A", n=4)
    _open(page, qtbot, "lot", a, 5)
    imgs = _images(page)
    imgs[0].check.setChecked(True)                                   # checkbox
    assert page.selbar.is_open() and page.selbar.count() == 1
    assert page.selbar.text() == "1 image selected"
    qtbot.mouseClick(imgs[2], Qt.LeftButton, Qt.ControlModifier)     # Ctrl+click adds
    assert page.selbar.text() == "2 images selected"
    assert imgs[2].is_checked() and imgs[2].is_selected()
    qtbot.mouseClick(imgs[2], Qt.LeftButton, Qt.ControlModifier)     # …and toggles off
    assert page.selbar.count() == 1 and not imgs[2].is_checked()
    qtbot.mouseClick(imgs[1], Qt.LeftButton)                         # plain click: only this
    assert [c.item["path"] for c in page.selected_cards()] == [imgs[1].item["path"]]
    qtbot.mouseClick(imgs[3], Qt.LeftButton, Qt.ShiftModifier)       # Shift: range 1..3
    assert page.selbar.text() == "3 images selected"
    assert "Delete 3" in page.selbar.delete_btn.text()
    assert page.selbar.move_btn.isEnabled()
    page.selbar.clear_btn.click()                                    # Clear
    assert page.selbar.count() == 0 and not page.selbar.is_open()
    assert not any(c.is_checked() for c in imgs)
    page.selbar.select_all_btn.click()
    assert page.selbar.count() == 4                                  # the record is not selectable


def test_context_menu_actions_for_images_and_folders(page, env, qtbot):
    from ui.app_state import NodeRef
    a = _lot(env, "A", n=2)
    _open(page, qtbot, "lot", a, 3)
    img = _images(page)[0]
    texts = [x[0] for x in page.card_menu_actions(img) if x]
    assert texts[0] == "Open Lot"
    assert "Rename…" in texts and "Move to another Lot…" in texts
    assert any(t.startswith("Delete image") for t in texts)
    lot_texts = [x[0] for x in page.menu_actions(NodeRef("lot", a)) if x]
    assert "Open Lot" in lot_texts and "Rename  (F2)" in lot_texts
    assert "Move to another Part Number…" in lot_texts
    assert any(t.startswith("Delete") for t in lot_texts)
    proj = [x[0] for x in page.menu_actions(NodeRef("project", a.parent.parent)) if x]
    assert "Open Job #" in proj and not any(t.startswith("Move to") for t in proj)
    # multi-selection: the menu acts on the whole selection
    page.select_all()
    multi = [x[0] for x in page.card_menu_actions(img) if x]
    assert "Move 2 images to…" in multi and "Delete 2 images  (move to trash)…" in multi
    # "Open" from the menu opens the lot the image belongs to
    opened = []
    page.open_session_requested.connect(opened.append)
    page.clear_selection()
    [x for x in page.card_menu_actions(img) if x][0][2]()
    assert opened == [a]


def test_move_picker_offers_same_level_only_and_undo(page, env, qtbot):
    a = _lot(env, "A", n=3)
    b = _lot(env, "B", n=1)
    c = _lot(env, "C", n=1, sample="P-2", project="25-001")
    _open(page, qtbot, "lot", a, 4)
    imgs = _images(page)
    moving = [imgs[0].item["filename"], imgs[1].item["filename"]]
    imgs[0].check.setChecked(True)
    imgs[1].check.setChecked(True)
    page.selbar.move_btn.click()
    dlg = page.move_dialog
    assert dlg is not None
    assert set(dlg.destinations()) == {b, c}                         # other lots only
    assert "Lot B" in dlg.list.item(0).text() or "Lot B" in dlg.list.item(1).text()
    dlg.filter.setText("25-001")
    assert dlg.visible_destinations() == [c]
    dlg.filter.setText("")
    assert not dlg.btn_move.isEnabled()
    dlg.select(b)
    dlg.btn_move.click()
    qtbot.waitUntil(lambda: len(_names(b)) == 3, timeout=15000)
    assert not set(moving) & set(_names(a)) and len(_names(a)) == 1
    assert "field_0 (2).png" in _names(b)                            # never overwritten
    qtbot.waitUntil(lambda: page.toast_log.has("Moved"), timeout=15000)
    title, body, sev, action, undo = page.toast_log.last("Moved")[:5]
    assert action == "Undo" and "2 images" in body
    undo()
    qtbot.waitUntil(lambda: set(moving) <= set(_names(a)), timeout=15000)
    assert _names(b) == ["field_0.png"]                              # original names restored


def test_move_lot_picker_lists_samples_only(page, env, qtbot):
    a = _lot(env, "A", n=1)
    _lot(env, "B", n=1)
    other = _lot(env, "C", n=1, sample="P-2").parent
    _open(page, qtbot, "sample", a.parent, 2)
    card = [c for c in page.cards() if Path(c.item["path"]) == a][0]
    page.start_move([card.item])
    assert page.move_dialog.destinations() == [other]                 # never lots / projects
    page.move_dialog.select(other)
    page.move_dialog.btn_move.click()
    qtbot.waitUntil(lambda: (other / "A" / "lot.json").exists(), timeout=15000)
    assert not a.exists()


def test_delete_selection_goes_to_trash_and_undo_restores(page, env, qtbot):
    a = _lot(env, "A", n=3)
    before = _names(a)
    _open(page, qtbot, "lot", a, 4)
    imgs = _images(page)
    victims = [imgs[0].item["filename"], imgs[2].item["filename"]]
    imgs[0].check.setChecked(True)
    imgs[2].check.setChecked(True)
    page.selbar.delete_btn.click()
    assert page.confirm.isVisibleTo(page)
    assert page.confirm.title.text() == "Delete 2 images?"
    assert ".trash" in page.confirm.body.text() and "Undo" in page.confirm.body.text()
    page.confirm.ok_btn.click()
    qtbot.waitUntil(lambda: not set(victims) & set(_names(a)), timeout=15000)
    assert (env / ".trash").exists()
    qtbot.waitUntil(lambda: len(page.cards()) == 2 and not page.is_loading(), timeout=15000)
    title, body, sev, action, undo = page.toast_log.last("Moved to trash")[:5]
    assert action == "Undo"
    undo()
    qtbot.waitUntil(lambda: sorted(_names(a)) == sorted(before), timeout=15000)
    qtbot.waitUntil(lambda: len(page.cards()) == 4, timeout=15000)


def test_folder_card_trash_button_deletes_and_undo_restores(page, env, qtbot):
    a = _lot(env, "A", n=1)
    _lot(env, "B", n=1)
    _open(page, qtbot, "sample", a.parent, 2)
    card = [c for c in page.cards() if Path(c.item["path"]) == a][0]
    card.trash_btn.click()
    assert page.confirm.isVisibleTo(page) and "Lot A" in page.confirm.title.text()
    page.confirm.ok_btn.click()
    qtbot.waitUntil(lambda: page.toast_log.has("Moved to trash"), timeout=15000)
    assert not a.exists()
    page.toast_log.last("Moved to trash")[4]()
    qtbot.waitUntil(lambda: (a / "lot.json").exists(), timeout=15000)


def test_rename_image_from_card_menu(page, env, qtbot):
    a = _lot(env, "A", n=2)
    _open(page, qtbot, "lot", a, 3)
    img = _images(page)[0]
    old = img.item["filename"]
    acts = {x[0]: x[2] for x in page.card_menu_actions(img) if x}
    acts["Rename…"]()
    dlg = page.rename_dialog
    assert dlg is not None and dlg.edit.text() == Path(old).stem
    dlg.edit.setText("Transverse 01")
    dlg.btn_ok.click()
    assert "Transverse 01" + Path(old).suffix in _names(a) and old not in _names(a)


def test_selection_bar_move_disabled_for_projects(page, env, qtbot):
    _lot(env, "A", n=1)
    _lot(env, "B", n=1, project="25-002")
    _open(page, qtbot, "workspace", env, 2)
    page.select_all()
    assert page.selbar.count() == 2 and not page.selbar.move_btn.isEnabled()
    assert "cannot be moved" in page.selbar.move_btn.toolTip()
