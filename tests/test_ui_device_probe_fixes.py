"""Review fixes for commit 455e548 (UPDATE 4 items 4 / 10b UI).

a. the GPU check finishing and changing the effective device emits
   ``ParamPanel.changed`` so the session's ``sam_device`` follows;
b. ``setup_probe`` tolerates a scale-bar dict without a "rect" key;
c. the delayed start-up GPU check does nothing once the app is closing.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core.ai_device as aid  # noqa: E402
from core.ai_device import AiDeviceInfo  # noqa: E402

GPU = AiDeviceInfo(torch_available=True, cpu_available=True, gpu_available=True,
                   gpu_name="NVIDIA RTX A2000", vram_total_mb=6144, gpu_reason="")
NO_CARD = AiDeviceInfo(torch_available=True, cpu_available=True, torch_version="2",
                       cuda_version="12.1", gpu_reason=aid.REASON_NO_CARD)


@pytest.fixture
def ap(monkeypatch, qapp, tmp_path):
    import ui.detection_modes as dm
    import ui.pages.analyze_page as ap
    from ui.ai_probe import reset_device_probe
    monkeypatch.setattr(dm, "sam_model_available", lambda: True)
    monkeypatch.setattr(ap, "sam_model_available", lambda: True)
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: GPU)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    reset_device_probe()
    yield ap
    reset_device_probe()


def _panel(ap, qtbot, pref=""):
    from ui.ai_probe import AiDeviceProbe
    probe = AiDeviceProbe()
    p = ap.ParamPanel(probe=probe)
    qtbot.addWidget(p)
    p.set_device_preference(pref)
    return p, probe


def test_probe_answer_changing_device_emits_changed(ap, qtbot):
    p, probe = _panel(ap, qtbot)
    assert p.sam_device() == "cpu"                        # still checking, no preference
    got = []
    p.changed.connect(lambda: got.append(p.get_params().sam_device))
    probe.set_info(GPU)
    assert got == ["gpu"]


def test_probe_answer_without_device_change_is_quiet(ap, qtbot):
    p, probe = _panel(ap, qtbot)
    got = []
    p.changed.connect(lambda: got.append(1))
    probe.set_info(NO_CARD)                               # cpu -> cpu
    assert got == []


def test_setup_probe_tolerates_bar_without_rect(monkeypatch):
    import core.infobar as infobar
    import core.scale_bar as sb
    import ui.app_state as st

    class IB:
        bars = [1]

        def to_dict(self):
            return {"bars": 1}
    monkeypatch.setattr(infobar, "detect_info_bar", lambda img: IB())
    monkeypatch.setattr(sb, "find_scale_bar_line", lambda img, info_bar=None: {"length_px": 120})
    from core import info_bar_ocr
    from core.info_bar_ocr import InfoBarReading
    seen = []

    def fake(image, bbox=None, *, info_bar=None, metadata=None):
        seen.append(bbox)
        return InfoBarReading(status="no_text")
    monkeypatch.setattr(info_bar_ocr, "read_info_bar", fake)
    out = st.setup_probe(np.zeros((60, 80, 3), np.uint8), None, False, True)
    assert out["bar_px"] == 120.0 and out["info"] == {"bars": 1}
    assert seen == [None] and out["ocr"]["status"] == "no_text"


def test_startup_gpu_check_skipped_when_closing(ap, qtbot, monkeypatch):
    p, probe = _panel(ap, qtbot)
    started = []
    monkeypatch.setattr(probe, "start", lambda *a, **k: started.append(1))
    assert p._probe_timer.isActive()
    p.stop_background()
    assert not p._probe_timer.isActive()
    p._start_device_probe()                               # even if the timer fired anyway
    assert started == []


def test_startup_gpu_check_skipped_while_tasks_shut_down(ap, qtbot, monkeypatch):
    import ui.workers as workers
    p, probe = _panel(ap, qtbot)
    started = []
    monkeypatch.setattr(probe, "start", lambda *a, **k: started.append(1))
    monkeypatch.setattr(workers, "_SHUTTING_DOWN", True)
    p._start_device_probe()
    assert started == []
    monkeypatch.setattr(workers, "_SHUTTING_DOWN", False)
    p._start_device_probe()
    assert started == [1]
