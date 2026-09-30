"""core.perf - thread caps for weak PCs (UPDATE 4 item 7)."""
import sys
import types

import cv2
import numpy as np
import pytest

from core import perf
from core.grain_detector import DetectionParams, GrainDetector


@pytest.fixture(autouse=True)
def _restore():
    before = cv2.getNumThreads()
    real = sys.modules.get("torch")
    t_before = real.get_num_threads() if hasattr(real, "get_num_threads") else None
    perf._reset_for_tests()
    yield
    cv2.setNumThreads(before)
    if t_before is not None:
        real.set_num_threads(t_before)
    perf._reset_for_tests()


def test_caps_values():
    c = perf.configure_threads(logical_cores=8, physical_cores=4)
    assert c["opencv_threads"] == 7 and c["torch_threads"] == 3
    assert c["torch_interop_threads"] == 1 and c["ocr_threads"] == 2
    assert c["opencv_applied"] is True and cv2.getNumThreads() == 7
    one = perf.configure_threads(logical_cores=1, physical_cores=1)
    assert one["opencv_threads"] == 1 and one["torch_threads"] == 1
    assert one["ocr_threads"] == 1 and perf.ocr_threads() == 1
    two = perf.configure_threads(logical_cores=2)
    assert two["physical_cores"] == 2 and two["torch_threads"] == 2
    assert perf.current_caps()["logical_cores"] == 2
    four = perf.configure_threads(logical_cores=4)      # 2 cores + SMT
    assert four["physical_cores"] == 2 and four["torch_threads"] == 2
    assert perf.configure_threads(logical_cores=6, physical_cores=3)["torch_threads"] == 2


def test_physical_estimate_and_garbage_never_raise():
    assert perf.estimate_physical_cores(16) == 8
    assert perf.estimate_physical_cores(2) == 2
    assert perf.estimate_physical_cores(0) == 1
    assert perf.estimate_physical_cores("x") == 1
    c = perf.configure_threads(logical_cores="junk", physical_cores=-3)
    assert "error" not in c and c["opencv_threads"] >= 1 and c["torch_threads"] >= 1
    d = perf.configure_threads()          # real machine, repeat call is fine
    assert d["opencv_threads"] >= 1 and 1 <= perf.ocr_threads() <= 2


class _FakeTorch(types.SimpleNamespace):
    def __init__(self, interop_raises=False):
        super().__init__(calls=[], interop_raises=interop_raises)

    def set_num_threads(self, n):
        self.calls.append(("intra", n))

    def set_num_interop_threads(self, n):
        self.calls.append(("inter", n))
        if self.interop_raises:
            raise RuntimeError("cannot set number of interop threads")


def test_torch_not_imported_by_configure(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    c = perf.configure_threads(logical_cores=8, physical_cores=4)
    assert c["torch_applied"] is False
    assert "torch" not in sys.modules
    assert perf.apply_torch_threads() is False     # nothing loaded -> no-op


def _isolate_from_stray_threads(monkeypatch, *fakes):
    """A leftover background thread (start-up GPU probe) may call
    ai_device._import_torch -> perf.apply_torch_threads(real torch) at any
    moment, flipping the once-only module state.  Make perf._apply_torch a
    no-op for any module that is not one of ``fakes`` so such a thread can
    never touch the state, then reset the state under the lock."""
    real_apply = perf._apply_torch

    def guarded(torch, caps):
        return real_apply(torch, caps) if any(torch is f for f in fakes) else True

    monkeypatch.setattr(perf, "_apply_torch", guarded)
    with perf._LOCK:
        monkeypatch.setattr(perf, "_CAPS", None)
        monkeypatch.setattr(perf, "_TORCH_APPLIED_ID", None)
        monkeypatch.setattr(perf, "_TORCH_INTEROP_DONE", False)


def test_torch_cap_applied_on_load_once(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)  # real torch untouched
    t = _FakeTorch(interop_raises=True)
    _isolate_from_stray_threads(monkeypatch, t)
    perf.configure_threads(logical_cores=8, physical_cores=4)
    assert perf.apply_torch_threads(t) is True
    assert t.calls == [("intra", 3), ("inter", 1)]
    assert perf.apply_torch_threads(t) is True     # cached: no more calls
    assert t.calls == [("intra", 3), ("inter", 1)]
    # re-configuring re-applies intra-op; inter-op is one-shot in torch
    monkeypatch.setitem(sys.modules, "torch", t)
    c = perf.configure_threads(logical_cores=4, physical_cores=2)
    assert c["torch_applied"] is True and t.calls[-1] == ("intra", 2)
    assert sum(1 for k, _ in t.calls if k == "inter") == 1


def test_broken_torch_never_raises():
    bad = types.SimpleNamespace()                  # no set_num_threads at all
    assert perf.apply_torch_threads(bad) is False


def test_import_torch_hook_applies_caps(monkeypatch):
    from core import ai_device
    t = _FakeTorch()
    monkeypatch.setitem(sys.modules, "torch", t)   # "import torch" finds it
    _isolate_from_stray_threads(monkeypatch, t)
    perf.configure_threads(logical_cores=8, physical_cores=4)
    t.calls.clear()
    perf._reset_for_tests()
    perf.configure_threads(logical_cores=6, physical_cores=3)
    assert ai_device._import_torch() is t
    assert ("intra", 2) in t.calls


def test_ocr_engine_gets_thread_cap(monkeypatch):
    pytest.importorskip("rapidocr_onnxruntime")
    import rapidocr_onnxruntime
    from core import info_bar_ocr as ibo
    seen = {}

    class FakeRapid:
        def __init__(self, **kw):
            seen.update(kw)
    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", FakeRapid)
    perf.configure_threads(logical_cores=8, physical_cores=4)
    ibo._load_engine()
    assert seen == {"intra_op_num_threads": 2, "inter_op_num_threads": 1}


def test_caps_do_not_change_detection(mosaic_bgr):
    """Thread counts only change scheduling, never the labels."""
    img = mosaic_bgr[:256, :256].copy()
    results = []
    for n in (None, 1):
        if n is None:
            cv2.setNumThreads(max(2, cv2.getNumThreads()))
        else:
            perf.configure_threads(logical_cores=2, physical_cores=1)
            assert cv2.getNumThreads() == 1
        for mode in ("boundary", "threshold"):
            r = GrainDetector().analyze(img, 1.0, DetectionParams(detection_mode=mode))
            results.append((mode, r.label_image.copy(), len(r.grains)))
    free, capped = results[:2], results[2:]
    for (m1, a, na), (m2, b, nb) in zip(free, capped):
        assert m1 == m2 and na == nb and np.array_equal(a, b)
