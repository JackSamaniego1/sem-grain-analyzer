"""UPDATE 4 item 10b (core): explicit GPU / CPU choice for AI-assisted (SAM)
detection -- device probe, selection, OOM fallback, per-device model cache.

Every branch runs with a fake torch / fake SAM; the real-GPU equivalence
test at the bottom only runs when CUDA-enabled torch sees a GPU.
"""
import types
import warnings

import numpy as np
import pytest

import core.ai_device as aid
import core.grain_detector as gd
from core.grain_detector import DetectionParams, GrainDetector


# ----------------------------------------------------------------------
# Fake torch
# ----------------------------------------------------------------------
class _T:
    def __init__(self, v):
        self.v = v

    def __mul__(self, k):
        return _T([x * k for x in self.v])

    def sum(self):
        return _T([sum(self.v)])

    def item(self):
        return self.v[0]


def fake_torch(cuda_build="12.1", available=True, count=1, warn="",
               avail_raises=None, op_error=None, name="NVIDIA RTX A2000",
               free_mb=5000, total_mb=6144):
    def is_available():
        if warn:
            warnings.warn(warn)
        if avail_raises:
            raise RuntimeError(avail_raises)
        return available

    def ones(n, device="cpu"):
        if op_error:
            raise RuntimeError(op_error)
        return _T([1.0] * n)

    cuda = types.SimpleNamespace(
        is_available=is_available, device_count=lambda: count,
        synchronize=lambda: None, get_device_name=lambda i=0: name,
        mem_get_info=lambda i=0: (free_mb * 2**20, total_mb * 2**20),
        empty_cache=lambda: None)
    return types.SimpleNamespace(__version__="2.5.0", version=types.SimpleNamespace(
        cuda=cuda_build), cuda=cuda, ones=ones)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    aid.clear_cache()
    gd._SAM_MODELS.clear()
    yield
    aid.clear_cache()
    gd._SAM_MODELS.clear()


def _probe_with(monkeypatch, torch):
    monkeypatch.setattr(aid, "_import_torch", lambda: torch)
    return aid.ai_devices(refresh=True)


# ----------------------------------------------------------------------
# Probe branches
# ----------------------------------------------------------------------
def test_no_torch(monkeypatch):
    info = _probe_with(monkeypatch, None)
    assert not info.torch_available and not info.cpu_available and not info.gpu_available
    assert info.gpu_reason == aid.REASON_NO_TORCH
    with pytest.raises(aid.AiUnavailableError):
        aid.resolve_device("cpu", info)


def test_cpu_only_build(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch(cuda_build=None))
    assert info.torch_available and info.cpu_available and not info.gpu_available
    assert info.gpu_reason == "This installation has no GPU support."
    assert aid.resolve_device("auto", info) == "cpu"
    assert aid.resolve_device("cpu", info) == "cpu"
    with pytest.raises(aid.GpuUnavailableError) as ei:
        aid.resolve_device("gpu", info)
    assert "no GPU support" in str(ei.value) and "CPU" in str(ei.value)


def test_no_nvidia_card(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch(available=False, count=0))
    assert not info.gpu_available and info.gpu_reason == aid.REASON_NO_CARD


def test_driver_too_old(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch(
        available=False, warn="CUDA initialization: The NVIDIA driver on your "
                              "system is too old (found version 11040)."))
    assert info.gpu_reason == aid.REASON_DRIVER_OLD


def test_is_available_raising_is_contained(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch(avail_raises="boom"))
    assert not info.gpu_available and info.gpu_reason


def test_tiny_op_failure_card_too_old(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch(
        op_error="CUDA error: no kernel image is available for execution"))
    assert not info.gpu_available and info.gpu_reason == aid.REASON_CARD_TOO_OLD


def test_tiny_op_failure_generic(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch(op_error="CUDA error: unknown"))
    assert not info.gpu_available and info.gpu_reason == aid.REASON_GPU_FAILED
    assert aid.resolve_device("auto", info) == "cpu"


def test_cuda_ok(monkeypatch):
    info = _probe_with(monkeypatch, fake_torch())
    assert info.gpu_available and info.gpu_reason == ""
    assert info.gpu_name == "NVIDIA RTX A2000"
    assert (info.vram_total_mb, info.vram_free_mb) == (6144, 5000)
    assert aid.resolve_device("auto", info) == "cuda"
    assert aid.resolve_device("GPU", info) == "cuda"
    assert aid.resolve_device("cpu", info) == "cpu"
    assert set(info.to_dict()) >= {"gpu_available", "gpu_name", "vram_free_mb",
                                   "gpu_reason"}


def test_probe_is_cached_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(aid, "_import_torch",
                        lambda: calls.append(1) or fake_torch(cuda_build=None))
    aid.ai_devices(refresh=True)
    aid.ai_devices()
    aid.ai_devices()
    assert len(calls) == 1
    monkeypatch.setattr(aid, "_probe", lambda t: 1 / 0)
    info = aid.ai_devices(refresh=True)
    assert not info.gpu_available and info.gpu_reason


def test_torch_not_imported_at_module_import():
    import importlib
    import sys
    src = open(aid.__file__, encoding="utf-8").read()
    assert "\nimport torch" not in src and "\nfrom torch" not in src
    importlib.reload(aid)       # no side effects
    assert "core.ai_device" in sys.modules


def test_choice_normalisation_and_oom_classifier():
    assert aid.normalize_device_choice(None) == "auto"
    assert aid.normalize_device_choice("CUDA") == "gpu"
    assert aid.normalize_device_choice("bogus") == "auto"

    class OutOfMemoryError(RuntimeError):
        pass
    assert aid.is_cuda_oom(OutOfMemoryError("x"))
    assert aid.is_cuda_oom(RuntimeError("CUDA out of memory. Tried to allocate"))
    assert not aid.is_cuda_oom(MemoryError("out of memory"))
    assert not aid.is_cuda_oom(ValueError("bad"))


# ----------------------------------------------------------------------
# Detector with fake SAM
# ----------------------------------------------------------------------
CELL = 64


def _mosaic():
    rng = np.random.default_rng(0)
    g = np.zeros((4 * CELL, 4 * CELL), np.uint8)
    for i in range(4):
        for j in range(4):
            g[i * CELL:(i + 1) * CELL, j * CELL:(j + 1) * CELL] = 60 + 10 * (i * 4 + j)
    g = np.clip(g + rng.normal(0, 4, g.shape), 0, 255).astype(np.uint8)
    return np.dstack([g, g, g])


class FakeSam:
    def __init__(self):
        self.device = "cpu"
        self.moves = []

    def to(self, device):
        self.device = str(device)
        self.moves.append(self.device)
        return self

    def eval(self):
        return self


class _OOM(RuntimeError):
    pass


_OOM.__name__ = "OutOfMemoryError"


class FakeGen:
    fail_on_cuda = None          # exception instance to raise on the GPU

    def __init__(self, model, **kw):
        self.model, self.kw = model, kw
        self.points_per_batch = kw["points_per_batch"]

    def generate(self, rgb):
        if self.model.device == "cuda" and FakeGen.fail_on_cuda is not None:
            raise FakeGen.fail_on_cuda
        h, w = rgb.shape[:2]
        out = []
        for i in range(h // CELL):
            for j in range(w // CELL):
                seg = np.zeros((h, w), bool)
                seg[i * CELL + 2:(i + 1) * CELL - 2, j * CELL + 2:(j + 1) * CELL - 2] = True
                out.append({"segmentation": seg, "area": int(seg.sum()),
                            "predicted_iou": 0.95})
        return out


@pytest.fixture
def fake_sam(monkeypatch, tmp_path):
    ckpt = tmp_path / "sam.pth"
    ckpt.write_bytes(b"x")
    builds, gens = [], []

    def build(path):
        m = FakeSam()
        builds.append(m)
        return m

    def make_gen(model, **kw):
        g = FakeGen(model, **kw)
        gens.append(g)
        return g

    monkeypatch.setattr(gd, "find_sam_checkpoint", lambda: str(ckpt))
    monkeypatch.setattr(gd, "_build_sam_model", build)
    monkeypatch.setattr(gd, "_make_mask_generator", make_gen)
    FakeGen.fail_on_cuda = None
    yield types.SimpleNamespace(builds=builds, gens=gens)
    FakeGen.fail_on_cuda = None


def _set_devices(monkeypatch, gpu=True):
    info = aid.AiDeviceInfo(
        torch_available=True, cpu_available=True, gpu_available=gpu,
        gpu_name="NVIDIA RTX A2000" if gpu else "",
        gpu_reason="" if gpu else aid.REASON_NO_GPU_BUILD)
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: info)
    return info


def _run(device=None, params=None):
    p = params or DetectionParams(detection_mode="sam_astm")
    return GrainDetector().analyze(_mosaic(), 1.0, p, device=device)


def test_auto_uses_gpu_when_usable(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    r = _run()
    assert (r.ai_device, r.ai_device_requested) == ("gpu", "auto")
    assert r.ai_device_name == "NVIDIA RTX A2000" and not r.ai_device_fallback
    assert fake_sam.gens[-1].model.device == "cuda"
    assert r.grain_count == 16


def test_auto_without_gpu_runs_cpu(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=False)
    r = _run()
    assert r.ai_device == "cpu" and r.ai_device_name == "CPU"


def test_explicit_gpu_unavailable_raises_clear_error(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=False)
    with pytest.raises(aid.GpuUnavailableError) as ei:
        _run(device="gpu")
    assert "no GPU support" in str(ei.value)
    assert fake_sam.builds == []            # failed before loading anything


def test_params_sam_device_and_kwarg_override(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    p = DetectionParams(detection_mode="sam_astm", sam_device="cpu")
    assert DetectionParams().sam_device == "auto"
    assert _run(params=p).ai_device == "cpu"
    assert _run(device="gpu", params=p).ai_device == "gpu"


def test_non_ai_modes_leave_device_fields_empty():
    r = GrainDetector().analyze(_mosaic(), 1.0, DetectionParams(detection_mode="boundary"),
                                device="gpu")
    assert r.ai_device == "" and not r.ai_device_fallback


def test_same_sam_settings_on_both_devices(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    _run(device="cpu")
    _run(device="gpu")
    cpu_kw, gpu_kw = fake_sam.gens[0].kw, fake_sam.gens[1].kw
    for k in ("points_per_side", "pred_iou_thresh", "stability_score_thresh",
              "crop_n_layers", "min_mask_region_area"):
        assert cpu_kw[k] == gpu_kw[k], k
    assert cpu_kw["points_per_batch"] == gd.SAM_POINTS_PER_BATCH_CPU


def test_oom_falls_back_to_cpu_and_flags(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    ref = _run(device="cpu")
    FakeGen.fail_on_cuda = _OOM("CUDA out of memory. Tried to allocate 2.00 GiB")
    freed = []
    monkeypatch.setattr(aid, "release_gpu_memory", lambda: freed.append(1))
    msgs = []
    r = GrainDetector().analyze(_mosaic(), 1.0, DetectionParams(detection_mode="sam_astm"),
                                progress_callback=lambda p, m: msgs.append(m),
                                device="gpu")
    assert r.ai_device == "cpu" and r.ai_device_fallback
    assert r.ai_device_requested == "gpu"
    assert "out of memory" in r.ai_device_note
    assert freed
    assert any("continuing on the CPU" in m for m in msgs)
    assert r.grain_count == ref.grain_count
    assert np.array_equal(r.label_image, ref.label_image)
    # the GPU model stays cached (OOM is per-image, not a broken GPU)
    assert any(k[1] == "cuda" for k in gd._SAM_MODELS)


def test_other_cuda_error_falls_back_and_evicts_gpu_model(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    FakeGen.fail_on_cuda = RuntimeError("CUDA error: an illegal memory access")
    r = _run(device="auto")
    assert r.ai_device == "cpu" and r.ai_device_fallback
    assert r.ai_device_note == aid.NOTE_GPU_ERROR_FALLBACK
    assert not any(k[1] == "cuda" for k in gd._SAM_MODELS)


def test_non_cuda_error_propagates(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    FakeGen.fail_on_cuda = ValueError("bad input")
    with pytest.raises(ValueError):
        _run(device="gpu")


def test_cpu_errors_are_not_swallowed(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    monkeypatch.setattr(FakeGen, "generate",
                        lambda self, rgb: (_ for _ in ()).throw(
                            _OOM("CUDA out of memory")))
    with pytest.raises(RuntimeError):
        _run(device="cpu")


def test_device_switch_reuses_and_reloads_models(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    _run(device="cpu")
    assert len(fake_sam.builds) == 1
    _run(device="gpu")            # GPU copy made from the cached CPU model
    assert len(fake_sam.builds) == 1
    cpu_m = gd._SAM_MODELS[next(k for k in gd._SAM_MODELS if k[1] == "cpu")]
    gpu_m = gd._SAM_MODELS[next(k for k in gd._SAM_MODELS if k[1] == "cuda")]
    assert cpu_m is not gpu_m and cpu_m.device == "cpu" and gpu_m.device == "cuda"
    _run(device="cpu")
    _run(device="gpu")
    assert len(fake_sam.builds) == 1
    assert fake_sam.gens[-2].model is cpu_m and fake_sam.gens[-1].model is gpu_m
    gd.release_sam_models("cuda")
    assert not any(k[1] == "cuda" for k in gd._SAM_MODELS)
    _run(device="gpu")            # reloaded after release
    assert fake_sam.gens[-1].model.device == "cuda"
    gd.release_sam_models()
    assert gd._SAM_MODELS == {}
    _run(device="gpu")            # nothing cached: built from the checkpoint
    assert len(fake_sam.builds) == 2


def test_failed_gpu_load_caches_nothing_and_falls_back(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)

    def to(self, device):
        if str(device) == "cuda":
            raise _OOM("CUDA out of memory while loading")
        self.device = str(device)
        return self
    monkeypatch.setattr(FakeSam, "to", to)
    r = _run(device="gpu")
    assert r.ai_device == "cpu" and r.ai_device_fallback
    assert not any(k[1] == "cuda" for k in gd._SAM_MODELS)


def test_downscale_progress_after_loading(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=False)
    big = np.dstack([np.tile(_mosaic()[..., 0], (5, 5))] * 3)     # 1280 px
    msgs = []
    GrainDetector().analyze(big, 1.0, DetectionParams(detection_mode="sam_astm"),
                            progress_callback=lambda p, m: msgs.append((p, m)),
                            device="cpu")
    load = next(i for i, (p, m) in enumerate(msgs) if m.startswith("Loading SAM weights"))
    down = next(i for i, (p, m) in enumerate(msgs) if m.startswith("Downscaled"))
    assert msgs[load][0] == 5 and msgs[down][0] == 8 and down > load


def test_cuda_error_classifier_is_narrow():
    assert not aid.is_cuda_error(AssertionError("Torch not compiled with CUDA enabled"))
    assert not aid.is_cuda_error(ValueError("CUDA error: fake"))
    assert not aid.is_cuda_error(RuntimeError("Expected all tensors on cuda:0"))
    assert aid.is_cuda_error(RuntimeError("CUDA error: device-side assert triggered"))
    assert aid.is_cuda_error(RuntimeError("cuDNN error: CUDNN_STATUS_EXECUTION_FAILED"))
    assert aid.is_cuda_error(RuntimeError("CUBLAS_STATUS_EXECUTION_FAILED when calling"))


def test_unrelated_cuda_text_error_is_raised_not_rerun(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=True)
    FakeGen.fail_on_cuda = AssertionError("Torch not compiled with CUDA enabled")
    with pytest.raises(AssertionError):
        _run(device="auto")


def test_cuda_error_makes_gpu_unusable_until_reprobe(monkeypatch, fake_sam):
    monkeypatch.setattr(aid, "_import_torch", lambda: fake_torch())
    assert aid.ai_devices(refresh=True).gpu_available
    FakeGen.fail_on_cuda = RuntimeError("CUDA error: an illegal memory access "
                                        "was encountered")
    r = _run(device="auto")
    assert r.ai_device == "cpu" and r.ai_device_fallback
    info = aid.ai_devices()
    assert not info.gpu_available and info.gpu_reason == aid.REASON_GPU_ERROR_STICKY
    FakeGen.fail_on_cuda = None
    n_gens = len(fake_sam.gens)
    r2 = _run(device="auto")               # straight to CPU, no GPU attempt
    assert r2.ai_device == "cpu" and not r2.ai_device_fallback
    assert len(fake_sam.gens) == n_gens + 1
    assert fake_sam.gens[-1].model.device == "cpu"
    assert not any(k[1] == "cuda" for k in gd._SAM_MODELS)
    with pytest.raises(aid.GpuUnavailableError) as ei:
        _run(device="gpu")
    assert "until the app is restarted" in str(ei.value)
    assert aid.ai_devices(refresh=True).gpu_available     # re-probe clears it
    assert _run(device="gpu").ai_device == "gpu"


def test_cancel_while_waiting_for_sam_lock(monkeypatch, fake_sam):
    import threading
    from core.cancel import AnalysisCancelled
    _set_devices(monkeypatch, gpu=False)
    cancel, loading, box = threading.Event(), threading.Event(), {}

    def work():
        try:
            GrainDetector().analyze(
                _mosaic(), 1.0, DetectionParams(detection_mode="sam_astm"),
                progress_callback=lambda p, m: "Loading SAM weights" in m and loading.set(),
                cancel=cancel, device="cpu")
            box["done"] = True
        except AnalysisCancelled:
            box["cancelled"] = True

    gd._SAM_RUN_LOCK.acquire()
    try:
        t = threading.Thread(target=work, daemon=True)
        t.start()
        assert loading.wait(5)
        t.join(0.5)
        assert t.is_alive()                 # blocked on the run lock
        cancel.set()
        t.join(3)
        assert not t.is_alive() and box == {"cancelled": True}
    finally:
        gd._SAM_RUN_LOCK.release()


def test_run_lock_released_after_exception(monkeypatch, fake_sam):
    _set_devices(monkeypatch, gpu=False)
    orig = FakeGen.generate
    monkeypatch.setattr(FakeGen, "generate",
                        lambda self, rgb: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ValueError):
        _run(device="cpu")
    assert not gd._SAM_RUN_LOCK.locked()
    monkeypatch.setattr(FakeGen, "generate", orig)
    assert _run(device="cpu").grain_count == 16


def test_ai_devices_from_other_thread_during_run(monkeypatch, fake_sam):
    import threading
    monkeypatch.setattr(aid, "_import_torch", lambda: fake_torch())
    aid.ai_devices(refresh=True)
    orig, seen = FakeGen.generate, {}

    def generate(self, rgb):
        t = threading.Thread(target=lambda: seen.update(
            a=aid.ai_devices(), b=aid.ai_devices(refresh=True)), daemon=True)
        t.start()
        t.join(5)
        seen["alive"] = t.is_alive()
        return orig(self, rgb)
    monkeypatch.setattr(FakeGen, "generate", generate)
    r = _run(device="gpu")
    assert seen["alive"] is False and seen["a"].gpu_available and seen["b"].gpu_available
    assert r.ai_device == "gpu"


# ----------------------------------------------------------------------
# Real GPU: CPU vs GPU equivalence (skipped without CUDA torch + model)
# ----------------------------------------------------------------------
def _real_cuda():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


@pytest.mark.skipif(not _real_cuda() or gd.find_sam_checkpoint() is None,
                    reason="needs CUDA-enabled torch, an NVIDIA GPU and the SAM model")
def test_real_gpu_matches_cpu(mosaic_bgr):
    p = DetectionParams(detection_mode="sam_astm")
    a = GrainDetector().analyze(mosaic_bgr, 1.0, p, device="cpu")
    b = GrainDetector().analyze(mosaic_bgr, 1.0, p, device="gpu")
    assert (a.ai_device, b.ai_device) == ("cpu", "gpu")
    assert abs(a.grain_count - b.grain_count) <= max(1, round(0.02 * a.grain_count))
    agree = np.mean((a.label_image > 0) == (b.label_image > 0))
    assert agree >= 0.99
