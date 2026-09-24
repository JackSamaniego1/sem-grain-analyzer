"""INN-27 UI: Projects ▸ Lot result card + field table (Include checkbox with
required reason), lot stats honouring grain filters / manual grain removals,
report export opt-in, and the UI-09 follow-ups FIX-02 (image card opens that
image), FIX-03 (Delete key routed by the Projects page), FIX-04 (one Catalog
through trash_node).  Offscreen; everything in tmp_path."""
import math
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from core.grain_detector import AnalysisResult, GrainResult  # noqa: E402
from data.models import AppSettings, ImageEntry, read_json, write_json_atomic  # noqa: E402
from data.settings import save_settings  # noqa: E402


class _Toasts:
    def __init__(self):
        self.calls = []

    def show_toast(self, *a, **k):
        self.calls.append(a)

    def has(self, title):
        return any(c[0] == title for c in self.calls)


def _result(G, ecd=2.0, n_grains=4):
    grains = [GrainResult(grain_id=i + 1, area_px=100.0, area_um2=math.pi * ecd ** 2 / 4,
                          perimeter_px=40.0, perimeter_um=5.0, equivalent_diameter_px=11.3,
                          equivalent_diameter_um=ecd, major_axis_um=ecd, minor_axis_um=ecd,
                          aspect_ratio=1.0, circularity=0.9, eccentricity=0.1,
                          centroid_x=5.0, centroid_y=5.0, bbox=(0, 0, 10, 10))
              for i in range(n_grains)]
    r = AnalysisResult()
    r.grains, r.grain_count = grains, n_grains
    r.px_per_um, r.has_calibration, r.mean_diameter_um = 5.0, True, ecd
    r.astm_g = G
    r.astm = {"primary_method": "planimetric", "G_primary": G}
    return r


def _lot(root: Path, sessions, lot="L-1"):
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(root)
    pp = ws.create_project("P1")
    sp = ws.create_sample(pp, "S1")
    lp = ws.create_lot(pp, sp, lot)
    img = np.full((32, 32, 3), 128, np.uint8)
    refs = []
    for label, gs in sessions:
        ents = [ImageEntry(image_bgr=img, result=_result(g), filename=f"{label}_{i}.png")
                for i, g in enumerate(gs)]
        refs.append(save_session(lp, {"project": "P1", "sample_id": "S1", "lot_number": lot},
                                 ents, label=label, catalog=Catalog(root)))
    return lp, refs


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


@pytest.fixture
def page(env, qtbot):
    from ui.app_state import AppState
    from ui.pages.projects_page import ProjectsPage
    st = AppState()
    w = ProjectsPage(st, _Toasts())
    qtbot.addWidget(w)
    w.resize(1400, 1000)
    w.show()
    yield w
    w.close()


def _show_lot(page, qtbot, lot):
    from ui.app_state import NodeRef
    page.reload()
    page.state.set_node(NodeRef("lot", lot))
    qtbot.waitUntil(lambda: not page.is_loading() and page._node.path == lot
                    and page.lot_panel.isVisible() and not page.is_lot_loading(),
                    timeout=15000)
    return page.lot_panel


# ---------------------------------------------------------------- card + table
def test_lot_card_shows_mean_ci_ra_fields_and_status(page, env, qtbot):
    lot, _ = _lot(env, [("A", [7.0, 7.2, 7.4]), ("B", [7.6, 7.8])])
    lp = _show_lot(page, qtbot, lot)
    st = lp.stats()
    assert st.n_fields == 5 and st.G_mean == pytest.approx(7.4)
    card = lp.card
    assert card.g_value.text() == "G 7.40"
    assert card.g_pm.text() == f"± {st.G_ci95:.2f}"
    assert card.f_ra.value.text() == f"{st.RA_pct:.1f} %"
    assert card.f_fields.value.text() == f"5 of {st.n_needed}"
    assert "µm" in card.f_ecd.value.text()
    assert card.chip.kind() == {"green": "success", "amber": "warning"}[st.status_level]
    assert card.summary.text() == st.summary_text()
    assert lp.table.rowCount() == 5
    assert all(lp.table.item(r, 0).checkState() == Qt.Checked for r in range(5))
    # scope: one session only
    assert card.scope.isVisibleTo(card) and card.scope.count() == 3
    card.scope.setCurrentIndex(2)
    assert lp.stats().n_fields == 2 and lp.table.rowCount() == 2
    assert lp.stats().G_mean == pytest.approx(7.7)


def test_uncalibrated_lot_chip_is_grey(page, env, qtbot):
    lot, refs = _lot(env, [("A", [7.0, 7.2])])
    for f in ("A_0", "A_1"):
        p = refs[0].path / "results" / f"{f}.summary.json"
        d = read_json(p)
        d.update(astm_g=None, has_calibration=False, px_per_um=0.0, astm={})
        write_json_atomic(p, d)
    lp = _show_lot(page, qtbot, lot)
    assert lp.card.chip.kind() == "neutral" and lp.card.chip.text() == "Uncalibrated"
    assert lp.card.g_value.text() == "G n/a"


def test_exclusion_needs_reason_and_is_logged(page, env, qtbot):
    lot, refs = _lot(env, [("A", [7.0, 7.2, 7.4, 7.6, 7.8])])
    lp = _show_lot(page, qtbot, lot)
    row = [f.image_name for f in lp.table.fields()].index("A_2.png")
    lp.table.item(row, 0).setCheckState(Qt.Unchecked)
    assert lp.bar.isVisible() and not lp.bar.ok.isEnabled()      # reason required
    lp.bar._cancel()                                              # keep included
    assert lp.table.item(row, 0).checkState() == Qt.Checked
    assert read_json(refs[0].path / "manifest.json").get("audit_log", []) == []

    lp.table.item(row, 0).setCheckState(Qt.Unchecked)
    lp.bar.reason.setText("scratch across the field")
    assert lp.bar.ok.isEnabled()
    lp.bar.ok.click()
    qtbot.waitUntil(lambda: lp.stats() is not None and lp.stats().n_fields == 4, timeout=15000)
    m = read_json(refs[0].path / "manifest.json")
    e = [i for i in m["images"] if i["filename"] == "A_2.png"][0]
    assert e["included"] is False and e["exclusion_reason"] == "scratch across the field"
    assert m["audit_log"][-1]["action"] == "exclude_field"
    assert m["audit_log"][-1]["operator"] == "Tester"
    assert page.toasts.has("Field excluded")
    row = [f.image_name for f in lp.table.fields()].index("A_2.png")
    assert lp.table.item(row, 0).checkState() == Qt.Unchecked
    assert "scratch" in lp.table.item(row, 6).text()
    # re-include: no reason needed, logged too
    lp.table.item(row, 0).setCheckState(Qt.Checked)
    qtbot.waitUntil(lambda: lp.stats().n_fields == 5, timeout=15000)
    assert read_json(refs[0].path / "manifest.json")["audit_log"][-1]["action"] == "include_field"


def test_lot_panel_hidden_for_other_levels(page, env, qtbot):
    from ui.app_state import NodeRef
    lot, _ = _lot(env, [("A", [7.0, 7.2])])
    _show_lot(page, qtbot, lot)
    page.state.set_node(NodeRef("sample", lot.parent))
    qtbot.waitUntil(lambda: not page.is_loading() and page._node.kind == "sample", timeout=15000)
    assert not page.lot_panel.isVisible()


# ---------------------------------------------------------------- edited G (known gap)
def _analysed_lot(root: Path):
    """One real analysed image (label image kept) saved like the app does."""
    from core.grain_detector import GrainDetector
    from data.session_io import save_session
    from data.workspace import Workspace
    from tests.conftest import make_mosaic
    gray, _ = make_mosaic(h=256, w=256, n_grains=40, seed=5)
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    res = GrainDetector().analyze(bgr, px_per_um=2.0)
    ws = Workspace(root)
    pp = ws.create_project("P1")
    sp = ws.create_sample(pp, "S1")
    lp = ws.create_lot(pp, sp, "L-9")
    ref = save_session(lp, {}, [ImageEntry(image_bgr=bgr, result=res, filename="f.png")])
    return lp, Path(ref.path), res


def test_lot_stats_honour_manual_grain_removal(env):
    from data.catalog import fields_for_lot
    from ui.pages.lot_results import refilter_saved_field
    lot, sdir, res = _analysed_lot(env)
    calls = []

    def spy(*a):
        calls.append(a)
        return refilter_saved_field(*a)

    before = fields_for_lot(lot, refilter=spy)[0]
    assert calls == []                              # saved result is current: no recompute
    assert before.grain_count == len(res.grains)
    # a manual removal recorded in the manifest but not yet in the saved result
    gone = sorted(int(g.grain_id) for g in res.grains)[:len(res.grains) // 2]
    m = read_json(sdir / "manifest.json")
    m["images"][0]["manual_excluded"] = gone
    write_json_atomic(sdir / "manifest.json", m)
    stale = fields_for_lot(lot)[0]                   # data layer alone: saved numbers
    assert stale.grain_count == before.grain_count
    edited = fields_for_lot(lot, refilter=spy)[0]
    assert len(calls) == 1
    assert edited.grain_count == before.grain_count - len(gone)
    assert edited.G is not None and edited.G != pytest.approx(before.G, abs=1e-6)
    assert len(edited.ecds_um) == edited.grain_count


def test_edits_detector_rules():
    from data.catalog import edits_not_in_saved_result as stale
    grains = [{"grain_id": 1, "area_px": 50, "aspect_ratio": 1.2, "circularity": 0.8},
              {"grain_id": 2, "area_px": 500, "aspect_ratio": 4.0, "circularity": 0.3}]
    filtered = {"astm": {"notes": ["Post-filter: planimetric …"]}}
    assert not stale({}, grains, {}, set())
    assert stale({}, grains, {}, {2})                              # manual id still kept
    assert not stale(filtered, grains, {}, {9})                    # already removed
    assert stale(filtered, grains, {"min_area_px": 100}, set())   # size limit not applied
    assert stale(filtered, grains, {"max_aspect_ratio": 3}, set())
    assert stale({}, grains, {"exclude_border": True}, set())      # never filtered
    assert not stale(filtered, grains, {"exclude_border": True}, set())


# ---------------------------------------------------------------- report opt-in
def test_report_export_opts_in_to_lot_block(env, tmp_path):
    from reports.model import ReportImageInput
    from ui.pages import report_builder as rb

    class S:
        pass

    lot, refs = _lot(env, [("A", [7.0, 7.4, 7.8])])
    st = S()
    st.settings = AppSettings()
    st.session = S()
    st.session.path = refs[0].path
    inputs = [ReportImageInput(image_path=str(refs[0].path / "images" / f"A_{i}.png"),
                               result=_result(g), lot_number="L-1")
              for i, g in enumerate([7.0, 7.4, 7.8])]
    assert rb.sample_statistics_arg(st, inputs[:1]) is None
    assert rb.sample_statistics_arg(st, inputs) == "auto"
    from data.session_io import set_image_included
    set_image_included(refs[0].path, "A_2.png", False, reason="bad field", operator="t")
    out = rb.sample_statistics_arg(st, inputs)
    assert isinstance(out, list) and out[0]["n_fields"] == 2 and out[0]["n_excluded"] == 1
    model = rb.build_model(inputs, title="t", operator="t", sample_statistics="auto")
    assert model.sample_statistics and model.sample_statistics[0]["n_fields"] == 3


# ---------------------------------------------------------------- FIX-02/03/04
def _lot_mode_env(env):
    from data.hierarchy import PRESETS, save_profile
    env.mkdir(parents=True, exist_ok=True)
    save_profile(env, PRESETS["job_part_lot"])


def _image_lot(env, n=3):
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    from tests.ui_shell_helpers import mosaic_png
    ws = Workspace(env)
    pp = ws.create_project("24-117")
    sp = ws.create_sample(pp, "P-1")
    lp = ws.create_lot(pp, sp, "A")
    src = env.parent / "src"
    src.mkdir(exist_ok=True)
    ents = [ImageEntry(source_path=mosaic_png(src / f"field_{i}.png", seed=11 + i))
            for i in range(n)]
    save_session(lp, {}, ents, in_place=True, catalog=Catalog(env))
    return lp


def test_image_card_open_jumps_to_that_image(env, qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState, NodeRef
    _lot_mode_env(env)
    lp = _image_lot(env)
    w = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(w)
    w.show()
    pg = w.projects
    pg.reload()
    pg.state.set_node(NodeRef("lot", lp))
    qtbot.waitUntil(lambda: not pg.is_loading() and len(pg.cards()) == 4, timeout=15000)
    card = [c for c in pg.cards() if c.item["kind"] == "image"][2]
    name = card.item["filename"]
    with qtbot.waitSignal(pg.open_image_requested, timeout=5000) as sig:
        pg._card_open(card)
    assert sig.args == [lp, name]
    qtbot.waitUntil(lambda: w.state.session is not None
                    and w.state.current_image() is not None
                    and w.state.current_image().filename == name, timeout=15000)
    assert w.current_page() == "analyze"
    w.go("projects")
    assert "trash" in w.act_delete.text()
    w.close()


def test_delete_key_routes_through_projects_page(env, qtbot):
    from ui.app_state import AppState, NodeRef
    from ui.pages.projects_page import ProjectsPage
    _lot_mode_env(env)
    lp = _image_lot(env)
    pg = ProjectsPage(AppState(), _Toasts())
    qtbot.addWidget(pg)
    pg.resize(1400, 900)
    pg.show()
    pg.reload()
    pg.state.set_node(NodeRef("lot", lp))
    qtbot.waitUntil(lambda: not pg.is_loading() and len(pg.cards()) == 4, timeout=15000)
    imgs = [c for c in pg.cards() if c.item["kind"] == "image"]
    record = [c for c in pg.cards() if c.item.get("record")][0]
    qtbot.mouseClick(record, Qt.LeftButton)
    pg.delete_pressed()                          # the folder you are in: never deleted
    assert not pg.confirm.isVisible()
    imgs[0].check.setChecked(True)
    imgs[2].check.setChecked(True)
    pg.delete_pressed()
    assert pg.confirm.isVisible() and "Delete 2 images?" in pg.confirm.title.text()
    pg.confirm.hide_bar()
    pg.lot_panel.show()
    pg.lot_panel.table.setFocus()
    pg.lot_panel.table.setFocus(Qt.OtherFocusReason)
    from PySide6.QtWidgets import QApplication
    if QApplication.focusWidget() is pg.lot_panel.table:
        pg.delete_pressed()                      # typing / table in the lot result: no-op
        assert not pg.confirm.isVisible()


def test_batched_delete_builds_one_catalog(env, qtbot, monkeypatch):
    import ui.pages.projects_page as pp_mod
    from data.catalog import Catalog
    from data.workspace import Workspace
    made = []

    class Counting(Catalog):
        def __init__(self, root):
            made.append(root)
            super().__init__(root)

    lot_a, _ = _lot(env, [("A", [7.0])], lot="L-1")
    ws = Workspace(env)
    lot_b = ws.create_lot(lot_a.parent.parent, lot_a.parent, "L-2")
    monkeypatch.setattr(pp_mod, "Catalog", Counting)
    cat = Counting(env)
    for lp in (lot_a, lot_b):
        pp_mod.trash_node(ws, env, "lot", lp, catalog=cat)
    assert len(made) == 1 and not lot_a.exists() and not lot_b.exists()
    pp_mod.trash_node(ws, env, "sample", lot_a.parent)          # default still works
    assert len(made) == 2
