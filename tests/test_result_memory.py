"""
Memory for large loads (wave 2, task B): analysed images that are not in use
keep compact per-grain numbers + compressed label maps only; overlays are
drawn again on demand (current image, reports) through a small LRU.
"""
import copy

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector  # noqa: E402
from core.metrics import compute_statistics  # noqa: E402
from core.result_pack import (  # noqa: E402
    PackedArray, is_packed, pack_result, unpack_result, unpacked_copy,
)
from tests.conftest import make_mosaic  # noqa: E402

H = W = 384
N_IMAGES = 200


def _raw(seed: int = 3):
    gray, lab = make_mosaic(h=H, w=W, n_grains=60, seed=seed)
    lab = lab.astype(np.int32) + 1
    lab[:4, :] = 0
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    p = DetectionParams(min_grain_size_px=0, max_grain_size_px=0)
    grains = GrainDetector()._measure_grains(lab, p, 2.0)
    r = AnalysisResult(grains=grains, grain_count=len(grains), label_image=lab,
                       binary_image=(lab > 0).astype(np.uint8) * 255,
                       valid_mask=np.ones((H, W), bool), px_per_um=2.0, has_calibration=True)
    compute_statistics(r, (H, W))
    r.overlay_image = GrainDetector()._draw_overlay(bgr, lab, grains)
    return bgr, r


def test_packed_array_round_trip_exact():
    rng = np.random.default_rng(0)
    for a in (rng.integers(0, 70000, (50, 60)).astype(np.int32),
              rng.integers(0, 300, (50, 60)).astype(np.int32),
              rng.random((33, 17)) > 0.5, rng.integers(0, 255, (20, 20, 3)).astype(np.uint8),
              np.zeros((0, 0), np.int32)):
        p = PackedArray(a)
        b = p.unpack()
        assert b.dtype == a.dtype and b.shape == a.shape and np.array_equal(a, b)
        assert b.flags.writeable


def test_pack_result_shares_arrays_and_restores():
    _bgr, raw = _raw()
    res = copy.copy(raw)                      # filtered result sharing the masks
    lab = raw.label_image.copy()
    memo = {}
    assert pack_result(raw, memo) and pack_result(res, memo)
    assert raw.label_image is None and raw.overlay_image is None and is_packed(res)
    # one compressed copy of the shared label map, far smaller than the array
    blob = raw._packed_arrays["label_image"]
    assert res._packed_arrays["label_image"] is blob and blob.nbytes < lab.nbytes / 10
    cp = unpacked_copy(raw)
    assert is_packed(raw) and np.array_equal(cp.label_image, lab)
    assert unpack_result(raw) and np.array_equal(raw.label_image, lab)
    assert raw.grain_count == len(raw.grains) > 10              # numbers never touched


def _state_with_images(tmp_path, monkeypatch, n=N_IMAGES):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from data.models import SessionMeta
    from ui.app_state import AppState, ImageDoc, SessionDoc
    st = AppState(settings_path=tmp_path / "settings.json")
    doc = SessionDoc(path=tmp_path / "sess", meta=SessionMeta())
    st.session = doc
    bgr, base = _raw()
    for i in range(n):
        raw = copy.copy(base)
        for name in ("label_image", "binary_image", "valid_mask", "overlay_image"):
            setattr(raw, name, getattr(base, name).copy())
        res = copy.copy(raw)                                      # kept == all grains
        res.overlay_image = raw.overlay_image.copy()
        im = ImageDoc(filename=f"img_{i:03d}.png", raw=raw, result=res, status="done",
                      shape=(H, W), readable=True)
        doc.images.append(im)
        st.hold_arrays(im)
    return st, bgr, base


def test_200_analysed_images_hold_bounded_label_and_overlay_memory(tmp_path, qapp, monkeypatch):
    from ui.app_state import RESULT_CACHE_MAX_IMAGES
    st, bgr, base = _state_with_images(tmp_path, monkeypatch)
    per_image = sum(getattr(base, n).nbytes for n in
                    ("label_image", "binary_image", "valid_mask", "overlay_image")) \
        + base.overlay_image.nbytes
    everything = per_image * N_IMAGES
    held = st.held_array_bytes()
    cap = per_image * (RESULT_CACHE_MAX_IMAGES + 2)            # hot LRU + compressed rest
    assert held < cap, (held, cap, everything)
    assert held < everything / 10
    assert st.held_array_count() <= RESULT_CACHE_MAX_IMAGES
    hot = [im for im in st.images() if not is_packed(im.raw)]
    assert len(hot) <= RESULT_CACHE_MAX_IMAGES
    # numbers stay available for every image (tables, charts, statistics)
    assert all(im.result.grain_count == base.grain_count for im in st.images())

    # showing an old image brings its labels back exactly
    far = st.images()[0]
    assert is_packed(far.raw)
    st.set_current_image(far.uid)
    cur = st.current_image()
    assert cur is far and not is_packed(far.raw)
    assert np.array_equal(far.raw.label_image, base.label_image)
    assert far.result.label_image is not None and far.result.valid_mask is not None
    assert st.held_array_count() <= RESULT_CACHE_MAX_IMAGES

    # overlays of compacted images are drawn again on demand (small LRU)
    other = st.images()[5]
    assert is_packed(other.raw) and other.result.overlay_image is None
    other.image_bgr = bgr
    ov = st.overlay_for(other.uid)
    assert ov is not None and ov.shape == (H, W, 3)
    assert is_packed(other.raw)                                # still compact
    assert st.overlay_for(other.uid) is ov                     # LRU hit
    for im in st.images()[10:20]:
        im.image_bgr = bgr
        st.overlay_for(im.uid)
    from ui.overlay_cache import OVERLAY_CACHE_MAX
    assert len(st.overlays) <= OVERLAY_CACHE_MAX


def test_report_inputs_load_overlays_lazily(tmp_path, qapp, monkeypatch):
    """The report builder gets an overlay loader per image, not 200 overlays."""
    from reports.model import ReportModel
    from ui.pages import report_builder as rb
    st, bgr, _base = _state_with_images(tmp_path, monkeypatch, n=30)
    for im in st.images():
        im.image_bgr = bgr
    inputs = rb.collect_inputs(st)
    assert len(inputs) == 30
    lazy = [i for i in inputs if i.overlay_bgr is None]
    assert len(lazy) >= 30 - 8 and all(i.overlay_loader is not None for i in lazy)
    calls = []
    for i in lazy:
        real = i.overlay_loader
        i.overlay_loader = (lambda r=real: calls.append(1) or r())
    model = ReportModel.from_results(inputs, title="T", asset_dir=str(tmp_path / "assets"))
    assert len(calls) == len(lazy)
    assert all(img.overlay_path for img in model.images)
    # nothing was unpacked in the GUI-side documents
    assert sum(is_packed(im.raw) for im in st.images()) >= 30 - 8


def test_edit_undo_redo_across_compaction(tmp_path, qapp, qtbot, monkeypatch):
    """A merged image that gets compacted (not in use) still undoes / redoes
    exactly: its label maps come back from the compressed copy."""
    st, bgr, base = _state_with_images(tmp_path, monkeypatch, n=12)
    monkeypatch.setattr(st, "schedule_save", lambda: None)
    im = st.images()[0]
    im.image_bgr = bgr
    st.set_current_image(im.uid)
    lab = im.raw.label_image
    a = int(lab[H // 2, W // 2])
    right = lab[H // 2, W // 2:]
    b = int(next(v for v in right if v not in (0, a)))
    st.merge_grains(im.uid, [a, b])
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=20000)
    merged = im.raw.label_image.copy()
    assert not np.array_equal(merged, base.label_image)
    st.set_current_image(st.images()[1].uid)
    st._dirty.clear()
    assert st.compact_arrays(im) and is_packed(im.raw) and im.detector_labels is None
    st.undo_stack.undo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=20000)
    assert np.array_equal(im.raw.label_image, base.label_image) and not im.edits
    st._dirty.clear()
    st.compact_arrays(im)
    st.undo_stack.redo()
    qtbot.waitUntil(lambda: not st.is_filtering(), timeout=20000)
    assert np.array_equal(im.raw.label_image, merged)
    assert np.array_equal(im.detector_labels, base.label_image)


def test_save_writes_packed_result_and_redrawn_overlay(tmp_path):
    """Autosave of an image compacted before it was written: the save thread
    unpacks the labels and asks the overlay loader (never the plain image)."""
    import cv2
    from data.session_io import _save_result_files
    bgr, raw = _raw()
    lab = raw.label_image.copy()
    snap = copy.copy(raw)
    pack_result(snap)
    marker = np.full_like(bgr, 77)
    snap.overlay_loader = lambda: marker
    (tmp_path / "r").mkdir()
    (tmp_path / "t").mkdir()
    _save_result_files(tmp_path / "r", tmp_path / "t", "img", snap, bgr)
    with np.load(tmp_path / "r" / "img.labels.npz") as z:
        assert np.array_equal(z["label_image"], lab)
        assert z["valid_mask"].shape == lab.shape
    ov = cv2.imread(str(tmp_path / "r" / "img.overlay.png"))
    assert ov is not None and int(ov.mean()) == 77
    assert is_packed(snap)                                   # the in-memory copy untouched


def test_unsaved_images_compacted_only_under_pressure(tmp_path, qapp, monkeypatch):
    from ui.app_state import RESULT_CACHE_MAX_IMAGES as N
    st, _bgr, _base = _state_with_images(tmp_path, monkeypatch, n=0)
    from ui.app_state import ImageDoc
    _b, base = _raw()
    for i in range(5 * N):
        raw = copy.copy(base)
        raw.label_image = base.label_image.copy()
        raw.overlay_image = base.overlay_image.copy()
        im = ImageDoc(filename=f"u{i}.png", raw=raw, result=raw, status="done")
        st.session.images.append(im)
        st._dirty.add(im.uid)                       # waiting for autosave
        st.hold_arrays(im)
        if i < 2 * N:
            assert st.held_array_count() == i + 1  # fresh overlays kept for the save
    assert st.held_array_count() <= 3 * N
