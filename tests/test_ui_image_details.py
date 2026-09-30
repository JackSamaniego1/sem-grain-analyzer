"""UPDATE 4 item 11 (UI): instrument / magnification / kV / WD on image load.

The OCR (core.info_bar_ocr.read_info_bar) is mocked; core.image_info is
real.  Checked: metadata first, data-bar read in the background one image at
a time, "Please check" treatment, saved with the lot (not read again on
reopen), session fields typed by the operator never overwritten, one OCR per
image shared with Auto-find.
"""
from __future__ import annotations

import json
import os
import threading
import time

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import info_bar_ocr  # noqa: E402
from core.image_info import ImageInfo  # noqa: E402
from core.info_bar_ocr import InfoBarReading, ReadingField  # noqa: E402
from data.models import AppSettings  # noqa: E402
from data.settings import save_settings  # noqa: E402

TIMEOUT = 30000


def _reading(mag=20000.0, kv=5.0, wd=10.1, check_mag=False, label=None):
    r = InfoBarReading(status="ok",
                       magnification=ReadingField(mag, "x", 0.95, check_mag),
                       hv=ReadingField(kv, "kV", 0.95, False),
                       wd=ReadingField(wd, "mm", 0.95, False),
                       instrument=ReadingField("JSM-7800F", "", 0.95, False),
                       vendor=ReadingField("JEOL", "", 0.95, False),
                       detector=ReadingField("SE", "", 0.95, False))
    if label:
        r.scale = ReadingField(label[0], label[1], 0.95, False)
        r.scale_bar_px = 120
    return r


class FakeOcr:
    def __init__(self, reading, delay=0.0):
        self.reading, self.delay = reading, delay
        self.calls, self.threads = [], set()
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, image, scale_bar_bbox=None, *, info_bar=None, metadata=None):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(scale_bar_bbox)
            self.threads.add(threading.get_ident())
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return self.reading() if callable(self.reading) else self.reading


@pytest.fixture
def fake_ocr(monkeypatch):
    def install(reading, delay=0.0):
        f = FakeOcr(reading, delay)
        monkeypatch.setattr(info_bar_ocr, "read_info_bar", f)
        return f
    return install


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    import core.ai_device as aid
    from ui.ai_probe import reset_device_probe
    from ui.design.theme import apply_theme, set_reduced_motion
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: aid.AiDeviceInfo())
    reset_device_probe()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)
    reset_device_probe()


def _pngs(folder, n, seed=3):
    from tests.conftest import make_mosaic
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        g, _ = make_mosaic(h=160, w=200, n_grains=20, seed=seed + i)
        p = folder / f"new_{i}.png"
        cv2.imwrite(str(p), np.repeat(g[:, :, None], 3, axis=2))
        out.append(str(p))
    return out


def _open_state(qtbot, root, n=1):
    from tests.ui_shell_helpers import make_session
    from ui.app_state import AppState
    st = AppState()
    path = make_session(root, n)
    st.open_session(path)
    qtbot.waitUntil(lambda: st.session is not None, timeout=TIMEOUT)
    return st, path


def _idle(st):
    svc = st.image_details
    return not svc._pending and svc._running is None and not svc._queue


# ---------------------------------------------------------------- helpers (no Qt)
def test_summary_check_and_label_helpers():
    from ui import image_details as idt
    info = ImageInfo()
    info._set("instrument", "JEOL JSM-7800F", "metadata")
    info._set("magnification", 20000.0, "info_bar", True, "read with low confidence")
    info._set("accelerating_voltage_kv", 5.0, "metadata")
    info._set("scale_label", 100.0, "info_bar")
    info.scale_label_unit, info.scale_bar_px = "nm", 120
    assert idt.summary_text(info) == "JEOL JSM-7800F  ·  20,000×  ·  5 kV"
    assert idt.checked_fields(info) == ["magnification"]
    assert "magnification (read with low confidence)" in idt.check_text(info)
    assert idt.source_key(info) == "both"
    vals = idt.acquisition_values(info)
    assert vals == {"instrument": "JEOL JSM-7800F", "accelerating_voltage_kv": 5.0}
    rd = idt.label_reading(info)
    assert rd["um"] == pytest.approx(0.1) and rd["unit"] == "nm" and rd["bar_px"] == 120
    assert idt.needs_ocr(info)                              # WD still empty, OCR not run
    info.ocr_status = "ok"
    assert not idt.needs_ocr(info)
    assert "http" not in idt.tooltip_text(info)


def test_saved_details_roundtrip_and_merge(tmp_path):
    from ui import image_details as idt
    info = ImageInfo()
    info._set("magnification", 5000.0, "metadata")
    idt.save_infos(tmp_path, {"a.png": info.to_dict()})
    idt.save_infos(tmp_path, {"b.png": ImageInfo().to_dict()})
    saved = idt.load_saved(tmp_path)
    assert set(saved) == {"a.png", "b.png"}
    assert ImageInfo.from_dict(saved["a.png"]).magnification == 5000.0
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    (tmp_path / idt.IMAGE_INFO_FILE).write_text("{broken", encoding="utf-8")
    assert idt.load_saved(tmp_path) == {}


# ---------------------------------------------------------------- AppState
def test_opening_a_lot_reads_metadata_only(env, qtbot, fake_ocr):
    f = fake_ocr(_reading())
    st, _ = _open_state(qtbot, env, 2)
    qtbot.waitUntil(lambda: all(im.image_info is not None for im in st.images()),
                    timeout=TIMEOUT)
    for im in st.images():
        assert im.image_info.ocr_status == "not_run"         # PNG: nothing in the file
    time.sleep(0.2)
    assert not f.calls                                       # no OCR storm on open
    st.close_session()


def test_added_images_read_in_background_one_at_a_time(env, qtbot, fake_ocr):
    f = fake_ocr(_reading(check_mag=True), delay=0.05)
    st, path = _open_state(qtbot, env, 1)
    got = []
    st.image_details.ready.connect(got.append)
    t0 = time.perf_counter()
    st.add_images(_pngs(env.parent / "src_new", 6))
    assert time.perf_counter() - t0 < 1.0                   # GUI never waits
    qtbot.waitUntil(lambda: len(st.images()) == 7, timeout=TIMEOUT)
    new = [im for im in st.images() if im.filename.startswith("new_")
           or "new_" in (im.original_name or "")]
    assert len(new) == 6
    qtbot.waitUntil(lambda: all(im.image_info is not None and im.image_info.ocr_status == "ok"
                                for im in new) and _idle(st), timeout=TIMEOUT)
    assert len(f.calls) == 6 and f.peak == 1                 # one image at a time
    assert threading.get_ident() not in f.threads
    info = new[0].image_info
    assert info.instrument == "JEOL JSM-7800F" and info.accelerating_voltage_kv == 5.0
    assert info.needs_check["magnification"]
    # session fields: filled where empty; the flagged magnification is not
    m = st.session.meta
    assert m.instrument == "JEOL JSM-7800F" and m.accelerating_voltage_kv == 5.0
    assert m.working_distance_mm == pytest.approx(10.1) and m.magnification == ""
    # saved with the lot
    st.flush()
    from ui.image_details import IMAGE_INFO_FILE
    saved = json.loads((path / IMAGE_INFO_FILE).read_text(encoding="utf-8"))["images"]
    assert {im.filename for im in new} <= set(saved)
    st.close_session()
    # reopen: nothing is read again
    n = len(f.calls)
    st.open_session(path)
    qtbot.waitUntil(lambda: st.session is not None and all(
        im.image_info is not None for im in st.images()
        if im.filename in saved), timeout=TIMEOUT)
    time.sleep(0.2)
    assert len(f.calls) == n
    again = next(im for im in st.images() if im.filename == new[0].filename)
    assert again.image_info.magnification == 20000.0 and again.image_info.ocr_status == "ok"
    st.close_session()


def test_typed_session_values_are_never_overwritten(env, qtbot, fake_ocr):
    fake_ocr(_reading())
    st, _ = _open_state(qtbot, env, 1)
    st.session.meta.instrument = "Our Zeiss"
    st.session.meta.magnification = "1000×"
    st.add_images(_pngs(env.parent / "src_typed", 1))
    qtbot.waitUntil(lambda: len(st.images()) == 2, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: _idle(st) and any(
        im.image_info is not None and im.image_info.ocr_status == "ok"
        for im in st.images()), timeout=TIMEOUT)
    assert st.session.meta.instrument == "Our Zeiss"
    assert st.session.meta.magnification == "1000×"
    assert st.session.meta.accelerating_voltage_kv == 5.0      # was empty
    st.close_session()


def test_late_results_after_close_are_dropped(env, qtbot, fake_ocr):
    fake_ocr(_reading(), delay=0.3)
    st, _ = _open_state(qtbot, env, 1)
    st.add_images(_pngs(env.parent / "src_late", 2))
    qtbot.waitUntil(lambda: len(st.images()) == 3, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: st.image_details._running is not None, timeout=TIMEOUT)
    doc = st.session
    st.close_session()
    from ui.workers import ocr_pool
    ocr_pool().waitForDone(5000)
    qtbot.wait(50)
    assert st.session is None and st.image_details._running is None
    assert doc is not None


# ---------------------------------------------------------------- shared with Auto-find
def _auto_find(shell, qtbot):
    st = shell.state
    shell.analyze.setup_tile.btn_auto.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)


def _bar_shell(qtbot, root):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_ui_scale_label_ocr import _bar_session
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1500, 950)
    shell.show()
    shell.open_session(_bar_session(root, 1), prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None
                    and not shell.state.is_loading(), timeout=TIMEOUT)
    qtbot.waitUntil(lambda: _idle(shell.state), timeout=TIMEOUT)
    return shell


def test_auto_find_reading_fills_the_details_once(env, qtbot, fake_ocr):
    f = fake_ocr(_reading(label=(20.0, "µm")))
    shell = _bar_shell(qtbot, env)
    st, tile = shell.state, shell.analyze.setup_tile
    im = st.current_image()
    assert im.image_info.ocr_status == "not_run" and not f.calls
    assert "Auto-find" in tile.details_val.text()
    _auto_find(shell, qtbot)
    assert len(f.calls) == 1                                 # one reading for both
    assert im.image_info.ocr_status == "ok" and im.image_info.magnification == 20000.0
    assert im.bar_um == pytest.approx(20.0)
    assert "20,000×" in tile.details_val.text() and "5 kV" in tile.details_val.text()
    assert not tile.details_check.isVisibleTo(tile)
    shell.close()


def test_details_label_reused_by_auto_find(env, qtbot, fake_ocr):
    f = fake_ocr(_reading(label=(20.0, "µm"), check_mag=True))
    shell = _bar_shell(qtbot, env)
    st, tile = shell.state, shell.analyze.setup_tile
    im = st.current_image()
    # as if the image had just been added: the background reading ran
    st.image_details.request(st.session, [im], ocr=True)
    qtbot.waitUntil(lambda: im.image_info.ocr_status == "ok" and _idle(st), timeout=TIMEOUT)
    assert len(f.calls) == 1
    # "Please check" like the scale-bar label
    assert tile.details_check.isVisibleTo(tile)
    assert tile.details_check.text() == "Please check"
    assert "magnification" in tile.details_hint.text()
    assert tile.details_hint.property("tone") == "warning"
    _auto_find(shell, qtbot)
    assert len(f.calls) == 1                                 # label re-used, not read again
    assert im.bar_read["um"] == pytest.approx(20.0) and im.bar_um == pytest.approx(20.0)
    shell.close()


def test_missing_reader_says_reinstall_without_link(env, qtbot, fake_ocr):
    fake_ocr(InfoBarReading(status="engine_missing", available=False,
                            message=info_bar_ocr.REINSTALL_MESSAGE))
    shell = _bar_shell(qtbot, env)
    st, tile = shell.state, shell.analyze.setup_tile
    im = st.current_image()
    st.image_details.request(st.session, [im], ocr=True)
    qtbot.waitUntil(lambda: im.image_info.ocr_status == "engine_missing", timeout=TIMEOUT)
    hint = tile.details_hint.text()
    assert "reinstall" in hint.lower() and "http" not in hint and "download" not in hint.lower()
    shell.close()
