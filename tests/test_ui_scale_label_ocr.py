"""UPDATE 4 item 4 (UI wiring): Auto-find also reads the scale-bar label.

The OCR itself (core.info_bar_ocr) is mocked: these tests check the UI
behaviour -- worker thread, pre-fill of number + unit, "please check" hint,
hand-typed / hand-set values never overwritten, reinstall message when the
text reader is missing, stale results dropped.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import info_bar_ocr  # noqa: E402
from core.info_bar_ocr import InfoBarReading, ReadingField  # noqa: E402
from data.models import AppSettings, ImageEntry  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.conftest import make_mosaic  # noqa: E402

TIMEOUT = 60000


# ---------------------------------------------------------------- helpers
def _reading(value=20.0, unit="µm", confirm=False, bar_px=120, status="ok",
             meta_ok=None, note=""):
    if status != "ok":
        return InfoBarReading(status=status, available=status != "engine_missing",
                              message=info_bar_ocr.REINSTALL_MESSAGE
                              if status == "engine_missing" else "")
    r = InfoBarReading(status="ok", scale=ReadingField(value, unit, 0.95, confirm, note=note),
                       scale_bar_px=bar_px, image_width_px=480)
    if meta_ok is not None:
        r.checks["metadata"] = meta_ok
    return r


class FakeOcr:
    """Stands in for core.info_bar_ocr.read_info_bar; records its calls and
    the thread they ran on."""

    def __init__(self, reading):
        self.reading = reading
        self.calls = []
        self.threads = set()

    def __call__(self, image, scale_bar_bbox=None, *, info_bar=None, metadata=None):
        self.calls.append(dict(bbox=scale_bar_bbox, metadata=metadata))
        self.threads.add(threading.get_ident())
        r = self.reading() if callable(self.reading) else self.reading
        return r


@pytest.fixture
def fake_ocr(monkeypatch):
    def install(reading):
        f = FakeOcr(reading)
        monkeypatch.setattr(info_bar_ocr, "read_info_bar", f)
        return f
    return install


def _bar_gray(seed: int, scale_len: int = 120):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_infobar import add_bar
    g, _ = make_mosaic(h=360, w=480, n_grains=50, seed=seed)
    g, _r, _s = add_bar(g, frac=0.12, scale_len=scale_len, seed=seed)
    return g


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.ai_probe import reset_device_probe
    from ui.design.theme import apply_theme, set_reduced_motion
    import core.ai_device as aid
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: aid.AiDeviceInfo(
        torch_available=True, cpu_available=True, gpu_reason=aid.REASON_NO_GPU_BUILD))
    reset_device_probe()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)
    reset_device_probe()


def _bar_session(root: Path, n: int = 2) -> Path:
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(root)
    pp = ws.create_project("Proj")
    sp = ws.create_sample(pp, "S-1")
    lp = ws.create_lot(pp, sp, "L-1")
    src = root.parent / "src"
    src.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = src / f"bar_{i}.png"
        g = _bar_gray(5 + i)
        cv2.imwrite(str(p), np.repeat(g[:, :, None], 3, axis=2))
        paths.append(str(p))
    ref = save_session(lp, {"operator": "Tester"}, [ImageEntry(source_path=p) for p in paths],
                       label="Run", catalog=Catalog(root))
    return Path(ref.path)


def _open(qtbot, root, n=2):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1500, 950)
    shell.show()
    shell.open_session(_bar_session(root, n), prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    qtbot.waitUntil(lambda: not shell.state.is_loading(), timeout=TIMEOUT)
    return shell


def _auto_find(shell, qtbot):
    st = shell.state
    shell.analyze.auto_find()          # both wizard steps at once
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)


# ---------------------------------------------------------------- worker level
def test_setup_probe_reads_label_only_when_asked_and_bar_present(fake_ocr):
    from ui.app_state import setup_probe
    f = fake_ocr(_reading(100.0, "nm", confirm=False, bar_px=120))
    bgr = np.repeat(_bar_gray(5)[:, :, None], 3, axis=2)
    out = setup_probe(bgr, None, False, True, ("2.0", "Zeiss", "high"))
    rd = out["ocr"]
    assert rd["value"] == 100.0 and rd["unit"] == "nm" and rd["um"] == pytest.approx(0.1)
    assert rd["confirm"] is False and rd["status"] == "ok"
    # the same scale bar the line finder found is handed to the OCR, and the
    # file's calibration for the cross-check
    assert f.calls[0]["bbox"] is not None and f.calls[0]["bbox"][2] == pytest.approx(120, abs=3)
    assert f.calls[0]["metadata"].px_per_um == pytest.approx(2.0)
    # not asked (scale set by hand) -> no OCR
    assert "ocr" not in setup_probe(bgr, None, False, False)
    # no data bar on the image -> no OCR
    plain, _ = make_mosaic(h=224, w=224, n_grains=30, seed=3)
    assert "ocr" not in setup_probe(np.repeat(plain[:, :, None], 3, axis=2), None, False, True)
    assert len(f.calls) == 1


def test_setup_probe_skips_ocr_while_app_closes(fake_ocr, monkeypatch):
    import ui.workers as workers
    from ui.app_state import setup_probe
    f = fake_ocr(_reading())
    monkeypatch.setattr(workers, "_SHUTTING_DOWN", True)
    out = setup_probe(np.repeat(_bar_gray(5)[:, :, None], 3, axis=2), None, False, True)
    assert "ocr" not in out and not f.calls and out["bar_px"] > 0


def test_read_scale_label_never_raises(monkeypatch):
    from ui.app_state import read_scale_label

    def boom(*a, **k):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(info_bar_ocr, "read_info_bar", boom)
    rd = read_scale_label(np.zeros((50, 50, 3), np.uint8))
    assert rd["status"] == "error" and rd["um"] == 0.0


# ---------------------------------------------------------------- Analyze page
def test_confirmed_label_fills_number_unit_and_scale_off_the_gui_thread(env, qtbot, fake_ocr):
    f = fake_ocr(_reading(20.0, "µm", confirm=False, bar_px=120))
    shell = _open(qtbot, env)
    st, tile = shell.state, shell.analyze.scale_row
    _auto_find(shell, qtbot)
    assert f.calls and threading.get_ident() not in f.threads      # worker thread only
    for im in st.images():
        assert im.bar_um == pytest.approx(20.0) and im.scale_source == "label"
        assert st.px_for(im) == pytest.approx(im.bar_px / 20.0)
        assert st.setup_ready(im)
    # batch 4 follow-up: applied, so nothing to enter -- the strip shows it
    assert not tile.bar_row.isVisibleTo(tile)
    strip = shell.analyze.setup_tile
    im0 = st.current_image()
    assert strip.scale_val.text().startswith(
        f"Scale bar: 20 µm · {im0.bar_px:.0f} px → {im0.bar_px / 20.0:.4g} px/µm")
    assert strip.scale_src.text() == "Read from image"
    shell.close()


def test_unconfirmed_label_is_applied_and_shown_for_checking(env, qtbot, fake_ocr):
    """Batch 4 follow-up (user decision; replaces "unsure label prefilled
    and not applied"): a label read successfully is used at once, even when
    nothing in the file can double-check it.  The strip under the image
    shows the length read; Edit… / typing corrects a wrong reading."""
    fake_ocr(_reading(500.0, "nm", confirm=True, bar_px=120))
    shell = _open(qtbot, env)
    st, a = shell.state, shell.analyze
    _auto_find(shell, qtbot)
    for im in st.images():
        assert st.setup_issues(im) == []
        assert im.bar_read["um"] == pytest.approx(0.5) and im.scale_source == "label"
        assert st.px_for(im) == pytest.approx(im.bar_px / 0.5)
    assert not a.scale_row.isVisibleTo(a)
    assert a.setup_tile.scale_val.text().startswith("Scale bar: 500 nm · 120 px → 240 px/µm")
    assert a.setup_tile.scale_src.text() == "Read from image"
    assert a.canvas.scale_bar_rect() is not None                   # bar stays highlighted
    assert not a.btn_scale_edit.isHidden()                         # the way to correct it
    # correcting it by hand wins and says so
    im0 = st.current_image()
    st.set_bar_length(im0.uid, 1.0, same_bar=False)
    assert im0.scale_source == "manual" and st.px_for(im0) == pytest.approx(120.0)
    shell.close()


def test_value_set_by_hand_is_never_overwritten(env, qtbot, fake_ocr):
    f = fake_ocr(_reading(50.0, "µm", confirm=False, bar_px=120))
    shell = _open(qtbot, env)
    st, tile = shell.state, shell.analyze.scale_row
    _auto_find(shell, qtbot)
    im0 = st.current_image()
    st.set_bar_length(im0.uid, 20.0, same_bar=False)             # typed + Apply
    assert im0.scale_source == "manual"
    n_calls = len(f.calls)
    _auto_find(shell, qtbot)
    assert im0.bar_um == pytest.approx(20.0) and im0.scale_source == "manual"
    assert st.px_for(im0) == pytest.approx(im0.bar_px / 20.0)
    # that image's label is not even read again
    assert len(f.calls) == n_calls + (len(st.images()) - 1)
    shell.close()


def test_length_being_typed_survives_a_label_read(env, qtbot, fake_ocr):
    # the row shows when the label disagrees with the file's metadata
    fake_ocr(_reading(500.0, "nm", confirm=True, bar_px=120, meta_ok=False))
    shell = _open(qtbot, env)
    st, tile = shell.state, shell.analyze.scale_row
    for im in st.images():
        im.cal_suggestion = (3.0, "Zeiss", "high")
    _auto_find(shell, qtbot)
    tile.bar_unit.setCurrentText("µm")                            # operator starts typing
    tile.bar_len.setValue(7.0)
    assert tile.is_typing()
    _auto_find(shell, qtbot)                                      # read again: 500 nm
    assert tile.bar_len.value() == pytest.approx(7.0) and tile.bar_unit.currentText() == "µm"
    # another image and back: typing is not carried to other images
    other = [im for im in st.images() if im.uid != st.current_uid][0]
    st.set_current_image(other.uid)
    qtbot.waitUntil(lambda: tile.bar_len.value() == pytest.approx(500.0), timeout=5000)
    assert not tile.is_typing()
    shell.close()


def test_missing_text_reader_says_reinstall_quietly(env, qtbot, fake_ocr, monkeypatch):
    fake_ocr(_reading(status="engine_missing"))
    shell = _open(qtbot, env)
    shown = []
    monkeypatch.setattr(shell.analyze, "_toast", lambda *a: shown.append(a))
    st, tile = shell.state, shell.analyze.scale_row
    _auto_find(shell, qtbot)
    assert all(st.setup_issues(im) == ["scale"] for im in st.images())
    assert tile.bar_len.value() == 0.0                             # left for the operator
    hint = tile.bar_hint.text()
    assert "reinstall" in hint.lower() and tile.bar_hint.isVisibleTo(tile)
    for text in [hint] + [" ".join(map(str, a)) for a in shown]:
        assert "http" not in text and "download" not in text.lower()
    assert not tile.bar_check.isVisibleTo(tile)
    shell.close()


def test_nothing_read_leaves_the_box_as_before(env, qtbot, fake_ocr):
    fake_ocr(InfoBarReading(status="no_text"))
    shell = _open(qtbot, env)
    tile = shell.analyze.scale_row
    _auto_find(shell, qtbot)
    assert tile.bar_row.isVisibleTo(tile) and tile.bar_len.value() == 0.0
    assert not tile.bar_hint.isVisibleTo(tile) and not tile.bar_check.isVisibleTo(tile)
    shell.close()


def test_label_disagreeing_with_file_metadata_asks_to_check(env, qtbot, fake_ocr):
    f = fake_ocr(_reading(100.0, "µm", confirm=True, bar_px=120, meta_ok=False,
                          note="disagrees with the file metadata"))
    shell = _open(qtbot, env)
    st, tile = shell.state, shell.analyze.scale_row
    for im in st.images():
        im.cal_suggestion = (3.0, "Zeiss", "high")                # file says 3 px/µm
    _auto_find(shell, qtbot)
    assert f.calls[0]["metadata"].px_per_um == pytest.approx(3.0)
    im0 = st.current_image()
    assert im0.scale_source == "metadata" and st.px_for(im0) == pytest.approx(3.0)
    assert tile.bar_row.isVisibleTo(tile)                         # shown despite metadata
    assert tile.bar_len.value() == pytest.approx(100.0)
    assert tile.bar_check.isVisibleTo(tile)
    assert "stored in the image file" in tile.bar_hint.text()
    shell.close()


def test_results_are_per_image_and_dropped_after_the_session_closes(env, qtbot, fake_ocr):
    readings = iter([_reading(20.0, "µm", True), _reading(5.0, "µm", True)])
    fake_ocr(lambda: next(readings))
    shell = _open(qtbot, env)
    st, tile = shell.state, shell.analyze.scale_row
    _auto_find(shell, qtbot)
    a, b = st.images()
    got = {a.uid: a.bar_read["um"], b.uid: b.bar_read["um"]}
    assert sorted(got.values()) == [5.0, 20.0]
    strip = shell.analyze.setup_tile
    for im in (a, b):                                             # strip follows the image
        st.set_current_image(im.uid)
        qtbot.waitUntil(lambda im=im: strip.scale_val.text().startswith(
            f"Scale bar: {got[im.uid]:g} µm"), timeout=5000)
    # a read that arrives after the session was closed is dropped, no crash
    doc = st.session
    late = {"shape": a.shape, "bar_px": 120.0, "info": {},
            "ocr": {"status": "ok", "um": 9.0, "value": 9.0, "unit": "µm", "confirm": False}}
    st._setup_pending.add(a.uid)
    st.close_session()
    st._apply_setup(doc, a, late)
    assert a.bar_um != pytest.approx(9.0) and a.bar_read["um"] != pytest.approx(9.0)
    shell.close()
