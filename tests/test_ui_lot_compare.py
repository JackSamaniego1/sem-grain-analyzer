"""INN-43: baseline lot flag (data layer) and the Projects › Compare lots page
(ΔG matrix, Welch ANOVA sentence, star / baseline, TOST verdicts with an
editable tolerance, ECD overlay, empty / not-calibrated states) and its
reachability from the Projects selection bar.  Offscreen; all in tmp_path."""
import math
from pathlib import Path

import numpy as np
import pytest

from core.grain_detector import AnalysisResult, GrainResult
from data.models import AppSettings, ImageEntry, LotMeta, read_json, write_json_atomic
from data.settings import save_settings


# ---------------------------------------------------------------- fixtures
def _result(G, ecd=2.0, n_grains=5, calibrated=True):
    grains = [GrainResult(grain_id=i + 1, area_px=100.0, area_um2=math.pi * ecd ** 2 / 4,
                          perimeter_px=40.0, perimeter_um=5.0, equivalent_diameter_px=11.3,
                          equivalent_diameter_um=ecd * (1 + 0.1 * i), major_axis_um=ecd,
                          minor_axis_um=ecd, aspect_ratio=1.0, circularity=0.9,
                          eccentricity=0.1, centroid_x=5.0, centroid_y=5.0, bbox=(0, 0, 10, 10))
              for i in range(n_grains)]
    r = AnalysisResult()
    r.grains, r.grain_count = grains, n_grains
    if calibrated:
        r.px_per_um, r.has_calibration, r.mean_diameter_um = 5.0, True, ecd
        r.astm_g = G
        r.astm = {"primary_method": "planimetric", "G_primary": G}
    else:
        r.px_per_um, r.has_calibration = 0.0, False
        r.astm_g, r.astm = None, {}
    return r


def _ws(root: Path, material="316L", sample="S1", project="P1"):
    from data.workspace import Workspace
    ws = Workspace(root)
    try:
        pp = ws.resolve_project(project)
    except FileNotFoundError:
        pp = ws.create_project(project)
    try:
        sp = ws.resolve_sample(pp, sample)
    except FileNotFoundError:
        sp = ws.create_sample(pp, sample, material=material)
    return ws, pp, sp


def _lot(root: Path, name: str, Gs, sample="S1", material="316L", calibrated=True, ecd=2.0):
    from data.catalog import Catalog
    from data.session_io import save_session
    ws, pp, sp = _ws(root, material, sample)
    lp = ws.create_lot(pp, sp, name)
    img = np.full((32, 32, 3), 128, np.uint8)
    ents = [ImageEntry(image_bgr=img, result=_result(g, ecd, calibrated=calibrated),
                       filename=f"f{i}.png") for i, g in enumerate(Gs)]
    save_session(lp, {"project": "P1", "sample_id": sample, "lot_number": name}, ents,
                 label="A", catalog=Catalog(root))
    return lp


EQ = [7.0, 7.1, 6.9, 7.05, 6.95, 7.0]          # baseline, ~7.0 ± 0.1
EQ2 = [7.02, 7.12, 6.92, 7.07, 6.97, 7.02]
FINER = [7.8, 7.9, 7.7, 7.85, 7.75, 7.8]
FEW = [7.2, 7.4]


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
    from ui.pages.lot_compare_page import LotComparePage
    w = LotComparePage(AppState(), _Toasts())
    qtbot.addWidget(w)
    w.resize(1400, 1000)
    w.show()
    yield w
    w.close()


class _Toasts:
    def __init__(self):
        self.calls = []

    def show_toast(self, title, message="", severity="info", **kw):
        self.calls.append((title, message, severity, kw))


def _load(page, qtbot, paths):
    page.set_lots(paths)
    qtbot.waitUntil(lambda: not page.is_loading(), timeout=15000)


def _flags(*lots):
    return [bool(read_json(Path(l) / "lot.json").get("is_baseline")) for l in lots]


# ---------------------------------------------------------------- data layer
def test_lotmeta_old_manifest_without_is_baseline_loads(tmp_path):
    old = {"schema_version": 1, "lot_number": "L-1", "supplier": "Acme", "notes": "",
           "spec_limits": {}, "created_utc": "2025-01-01T00:00:00Z"}
    write_json_atomic(tmp_path / "lot.json", old)
    m = LotMeta.from_dict(read_json(tmp_path / "lot.json"))
    assert m.is_baseline is False and m.lot_number == "L-1" and m.supplier == "Acme"
    m.is_baseline = True
    again = LotMeta.from_dict(m.to_dict())
    assert again.is_baseline is True and again.to_dict() == m.to_dict()


def test_set_baseline_unmarks_same_material_only(tmp_path):
    from data.workspace import Workspace
    root = tmp_path / "ws"
    a = _lot(root, "A", EQ)
    b = _lot(root, "B", EQ, sample="S2", material="316l ")    # same material, other sample
    c = _lot(root, "C", EQ, sample="S3", material="718")
    d = _lot(root, "D", EQ, sample="S4", material="")          # blank material: own sample
    ws = Workspace(root)
    ws.set_baseline_lot(c)
    ws.set_baseline_lot(d)
    assert ws.set_baseline_lot(a) == [a]
    assert _flags(a, b, c, d) == [True, False, True, True]
    changed = ws.set_baseline_lot(b)
    assert set(changed) == {a, b}
    assert _flags(a, b, c, d) == [False, True, True, True]
    assert ws.baseline_lot_for(a) == b and ws.baseline_lot_for(c) == c
    ws.set_baseline_lot(b, on=False)
    assert ws.baseline_lot_for(a) is None and _flags(b) == [False]
    # the flag lives in lot.json next to the lot's other metadata
    assert read_json(c / "lot.json")["lot_number"] == "C"


# ---------------------------------------------------------------- page
def test_empty_state_for_fewer_than_two_lots(page, env, qtbot):
    page.set_lots([])
    assert page.state_name() == "empty"
    a = _lot(env, "A", EQ)
    page.set_lots([a])
    assert page.state_name() == "empty"
    with qtbot.waitSignal(page.back_requested, timeout=1000):
        page.empty.action_button.click()


def test_uncalibrated_lots_show_not_enough_state(page, env, qtbot):
    a = _lot(env, "A", EQ)
    u = _lot(env, "U", EQ, calibrated=False)
    _load(page, qtbot, [a, u])
    assert page.state_name() == "problem"
    assert "Not calibrated yet: U" in page.problem.body_label.text()


def test_matrix_anova_and_verdicts_against_baseline(page, env, qtbot):
    from data.workspace import Workspace
    from ui.design.theme import current_tokens
    base = _lot(env, "BASE", EQ)
    eq, finer, few = _lot(env, "EQ", EQ2), _lot(env, "FINER", FINER), _lot(env, "FEW", FEW)
    Workspace(env).set_baseline_lot(base)
    _load(page, qtbot, [eq, finer, few])           # baseline added automatically, shown first
    assert page.state_name() == "populated"
    assert [r.lot["name"] for r in page.rows] == ["BASE", "EQ", "FINER", "FEW"]
    assert page.rows[0].lot["added"] and "added as baseline" in page.rows[0].caption.text()
    verdicts = {r.lot["name"]: r.verdict_text() for r in page.rows}
    assert verdicts == {"BASE": "Baseline", "EQ": "Equivalent", "FINER": "Not equivalent",
                        "FEW": "Can't tell yet"}
    assert page.row_for("FINER").sentence.text().startswith("Not equivalent — grains are finer")
    assert "more field" in page.row_for("FEW").sentence.text()
    assert page.rows[0].star.isChecked() and not page.rows[1].star.isChecked()
    # matrix: G(column) - G(row), coloured by band with the CI in the tooltip
    m = page.matrix
    assert m.rowCount() == m.columnCount() == 4
    assert m.item(0, 2).text() == "+0.80" and m.item(2, 0).text() == "−0.80"
    assert m.band_at(0, 1) == "green" and m.band_at(0, 3) == "amber" and m.band_at(0, 2) == "red"
    t = current_tokens()
    assert m.item(0, 2).background().color().name().lower() == t.danger.bg.lower()
    assert m.item(0, 1).background().color().name().lower() == t.success.bg.lower()
    assert "confidence range" in m.item(0, 2).toolTip() and "finer" in m.item(0, 2).toolTip()
    assert m.item(1, 1).text() == "—"
    # ANOVA in plain words; statistics only in the tooltip
    assert page.anova_badge.text() == "Lots differ"
    assert "average grain size" in page.anova_text.text()
    assert "Welch ANOVA" in page.anova_text.toolTip()
    # overlay chart: one series per calibrated lot
    assert page.chart.has_data() and len(page.chart._series) == 4
    assert "baseline BASE" in page.header.subtitle.text()


def test_tolerance_change_recomputes_verdicts(page, env, qtbot):
    from data.workspace import Workspace
    base, finer = _lot(env, "BASE", EQ), _lot(env, "FINER", FINER)
    Workspace(env).set_baseline_lot(base)
    _load(page, qtbot, [base, finer])
    assert page.row_for("FINER").verdict_text() == "Not equivalent"
    page.margin.setValue(1.0)
    assert page.row_for("FINER").verdict_text() == "Equivalent"
    assert "±1 G" in page.row_for("FINER").sentence.text()
    assert page.state.ui_state["compare_margin_G"] == pytest.approx(1.0)


def test_star_sets_baseline_persists_and_undo(page, env, qtbot):
    a, b, c = _lot(env, "A", EQ), _lot(env, "B", EQ2), _lot(env, "C", FINER)
    _load(page, qtbot, [a, b, c])
    assert all(r.verdict_text() == "No baseline" for r in page.rows)
    assert "click the star" in page.rows[0].sentence.text()
    page.row_for("A").star.click()
    qtbot.waitUntil(lambda: page.row_for("A").verdict_text() == "Baseline", timeout=5000)
    assert _flags(a, b, c) == [True, False, False]
    assert page.row_for("B").verdict_text() == "Equivalent"
    assert page.toasts.calls[-1][0] == "Baseline updated"
    # moving the star un-marks A (same material)
    page.row_for("C").star.click()
    qtbot.waitUntil(lambda: page.row_for("C").verdict_text() == "Baseline", timeout=5000)
    assert _flags(a, b, c) == [False, False, True]
    assert page.row_for("A").verdict_text() == "Not equivalent"
    # Undo on the toast restores A as the baseline
    page.toasts.calls[-1][3]["on_action"]()
    qtbot.waitUntil(lambda: _flags(a, b, c) == [True, False, False], timeout=5000)
    qtbot.waitUntil(lambda: page.row_for("A").verdict_text() == "Baseline", timeout=5000)
    # clicking the baseline's star clears it
    page.row_for("A").star.click()
    qtbot.waitUntil(lambda: _flags(a) == [False], timeout=5000)
    qtbot.waitUntil(lambda: page.row_for("A").verdict_text() == "No baseline", timeout=5000)


def test_different_materials_use_their_own_baseline(page, env, qtbot):
    from data.workspace import Workspace
    a = _lot(env, "A", EQ)
    x = _lot(env, "X", EQ2, sample="S9", material="718")
    Workspace(env).set_baseline_lot(a)
    _load(page, qtbot, [a, x])
    assert page.row_for("X").verdict_text() == "No baseline"
    assert "718" in page.row_for("X").sentence.text()


# ---------------------------------------------------------------- shell / projects
def test_compare_reachable_from_projects_selection(env, qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState, NodeRef
    a, b = _lot(env, "A", EQ), _lot(env, "B", FINER)
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1400, 900)
    shell.show()
    pp = shell.projects
    pp.reload()
    pp.state.set_node(NodeRef("sample", a.parent))
    qtbot.waitUntil(lambda: not pp.is_loading() and len(pp.cards()) == 2, timeout=15000)
    assert not pp.selbar.compare_btn.isVisibleTo(pp.selbar)
    pp._set_selection([a])
    assert not pp.selbar.compare_btn.isVisibleTo(pp.selbar)
    pp.select_all()
    assert pp.selbar.compare_btn.isVisibleTo(pp.selbar)
    pp.selbar.compare_btn.click()
    assert shell.current_page() == "compare"
    assert shell.stack.currentWidget() is shell.compare
    assert shell.rail.current() == "projects"
    qtbot.waitUntil(lambda: shell.compare.state_name() == "populated", timeout=15000)
    assert len(shell.compare.rows) == 2
    # clicking the (already current) Projects rail entry goes back
    shell.rail.item("projects").activated.emit("projects")
    assert shell.current_page() == "projects"
    shell.show_compare([a, b])
    shell.compare.back_btn.click()
    assert shell.current_page() == "projects"
    shell.close()
