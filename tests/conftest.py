"""
Shared fixtures for the Grain Analyzer test-suite.

Synthetic SEM-like images are generated procedurally so the suite has no
binary fixtures and runs in a couple of seconds on CPU.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Headless Qt for any UI tests that get added later.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_mosaic(h=512, w=512, n_grains=90, seed=7,
                boundary_val=35, boundary_px=2, noise_sigma=6.0,
                fill_range=(110, 200)):
    """
    Build a grayscale 'mosaic' grain image: Voronoi cells with mid-gray
    fills, thin dark grooves at the cell boundaries, mild Gaussian noise.
    Returns (gray_uint8, label_gt) where label_gt is the ground-truth cell id.
    """
    rng = np.random.default_rng(seed)
    pts = np.stack([rng.uniform(0, h, n_grains), rng.uniform(0, w, n_grains)], axis=1)
    yy, xx = np.mgrid[0:h, 0:w]
    coords = np.stack([yy.ravel(), xx.ravel()], axis=1).astype(np.float32)
    # nearest seed for every pixel (chunked to bound memory)
    label = np.empty(h * w, dtype=np.int32)
    for s in range(0, h * w, 65536):
        chunk = coords[s:s + 65536]
        d = ((chunk[:, None, :] - pts[None, :, :]) ** 2).sum(axis=2)
        label[s:s + 65536] = d.argmin(axis=1)
    label = label.reshape(h, w)

    fills = rng.uniform(fill_range[0], fill_range[1], n_grains)
    gray = fills[label].astype(np.float32)

    # boundary = pixel whose right or lower neighbour is a different cell
    edge = np.zeros((h, w), dtype=bool)
    edge[:, :-1] |= label[:, :-1] != label[:, 1:]
    edge[:-1, :] |= label[:-1, :] != label[1:, :]
    if boundary_px > 1:
        import cv2
        k = np.ones((boundary_px, boundary_px), np.uint8)
        edge = cv2.dilate(edge.astype(np.uint8), k).astype(bool)
    gray[edge] = boundary_val

    gray += rng.normal(0, noise_sigma, gray.shape)
    gray = np.clip(gray, 0, 255).astype(np.uint8)
    return gray, label + 1


@pytest.fixture
def mosaic_bgr():
    gray, _ = make_mosaic()
    return np.repeat(gray[:, :, None], 3, axis=2)


@pytest.fixture
def mosaic_with_black_regions():
    """
    Mosaic image with two pure-black regions:
      * a bottom band (like a masked-off SEM info bar / vignette)
      * a square hole in the field of view (like a pore or detector dropout)
    Returns (image_bgr, black_mask) where black_mask is True on black pixels.
    """
    gray, _ = make_mosaic()
    h, w = gray.shape
    black = np.zeros((h, w), dtype=bool)
    black[int(h * 0.78):, :] = True                 # bottom band
    black[60:200, 320:470] = True                    # interior square
    gray = gray.copy()
    gray[black] = 0
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    return bgr, black


def make_dark_mosaic(seed=9, boundary_val=12, noise_sigma=4.0):
    """Legitimately DARK grains (fills 40-70 uint8) with darker grooves."""
    return make_mosaic(seed=seed, fill_range=(40, 70),
                       boundary_val=boundary_val, noise_sigma=noise_sigma)


@pytest.fixture
def dark_mosaic_bgr():
    gray, _ = make_dark_mosaic()
    return np.repeat(gray[:, :, None], 3, axis=2)


@pytest.fixture
def dark_mosaic_with_black_regions():
    """Dark-grain mosaic plus a noisy near-black band (values 0-8, as a
    real detector produces) and a pure-black square."""
    gray, _ = make_dark_mosaic()
    h, w = gray.shape
    rng = np.random.default_rng(3)
    black = np.zeros((h, w), dtype=bool)
    black[int(h * 0.80):, :] = True
    black[80:200, 300:440] = True
    gray = gray.copy()
    gray[black] = rng.integers(0, 9, size=int(black.sum()))
    gray[80:200, 300:440] = 0
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    return bgr, black
