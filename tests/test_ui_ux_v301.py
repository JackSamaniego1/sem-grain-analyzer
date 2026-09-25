"""
v3.0.1 user feedback (handoff/08_USER_FEEDBACK_2026-09-25.md), Analyze page
and shell: UX-01..06, UX-08..11.

Detection in these tests uses the classic Threshold mode (the AI model file
is not part of a developer checkout); the AI-assisted default is tested by
pretending the model file is installed.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings, ImageEntry  # noqa: E402
from data.session_io import load_session  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.conftest import make_mosaic  # noqa: E402
from tests.ui_shell_helpers import confirm_setup, make_session  # noqa: E402

TIMEOUT = 90000


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


def _shell(qtbot):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1500, 950)
    shell.show()
    return shell


def _open(qtbot, path):
    shell = _shell(qtbot)
    shell.open_session(path, prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    return shell


def _bar_image(path: Path, seed: int, scale_len: int = 120) -> str:
    """Mosaic with an SEM-style info bar (text + scale bar) at the bottom."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_infobar import add_bar
    g, _ = make_mosaic(h=360, w=480, n_grains=50, seed=seed)
    g, _r, _s = add_bar(g, frac=0.12, scale_len=scale_len, seed=seed)
    cv2.imwrite(str(path), np.repeat(g[:, :, None], 3, axis=2))
    return str(path)


def _lots(root: Path, n_lots: int, per_lot: int, size: int = 224, bar: bool = False,
          sample: str = "S-1") -> Path:
    """``n_lots`` lots of one part, each with a session of ``per_lot`` images."""
    for li in range(n_lots):
        if bar:
            from data.catalog import Catalog
            from data.session_io import save_session
            from data.workspace import Workspace
            ws = Workspace(root)
            try:
                pp = ws.resolve_project("Proj")
            except FileNotFoundError:
                pp = ws.create_project("Proj")
            try:
                sp = ws.resolve_sample(pp, sample)
            except FileNotFoundError:
                sp = ws.create_sample(pp, sample)
            lp = ws.create_lot(pp, sp, f"L-{li + 1}")
            src = root.parent / "src"
            src.mkdir(parents=True, exist_ok=True)
            paths = [_bar_image(src / f"b{li}_{i}.png", 5 + li * 10 + i) for i in range(per_lot)]
            save_session(lp, {"operator": "Tester"}, [ImageEntry(source_path=p) for p in paths],
                         label=f"Run {li}", catalog=Catalog(root))
        else:
            make_session(root, per_lot, label=f"Run {li}", lot=f"L-{li + 1}", sample=sample)
    return root / "Proj" / sample


# ====================================================================== UX-01
def test_ux01_modes_ai_first_default_no_automatic(env, qtbot, monkeypatch):
    import ui.detection_modes as dm
    import ui.pages.analyze_page as ap
    assert [m[0] for m in dm.MODES] == ["sam_astm", "boundary", "threshold"]
    assert "auto" not in dm.MODE_KEYS
    # model file present -> AI-assisted is the default, legacy "auto" maps to it
    monkeypatch.setattr(dm, "sam_model_available", lambda: True)
    monkeypatch.setattr(ap, "sam_model_available", lambda: True)
    assert dm.default_mode() == "sam_astm" and dm.normalize_mode("auto") == "sam_astm"
    panel = ap.ParamPanel()
    qtbot.addWidget(panel)
    panel.show()
    assert list(panel.mode_cards) == ["sam_astm", "boundary", "threshold"]
    assert panel.mode() == "sam_astm"
    assert panel.sec_adv.isHidden()                          # hidden while AI-assisted
    panel.set_mode("boundary", emit=True)
    assert not panel.sec_adv.isHidden()
    panel.set_mode("auto")                                   # old sessions
    assert panel.mode() == "sam_astm" and panel.sec_adv.isHidden()
    # model file missing -> reinstall hint, Boundary used, advanced visible
    monkeypatch.setattr(dm, "sam_model_available", lambda: False)
    monkeypatch.setattr(ap, "sam_model_available", lambda: False)
    p2 = ap.ParamPanel()
    qtbot.addWidget(p2)
    assert not p2.mode_cards["sam_astm"].isEnabled()
    assert "Reinstall" in p2.mode_cards["sam_astm"].toolTip()
    assert p2.mode() == "boundary" and not p2.sec_adv.isHidden()


def test_ux01_settings_panel_order(env, qtbot):
    shell = _open(qtbot, make_session(env, 1))
    a = shell.analyze
    lay = a.params.layout()
    order = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget()]
    assert order == [a.params.sec_mode, a.sec_cal, a.sec_scan, a.filters_host,
                     a.params.sec_invalid, a.params.sec_adv, a.sec_overlay]
    shell.close()


# ====================================================================== UX-02
def test_ux02_gate_blocks_until_scan_and_scale_confirmed(env, qtbot):
    from ui.pages.analyze_page import GATE_TEXT
    shell = _open(qtbot, make_session(env, 2))
    st, a = shell.state, shell.analyze
    a.params.set_mode("threshold")
    assert all(st.setup_issues(im) == ["scan", "scale"] for im in st.images())
    a.btn_all.click()
    assert not a.queue.is_running()
    hint = shell.setup_hint
    qtbot.waitUntil(lambda: hint.is_active(), timeout=5000)
    ov = hint.overlay
    assert ov.callout.body.text() == GATE_TEXT
    assert ov.callout.btn_next.text() == "Got it" and not ov.callout.dont_show.isVisible()
    # the spotlight is on the "Scan area & scale" tile, like a tour step
    r = hint.resolve(hint.steps[0])
    tile = a.setup_tile
    from PySide6.QtCore import QPoint, QRectF
    c = QRectF(tile.mapTo(shell, QPoint(0, 0)), tile.size().toSizeF()).center()
    assert r.contains(c)
    hint.finish()
    assert not shell.tour.is_active()                      # the tour itself never started
    confirm_setup(shell, qtbot)                             # scale + Auto-find
    with qtbot.waitSignal(a.queue.queue_finished, timeout=TIMEOUT):
        a.btn_all.click()
    shell.close()


def test_ux02_autofind_info_bar_scale_bar_and_length_entry(env, qtbot):
    sample = _lots(env, 1, 3, bar=True)
    lot = next(p for p in sample.iterdir() if p.is_dir() and (p / "lot.json").exists())
    rec = next(d for d in lot.iterdir() if (d / "manifest.json").exists())
    shell = _open(qtbot, rec)
    st, a = shell.state, shell.analyze
    a.setup_tile.btn_auto.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    for im in st.images():
        h, w = im.image_bgr.shape[:2]
        x, y, sw, sh = st.scan_for(im)
        assert (x, y, sw) == (0, 0, w) and sh < h          # info bar left out
        assert im.scan_source == "auto"
        assert im.bar_px == pytest.approx(120, abs=3)       # scale bar found
        assert st.setup_issues(im) == ["scale"]
    im0 = st.current_image()
    assert a.setup_tile.bar_row.isVisibleTo(a.setup_tile)
    a.setup_tile.bar_um.setValue(20.0)
    a.setup_tile.bar_same.setChecked(True)
    a.setup_tile.btn_bar.click()
    for im in st.images():                                  # same bar -> same scale
        assert st.px_for(im) == pytest.approx(im.bar_px / 20.0)
        assert st.setup_ready(im)
    assert im0.scale_source == "manual"
    assert "Manual" in a.setup_tile.scale_src.text()
    shell.close()


# ====================================================================== UX-03
def test_ux03_scale_bar_this_image_only_or_all(env, qtbot):
    from ui.calibration_dialog import CalibrationDialog
    shell = _open(qtbot, make_session(env, 3))
    st = shell.state
    im0, im1, im2 = st.images()
    dlg = CalibrationDialog(image_bgr=im0.image_bgr)
    qtbot.addWidget(dlg)
    assert dlg.btn_apply_image.text() == "Apply to this image only"
    got = []
    dlg.calibration_set.connect(got.append)
    dlg._px_distance = 100.0
    dlg.length_spin.setValue(25.0)
    dlg._set_apply_enabled(True)
    dlg.btn_apply_image.click()
    assert got == [pytest.approx(4.0)] and dlg.apply_scope == "image"
    st.set_calibration(4.0, im0.uid)
    assert st.px_for(im0) == 4.0 and st.px_for(im1) == 0.0
    snap = st.set_calibration_all(2.5)                     # "Apply to all images"
    assert all(st.px_for(im) == 2.5 for im in (im0, im1, im2))
    st.restore_scales(snap)
    assert st.px_for(im0) == 4.0 and st.px_for(im1) == 0.0
    shell.close()


# ====================================================================== UX-04
def test_ux04_filter_button_says_apply_for_this_image(qtbot):
    from ui.pages.filter_card import FilterCard
    fc = FilterCard()
    qtbot.addWidget(fc)
    assert fc.apply_all.text() == "Apply to all images"
    fc.scope.set_current_index(1)
    assert fc.apply_all.text() == "Apply"
    with qtbot.waitSignal(fc.apply_image_requested, timeout=1000):
        fc.apply_all.click()
    fc.scope.set_current_index(0)
    assert fc.apply_all.text() == "Apply to all images"
    with qtbot.waitSignal(fc.apply_all_requested, timeout=1000):
        fc.apply_all.click()


# ====================================================================== UX-05
def test_ux05_overlay_opacity_slider_persisted(env, qtbot):
    from ui.app_state import AppState, load_ui_state
    shell = _open(qtbot, make_session(env, 1))
    a = shell.analyze
    assert shell.state.overlay_opacity == 1.0
    a.opacity.setValue(45)
    assert shell.state.overlay_opacity == pytest.approx(0.45)
    assert a.canvas.overlay_opacity() == pytest.approx(0.45)
    assert load_ui_state()["overlay_opacity"] == pytest.approx(0.45)
    assert AppState().overlay_opacity == pytest.approx(0.45)   # next start
    shell.close()


# ====================================================================== UX-06
def test_ux06_remove_from_analyzer_and_add_back(env, qtbot):
    path = make_session(env, 3)
    shell = _open(qtbot, path)
    st, a = shell.state, shell.analyze
    uid = st.images()[1].uid
    name = st.images()[1].filename
    assert a.remove_images([uid]) == 1
    assert uid not in [im.uid for im in st.images()] and len(st.images()) == 2
    st.flush()
    assert name in [e.filename for e in load_session(path).manifest.images]   # lot untouched
    assert (path / "images" / name).exists() or any(path.rglob(name))
    rows = a.film.restore_rows()
    from ui.pages.image_tree import ROLE_TITLE
    assert len(rows) == 1 and "Add back 1" in list(rows.values())[0].data(0, ROLE_TITLE)
    a.film._on_clicked(list(rows.values())[0])              # the "Add back" row
    assert [im.uid for im in st.images()].count(uid) == 1 and len(st.images()) == 3
    assert not a.film.restore_rows()
    # Undo of a removal brings back exactly those images
    a.remove_images([st.images()[0].uid, st.images()[2].uid])
    assert len(st.images()) == 1
    st.restore_images(uids=[im.uid for im in st.session.removed])
    assert len(st.images()) == 3
    shell.close()


# ====================================================================== UX-08
def test_ux08_only_the_clicked_button_spins(env, qtbot):
    shell = _open(qtbot, make_session(env, 2))
    a = shell.analyze
    a.params.set_mode("threshold")
    confirm_setup(shell, qtbot)
    with qtbot.waitSignal(a.queue.queue_finished, timeout=TIMEOUT):
        a.btn_cur.click()
        assert a.btn_cur.is_loading() and not a.btn_all.is_loading()
        assert not a.btn_all.isEnabled()
    assert not a.btn_cur.is_loading() and a.btn_all.isEnabled()
    with qtbot.waitSignal(a.queue.queue_finished, timeout=TIMEOUT):
        a.btn_all.click()
        assert a.btn_all.is_loading() and not a.btn_cur.is_loading()
    shell.close()


# ====================================================================== UX-09
def test_ux09_projects_load_button_and_tree_and_table(env, qtbot):
    sample = _lots(env, 3, 2)
    shell = _shell(qtbot)
    pg = shell.projects
    pg.reload()
    qtbot.waitUntil(lambda: not pg.is_loading(), timeout=15000)
    pg.select_node(sample)
    qtbot.waitUntil(lambda: len(pg.cards()) == 3 and not pg.is_loading(), timeout=15000)
    pg._set_selection([str(c.item["path"]) for c in pg.cards()[:2]])
    assert pg.selbar.load_btn.isVisible()
    with qtbot.waitSignal(pg.load_requested, timeout=2000) as blk:
        pg.selbar.load_btn.click()
    assert len(blk.args[0]) == 2
    # the whole part: every lot's images, in one analyzer
    shell.load_into_analyzer([sample])
    st = shell.state
    qtbot.waitUntil(lambda: st.session is not None and st.session.multi
                    and not st.is_loading(), timeout=TIMEOUT)
    assert shell.current_page() == "analyze"
    assert len(st.images()) == 6 and len(st.session.records) == 3
    from ui.pages.image_tree import ROLE_KIND, ROLE_LINE2
    tree = shell.analyze.film
    kinds = sorted({g.data(0, ROLE_KIND) for g in tree.groups().values()})
    assert kinds == ["lot", "project", "sample"]            # Job > Part > Lot > images
    lot_groups = [g for g in tree.groups().values() if g.data(0, ROLE_KIND) == "lot"]
    assert len(lot_groups) == 3 and all(g.childCount() == 2 for g in lot_groups)
    assert "2 images" in lot_groups[0].data(0, ROLE_LINE2)
    # analyse everything, then the table groups / sorts by lot
    shell.analyze.params.set_mode("threshold")
    confirm_setup(shell, qtbot)
    with qtbot.waitSignal(shell.analyze.queue.queue_finished, timeout=TIMEOUT):
        shell.analyze.btn_all.click()
    qtbot.waitUntil(lambda: all(im.result is not None for im in st.images())
                    and not st.is_filtering(), timeout=TIMEOUT)
    t = shell.analyze.table
    shell.analyze.show_table_view()
    t.set_group_level("lot")
    t.rebuild()
    groups = t.group_items()
    assert len(groups) == 3 and sum(g.childCount() for g in groups) == 6
    assert {g.child(0).text(3) for g in groups} == {"L-1", "L-2", "L-3"}
    t.set_group_level("")
    t.rebuild()
    assert t.tree.topLevelItemCount() == 6
    from ui.pages.results_table import COL_LOT
    from PySide6.QtCore import Qt
    t.tree.sortItems(COL_LOT, Qt.DescendingOrder)          # click on "Lot" (descending)
    assert t.tree.topLevelItem(0).text(COL_LOT) == "L-3"
    # results were saved into each lot's own folder
    st.flush()
    for rec in st.session.records:
        ls = load_session(rec.path)
        assert all(e.has_result for e in ls.manifest.images)
    shell.close()


def test_ux09_two_hundred_images_load_without_freezing(env, qtbot):
    """~200 images from 4 lots: listed at once, pixels stream in off the GUI
    thread (the event loop keeps ticking), everything ends up loaded."""
    import time
    from PySide6.QtCore import QTimer
    sample = _lots(env, 4, 50, sample="Big")
    shell = _shell(qtbot)
    st = shell.state
    ticks = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    shell.load_into_analyzer([sample])
    qtbot.waitUntil(lambda: st.session is not None, timeout=TIMEOUT)
    assert len(st.images()) == 200                          # every image listed at once
    qtbot.waitUntil(lambda: not st.is_loading(), timeout=TIMEOUT)
    timer.stop()
    assert all(not im.loading and im.readable and im.thumb is not None for im in st.images())
    from ui.app_state import PIXEL_CACHE_MAX_IMAGES
    assert st.held_pixel_count() <= PIXEL_CACHE_MAX_IMAGES   # pixels only on demand
    tree = shell.analyze.film
    assert len([u for u in (im.uid for im in st.images()) if tree.item(u) is not None]) == 200
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert gaps and max(gaps) < 1.5, max(gaps)              # the GUI never froze
    shell.close()


# ====================================================================== UX-10 / UX-11
def test_ux10_nav_labels_fast_and_rail_expansion_remembered(env, qtbot):
    from ui.widgets.navigation import NAV_TIP_DELAY_MS
    shell = _shell(qtbot)
    item = shell.rail.item("analyze")
    assert NAV_TIP_DELAY_MS <= 150 and item._tip_timer.interval() <= 150
    assert item.toolTip() == "Analyze"
    item.show_label_tip()                                    # what the fast timer calls
    shell.rail.set_expanded(True, animate=False)
    assert shell.state.ui_state["rail_expanded"] is True
    shell.close()
    shell2 = _shell(qtbot)
    assert shell2.rail.is_expanded()
    shell2.close()


def test_ux11_device_chip_plain_words():
    from ui.app_shell import device_chip_text
    text, tip, kind = device_chip_text("CPU")
    assert text == "AI runs on: CPU" and "processor" in tip and kind == "neutral"
    text, tip, kind = device_chip_text("GPU · NVIDIA RTX A2000")
    assert text == "AI runs on: GPU (NVIDIA RTX A2000)" and "graphics card" in tip


def test_ux11_shell_chip_text(env, qtbot):
    shell = _shell(qtbot)
    assert shell.chip_device.text() == "AI runs on: CPU"
    assert "processor" in shell.chip_device.toolTip()
    shell.close()


# ====================================================================== memory bound
def test_pixel_memory_bounded_200_images_analysed(env, qtbot, monkeypatch):
    """200 images loaded and analysed: full-resolution pixels are held for
    at most PIXEL_CACHE_MAX_IMAGES images at once (current + small LRU);
    the analysis queue reads each image itself and releases it after."""
    import weakref
    from PySide6.QtCore import QThreadPool, QTimer
    import ui.app_state as app_state
    import ui.workers as workers
    refs = []
    real = workers.read_image

    def tracked(path):
        arr = real(path)
        if arr is not None:
            refs.append(weakref.ref(arr))
        return arr
    monkeypatch.setattr(workers, "read_image", tracked)
    monkeypatch.setattr(app_state, "read_image", tracked)
    peak = [0]

    def sample():
        refs[:] = [r for r in refs if r() is not None]
        peak[0] = max(peak[0], len(refs))
    timer = QTimer()
    timer.setInterval(15)
    timer.timeout.connect(sample)
    timer.start()

    sample_dir = _lots(env, 4, 50, sample="Mem")
    shell = _shell(qtbot)
    st, a = shell.state, shell.analyze
    shell.load_into_analyzer([sample_dir])
    qtbot.waitUntil(lambda: st.session is not None and not st.is_loading(), timeout=TIMEOUT)
    assert len(st.images()) == 200
    a.params.set_mode("threshold")
    confirm_setup(shell, qtbot)
    for im in st.images()[::37]:                            # browse around a little
        st.set_current_image(im.uid)
        qtbot.wait(20)
    with qtbot.waitSignal(a.queue.queue_finished, timeout=600000):
        a.btn_all.click()
    qtbot.waitUntil(lambda: all(im.result is not None for im in st.images())
                    and not st.is_filtering(), timeout=TIMEOUT)
    st.flush()
    timer.stop()
    sample()
    cap = app_state.PIXEL_CACHE_MAX_IMAGES
    held = sum(1 for im in st.images() if im.image_bgr is not None)
    assert held <= cap and st.held_pixel_count() <= cap
    assert st.pixel_peak <= cap                              # cache never held more
    # arrays alive anywhere (cache + images being read/analysed right now)
    in_flight = QThreadPool.globalInstance().maxThreadCount() + 1
    assert peak[0] <= cap + in_flight, (peak[0], cap, in_flight)
    # label maps / overlays: only the most recently used images stay full size
    qtbot.waitUntil(lambda: st.held_array_count() <= app_state.RESULT_CACHE_MAX_IMAGES,
                    timeout=10000)
    one = max(im.raw.label_image.nbytes * 3 for im in st.images()
              if im.raw is not None and im.raw.label_image is not None)
    assert st.held_array_bytes() < one * (app_state.RESULT_CACHE_MAX_IMAGES + 60),         st.held_array_bytes()
    # an evicted image comes back from disk when it is shown again
    far = next(im for im in st.images() if im.image_bgr is None)
    st.set_current_image(far.uid)
    qtbot.waitUntil(lambda: far.image_bgr is not None and a.canvas.has_image(), timeout=10000)
    shell.close()


def test_results_table_columns_widths_and_hiding(env, qtbot):
    from ui.pages.results_table import COL_G, COL_IMAGE, COL_SCAN
    shell = _open(qtbot, make_session(env, 2))
    t = shell.analyze.table
    hdr = t.tree.header()
    assert hdr.stretchLastSection()
    assert hdr.logicalIndex(hdr.count() - 1) == COL_SCAN        # details last
    assert hdr.visualIndex(COL_G) < hdr.visualIndex(COL_SCAN)   # results first
    shell.analyze.show_table_view()
    shell.resize(1400, 900)
    qtbot.wait(100)
    from ui.pages.results_table import COL_PROJECT
    assert t.tree.isColumnHidden(COL_PROJECT)                  # one job only: redundant
    total = sum(hdr.sectionSize(c) for c in range(hdr.count()) if not t.tree.isColumnHidden(c))
    assert total <= t.tree.viewport().width() + 2               # no horizontal scrolling
    t.set_column_visible(COL_SCAN, False)
    t.set_column_visible(COL_IMAGE, False)                      # never hidden
    assert t.tree.isColumnHidden(COL_SCAN) and not t.tree.isColumnHidden(COL_IMAGE)
    assert shell.state.ui_state["results_table_hidden"] == [COL_SCAN]
    t.set_column_visible(COL_PROJECT, True)                     # the user wants it anyway
    assert not t.tree.isColumnHidden(COL_PROJECT)
    shell.close()
    shell2 = _open(qtbot, make_session(env, 1, label="Again"))
    assert shell2.analyze.table.tree.isColumnHidden(COL_SCAN)   # remembered
    shell2.close()
