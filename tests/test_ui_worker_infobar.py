"""Worker maps info-bar crop + scan area back to full-frame coordinates."""
import cv2
import numpy as np

from core.grain_detector import DetectionParams
from tests.conftest import make_mosaic
from ui.workers import analyze_image


def _image_with_bottom_bar():
    gray, _ = make_mosaic(h=512, w=640)
    img = np.repeat(gray[:, :, None], 3, axis=2).copy()
    bar_top = 448
    img[bar_top:, :] = 0
    cv2.putText(img, "SE2  15.00 kV  WD 8.6 mm  Mag 500 x", (10, 485),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    cv2.line(img, (480, 490), (600, 490), (255, 255, 255), 3)
    return img, bar_top


def test_info_bar_excluded_and_mapped_to_full_frame():
    img, bar_top = _image_with_bottom_bar()
    scan = (20, 10, 600, 490)   # scan area that still includes the bar
    res = analyze_image(img, 0.0, DetectionParams(detection_mode="boundary"), scan)
    assert res.label_image.shape == img.shape[:2]
    assert not np.any(res.label_image[bar_top:, :]), "grains inside the info bar"
    assert res.info_bar_rect is not None
    x, y, w, h = res.info_bar_rect
    assert abs(y - bar_top) <= 3, (y, bar_top)          # full-frame y
    assert x >= scan[0]                                  # shifted by scan x
    for g in res.grains:
        assert g.centroid_y < bar_top
        assert scan[0] <= g.centroid_x < scan[0] + scan[2]
