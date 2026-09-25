"""
Analyze → autosave → reload (UI-04 / DATA-08), Review delete + undo, the
per-image comparison table, and the non-destructive grain filters
(border / false-grain toggles, manual removal, persistence).
"""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from data.models import AppSettings  # noqa: E402
from data.session_io import load_session  # noqa: E402
from data.settings import save_settings  # noqa: E402
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


def _open_shell(qtbot, path: Path):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1400, 900)
    shell.show()
    shell.open_session(path)
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    return shell


def _analyse_all(shell, qtbot):
    st = shell.state
    shell.analyze.params.set_mode("threshold")
    confirm_setup(shell, qtbot)            # UX-02: scan area + scale confirmed first
    with qtbot.waitSignal(shell.analyze.queue.queue_finished, timeout=TIMEOUT):
        shell.analyze_all()
    qtbot.waitUntil(lambda: all(im.status == "done" and im.result is not None
                                for im in st.images()) and not st.is_filtering(),
                    timeout=TIMEOUT)


def _settle(shell, qtbot):
    st = shell.state
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=TIMEOUT)
    st.flush()
    qtbot.waitUntil(lambda: st.save_state == "saved" and not st.is_dirty(), timeout=TIMEOUT)


@pytest.fixture
def analysed(env, qtbot):
    path = make_session(env, 2, label="Run", black=True)
    shell = _open_shell(qtbot, path)
    _analyse_all(shell, qtbot)
    yield shell, path
    shell.close()


def test_worker_returns_full_frame_result_with_scan_area(qapp, tmp_path):
    import cv2
    from core.grain_detector import DetectionParams
    from tests.ui_shell_helpers import mosaic_png
    from ui.workers import analyze_image
    bgr = cv2.imread(mosaic_png(tmp_path / "a.png"))
    r = analyze_image(bgr, 0.0, DetectionParams(detection_mode="threshold"), (20, 30, 150, 140))
    assert r.label_image.shape == bgr.shape[:2]
    assert not r.valid_mask[:30].any() and not r.valid_mask[:, :20].any()   # outside scan = not analysed
    assert r.label_image[:30].max() == 0
    for g in r.grains:                         # centroids/bboxes in full-frame coordinates
        assert 20 <= g.centroid_x <= 170 and 30 <= g.centroid_y <= 170
        assert g.bbox[0] >= 30 and g.bbox[1] >= 20


def test_analyze_two_images_autosaves_and_reloads(analysed, qtbot):
    shell, path = analysed
    st = shell.state
    counts = [im.result.grain_count for im in st.images()]
    assert all(c > 5 for c in counts)
    _settle(shell, qtbot)
    ls = load_session(path)
    assert [si.entry.has_result for si in ls.images] == [True, True]
    assert [si.result.grain_count for si in ls.images] == counts
    assert ls.manifest.detection_params.get("detection_mode") == "threshold"
    # the whole session re-opens with results in a fresh state
    from ui.app_state import AppState
    st2 = AppState()
    st2.open_session(path)
    qtbot.waitUntil(lambda: st2.session is not None, timeout=15000)
    assert [im.result.grain_count for im in st2.images()] == counts
    assert all(im.status == "done" for im in st2.images())


def test_comparison_table_has_one_row_per_image(analysed):
    shell, _ = analysed
    shell.go("review")
    assert shell.review.comparison_rows() == len(shell.state.images()) == 2
    assert shell.review.cmp.item(0, 1).text() == "Analysed"


def test_review_delete_and_undo(analysed, qtbot):
    shell, _ = analysed
    st = shell.state
    shell.go("review")
    im = st.current_image()
    n0 = im.result.grain_count
    gid = im.result.grains[0].grain_id
    shell.review.canvas.select([gid])
    assert shell.review.canvas.selected() == [gid]
    shell.review.delete_selected()
    qtbot.waitUntil(lambda: not st.is_filtering() and im.result.grain_count == n0 - 1,
                    timeout=TIMEOUT)
    assert gid in im.manual and "manual" in im.excluded[gid]
    assert gid not in {g.grain_id for g in im.result.grains}
    assert gid in {g.grain_id for g in im.raw.grains}          # raw untouched
    st.undo_stack.undo()
    qtbot.waitUntil(lambda: not st.is_filtering() and im.result.grain_count == n0,
                    timeout=TIMEOUT)
    assert gid not in im.manual and gid not in im.excluded
    st.undo_stack.redo()
    qtbot.waitUntil(lambda: not st.is_filtering() and im.result.grain_count == n0 - 1,
                    timeout=TIMEOUT)


def test_border_and_false_grain_toggles_change_counts_and_restore(analysed, qtbot):
    shell, _ = analysed
    st = shell.state
    im = st.current_image()
    base = im.result.grain_count
    assert not st.filter_options().exclude_border          # no scan area -> default off

    card = shell.review.filters
    shell.go("review")
    card.border.switch.setChecked(True)
    card._emit()
    qtbot.waitUntil(lambda: not st.is_filtering() and im.counts.get("border", 0) > 0, timeout=TIMEOUT)
    n_border = im.result.grain_count
    assert n_border == base - im.counts["border"] < base
    assert card.border.badge.text().startswith("−")

    card.false.switch.setChecked(True)            # image has a black void -> touching grains
    card._emit()
    qtbot.waitUntil(lambda: not st.is_filtering() and im.counts.get("touching_invalid", 0) > 0,
                    timeout=TIMEOUT)
    assert im.result.grain_count < n_border

    card.border.switch.setChecked(False)
    card.false.switch.setChecked(False)
    card._emit()
    qtbot.waitUntil(lambda: not st.is_filtering() and not im.excluded, timeout=TIMEOUT)
    assert im.result.grain_count == base                   # restored exactly
    # applies to every image of the session (session scope)
    assert all(not x.excluded for x in st.images())


def test_filters_and_manual_removal_persist_across_reload(analysed, qtbot):
    shell, path = analysed
    st = shell.state
    im = st.current_image()
    raw_n = len(im.raw.grains)
    opts = st.filter_options()
    opts.exclude_border = True
    st.set_filter_options(opts)
    qtbot.waitUntil(lambda: not st.is_filtering() and im.counts.get("border", 0) > 0, timeout=TIMEOUT)
    victim = im.result.grains[0].grain_id
    st.delete_grains(im.uid, [victim])
    qtbot.waitUntil(lambda: not st.is_filtering() and victim in im.manual
                    and victim not in {g.grain_id for g in im.result.grains}, timeout=TIMEOUT)
    kept = im.result.grain_count
    _settle(shell, qtbot)

    from ui.app_state import AppState
    st2 = AppState()
    st2.open_session(path)
    qtbot.waitUntil(lambda: st2.session is not None, timeout=15000)
    im2 = st2.images()[0]
    assert st2.session.filters.exclude_border is True
    assert victim in im2.manual
    assert im2.result.grain_count == kept
    assert len(im2.raw.grains) == raw_n                    # excluded grains re-measured
    # switching the border filter off after reload restores those grains
    o = st2.filter_options()
    o.exclude_border = False
    st2.set_filter_options(o)
    qtbot.waitUntil(lambda: not st2.is_filtering() and "border" not in str(im2.counts),
                    timeout=TIMEOUT)
    assert im2.result.grain_count == raw_n - 1             # only the manual removal remains
    st2.flush()


def test_scan_area_turns_border_filter_on_by_default(env, qtbot):
    path = make_session(env, 1, label="Scan")
    shell = _open_shell(qtbot, path)
    st = shell.state
    st.set_scan_rect((10, 10, 180, 180))
    assert st.filter_options().exclude_border is True       # v2.3 behaviour, now a visible toggle
    st.set_scan_rect(None)
    assert st.filter_options().exclude_border is False
    shell.close()


def test_close_window_mid_analysis_exits_cleanly(env, qtbot, capfd):
    """Closing while analysis and background tasks run must not raise
    'Signal source has been deleted' from a pool thread."""
    from ui.workers import pending_tasks
    path = make_session(env, n=3)
    shell = _open_shell(qtbot, path)
    confirm_setup(shell, qtbot)
    shell.analyze.analyze_all()
    qtbot.waitUntil(lambda: shell.analyze.queue.is_running(), timeout=10000)
    shell.close()
    qtbot.wait(300)
    err = capfd.readouterr().err
    assert "Signal source has been deleted" not in err
    assert "Error calling Python override" not in err
    assert not shell.analyze.queue.is_running()
    assert pending_tasks() >= 0
