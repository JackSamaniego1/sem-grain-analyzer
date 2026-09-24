"""
DET-01 (SAM side): filter_sam_masks is a pure function, tested with fake
SAM mask dicts — no checkpoint or torch needed.
"""
import numpy as np

from core.grain_detector import filter_sam_masks


H, W = 100, 100


def _mask(r0, r1, c0, c1, iou=0.95):
    seg = np.zeros((H, W), bool)
    seg[r0:r1, c0:c1] = True
    return {"segmentation": seg, "area": int(seg.sum()), "predicted_iou": iou}


def _scene():
    rng = np.random.default_rng(0)
    gray = np.clip(rng.normal(120, 8, (H, W)), 0, 255).astype(np.uint8)
    gray[60:, :] = 0                       # black band
    gray[0:20, 70:90] = 180                # flat saturated-ish patch (std 0)
    valid = np.ones((H, W), bool)
    valid[60:, :] = False
    return gray, valid


def _run(masks, gray, valid, **kw):
    args = dict(min_area=50, max_area=H * W * 0.5, min_mean_intensity=12,
                min_intensity_std=1.5, min_valid_fraction=0.5)
    args.update(kw)
    return filter_sam_masks(masks, gray, valid, **args)


def test_textured_grain_accepted():
    gray, valid = _scene()
    labels, binary, n = _run([_mask(5, 25, 5, 25)], gray, valid)
    assert n == 1 and (labels[5:25, 5:25] == 1).all() and binary.max() == 255


def test_black_mask_rejected():
    gray, valid = _scene()
    _, _, n = _run([_mask(65, 95, 10, 40)], gray, valid)
    assert n == 0


def test_black_mask_rejected_by_mean_even_if_valid_mask_missing():
    gray, _ = _scene()
    _, _, n = _run([_mask(65, 95, 10, 40)], gray, None)
    assert n == 0


def test_uniform_mask_rejected_by_std():
    gray, valid = _scene()
    _, _, n = _run([_mask(0, 20, 70, 90)], gray, valid)
    assert n == 0
    _, _, n = _run([_mask(0, 20, 70, 90)], gray, valid, min_intensity_std=0.0)
    assert n == 1


def test_mostly_invalid_mask_rejected():
    rng = np.random.default_rng(1)
    gray = np.clip(rng.normal(120, 8, (H, W)), 0, 255).astype(np.uint8)
    valid = np.ones((H, W), bool)
    valid[40:, :] = False
    # 30 rows, only 10 on valid pixels -> 33 % valid
    _, _, n = _run([_mask(30, 60, 10, 40)], gray, valid)
    assert n == 0
    _, _, n = _run([_mask(30, 60, 10, 40)], gray, valid, min_valid_fraction=0.3)
    assert n == 1


def test_size_iou_and_overlap_rules_preserved():
    gray, valid = _scene()
    masks = [
        _mask(5, 10, 5, 10),             # 25 px < min_area
        _mask(30, 50, 5, 25, iou=0.5),   # low predicted IoU
        _mask(0, 55, 0, 60),             # big mask (accepted first)
        _mask(2, 50, 2, 55),             # >50 % overlaps the big one
        _mask(5, 20, 5, 20),             # fully inside the big one
        _mask(20, 45, 62, 90),           # separate grain
    ]
    labels, _, n = _run(masks, gray, valid)
    assert n == 2
    assert (labels[0:55, 0:60] == 1).all()
    assert (labels[20:45, 62:90] == 2).all()


def test_background_sized_mask_rejected():
    gray = np.clip(np.random.default_rng(2).normal(120, 8, (H, W)), 0, 255
                   ).astype(np.uint8)
    _, _, n = _run([_mask(0, 50, 0, 100)], gray, np.ones((H, W), bool),
                   max_area=H * W)
    assert n == 0     # 50 % of frame > max_frame_fraction 0.4
