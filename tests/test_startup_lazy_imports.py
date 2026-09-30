"""
UPDATE 4 item 12: the app starts faster because scikit-image and
scipy.stats are imported on first use, not when the UI imports the core
modules; and the detector skips its own overlay when the caller draws one.
"""
import subprocess
import sys

import numpy as np

from core.grain_detector import DetectionParams, GrainDetector


def test_core_imports_do_not_pull_skimage_or_scipy_stats():
    code = (
        "import sys\n"
        "import core.grain_detector, core.lot_compare\n"
        "bad = [m for m in ('skimage', 'scipy.stats') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


def test_lazy_stats_works():
    from core.lot_compare import stats
    assert abs(float(stats.t.ppf(0.975, 10)) - 2.228) < 0.01


def test_detector_can_skip_its_overlay(mosaic_bgr):
    p = DetectionParams(detection_mode="threshold")
    with_ov = GrainDetector().analyze(mosaic_bgr, 1.0, p)
    without = GrainDetector().analyze(mosaic_bgr, 1.0, p, draw_overlay=False)
    assert with_ov.overlay_image is not None
    assert without.overlay_image is None
    assert without.grain_count == with_ov.grain_count
    assert np.array_equal(without.label_image, with_ov.label_image)


def test_worker_analyze_image_overlay_modes(mosaic_bgr):
    from ui.workers import analyze_image
    p = DetectionParams(detection_mode="threshold")
    r = analyze_image(mosaic_bgr, 1.0, p, draw_overlay=True)
    assert r.overlay_image is not None and r.overlay_image.shape == mosaic_bgr.shape
    r2 = analyze_image(mosaic_bgr, 1.0, p, draw_overlay=False)
    assert r2.overlay_image is None and r2.grain_count == r.grain_count
