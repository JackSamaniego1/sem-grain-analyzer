"""UPDATE 4 item 1: New Lot / New Part add another entry box per click;
one Create makes every filled entry and the user stays on the page."""
import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings  # noqa: E402
from data.settings import save_settings  # noqa: E402


@pytest.fixture
def shell(tmp_path, monkeypatch, qapp, qtbot):
    from ui.design.theme import apply_theme, set_reduced_motion
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="T", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    state = AppState()
    w = AppShell(state, probe_device=False)
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    yield w
    w.close()
    set_reduced_motion(False)


def _open(shell, qtbot, kind):
    """Job + part in the workspace; return the page with the New <kind> form open."""
    from ui.app_state import NodeRef
    ws = shell.state.workspace
    proj = ws.create_project("Job A")
    samp = ws.create_sample(proj, "P1")
    pg = shell.projects
    pg.reload()
    node = NodeRef("project", proj) if kind == "sample" else NodeRef("sample", samp)
    shell.state.set_node(node)
    qtbot.waitUntil(lambda: not pg.is_loading(), timeout=5000)
    pg.start_create(kind)
    return pg, proj, samp


def _fill(pg, i, text):
    key = pg.form._fields[0].key
    pg.form._rows[i]["edits"][key].setText(text)


def _lots(shell, proj, samp):
    return sorted(x.lot_number for x in shell.state.workspace.list_lots(proj, samp))


def test_each_click_adds_a_box_and_keeps_text(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "lot")
    assert pg.form.row_count() == 1
    _fill(pg, 0, "L1")
    pg.start_create("lot")
    _fill(pg, 1, "L2")
    pg.start_create("lot")
    assert pg.form.row_count() == 3
    assert pg.form._rows[0]["edits"][pg.form._fields[0].key].text() == "L1"
    assert pg.form._rows[1]["edits"][pg.form._fields[0].key].text() == "L2"
    pg.form.remove_row(pg.form._rows[2])
    assert pg.form.row_count() == 2


def test_create_adds_all_and_stays_on_page(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "lot")
    shell.go("projects")
    _fill(pg, 0, "L1")
    pg.start_create("lot")
    pg.start_create("lot")              # blank middle row
    _fill(pg, 1, "L2")
    pg.start_create("lot")              # blank -> skipped
    pg.form.ok.click()
    assert _lots(shell, proj, samp) == ["L1", "L2"]
    assert shell.current_page() == "projects"
    assert pg._node.kind == "sample"
    assert pg.form.isVisible() and pg.form.row_count() == 1
    assert pg.form._rows[0]["edits"][pg.form._fields[0].key].text() == ""
    qtbot.waitUntil(lambda: not pg.is_loading() and len(pg.cards()) == 2, timeout=5000)


def test_duplicates_block_everything_with_message(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "lot")
    shell.state.workspace.create_lot(proj, samp, "Old")
    _fill(pg, 0, "A")
    pg.start_create("lot")
    _fill(pg, 1, "a")                    # same as row 1, case-insensitive
    pg.start_create("lot")
    _fill(pg, 2, "OLD")                  # exists already
    pg.form.ok.click()
    assert _lots(shell, proj, samp) == ["Old"]          # nothing created
    assert pg.form.row_count() == 3                      # rows kept
    txt = pg.form.error.text()
    assert pg.form.error.isVisible() and "typed twice" in txt and "already exists" in txt
    _fill(pg, 1, "B")
    _fill(pg, 2, "C")
    pg.form.ok.click()
    assert _lots(shell, proj, samp) == ["A", "B", "C", "Old"]


def test_multiple_parts_in_a_job(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "sample")
    _fill(pg, 0, "P2")
    pg.start_create("sample")
    _fill(pg, 1, "P3")
    pg.form.ok.click()
    ids = sorted(s.sample_id for s in shell.state.workspace.list_samples(proj))
    assert ids == ["P1", "P2", "P3"]
    assert pg._node.kind == "project" and pg.form.isVisible()


def test_all_blank_creates_nothing(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "lot")
    pg.form.ok.click()
    assert _lots(shell, proj, samp) == []
    assert pg.form.error.isVisible()


def test_whitespace_only_rows_are_blank(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "lot")
    _fill(pg, 0, "   ")
    pg.start_create("lot")
    _fill(pg, 1, "X")
    pg.form.ok.click()
    assert _lots(shell, proj, samp) == ["X"]


def test_remove_middle_row_keeps_others_in_order(shell, qtbot):
    pg, proj, samp = _open(shell, qtbot, "lot")
    for i, t in enumerate(["L1", "L2", "L3"]):
        if i:
            pg.start_create("lot")
        _fill(pg, i, t)
    pg.form.remove_row(pg.form._rows[1])
    key = pg.form._fields[0].key
    assert [r["edits"][key].text() for r in pg.form._rows] == ["L1", "L3"]
    assert pg.form._rows[1]["title"].text().endswith("2")


def test_partial_failure_keeps_only_failed_entries(shell, qtbot, monkeypatch):
    pg, proj, samp = _open(shell, qtbot, "lot")
    ws = shell.state.workspace
    real = ws.create_lot

    def flaky(p, s, ident, **kw):
        if ident == "BAD":
            raise OSError("disk exploded: secret path")
        return real(p, s, ident, **kw)
    monkeypatch.setattr(ws, "create_lot", flaky)
    _fill(pg, 0, "OK1")
    pg.start_create("lot")
    pg.start_create("lot")               # blank row in between
    _fill(pg, 1, "BAD")
    pg.start_create("lot")
    _fill(pg, 3, "OK2")
    pg.form.ok.click()
    assert _lots(shell, proj, samp) == ["OK1", "OK2"]
    key = pg.form._fields[0].key
    assert [r["edits"][key].text() for r in pg.form._rows] == ["BAD"]
    txt = pg.form.error.text()
    assert "BAD" in txt and "OK1" in txt and "disk exploded" not in txt
    assert pg.form.isVisible()
