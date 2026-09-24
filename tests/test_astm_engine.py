"""DET-04 / INN-26: ASTM E112 / E1382 grain-size engine (core/astm.py).

Ground truth is analytic:
* square grid of pitch d: N_A = 1/d^2; P_L(0/90 deg) = 1/d,
  P_L(45/135 deg) = sqrt(2)/d  -> 4-orientation l_bar = 2d/(1+sqrt 2).
* hexagonal tiling of lattice spacing a (cell side s = a/sqrt 3):
  N_A = 1/(sqrt(3)/2 a^2); Cauchy: l_bar = pi A / perimeter = pi sqrt(3)/4 s.
* stationary Poisson-Voronoi of intensity lambda: N_A = lambda,
  boundary length per area L_A = 2 sqrt(lambda), P_L = (2/pi) L_A,
  l_bar = pi / (4 sqrt(lambda)).
"""
import math
import time

import numpy as np
import pytest
from scipy.spatial import cKDTree

from core import astm as A
from core.grain_detector import AnalysisResult, DetectionParams, GrainDetector
from tests.conftest import make_mosaic

PPU = 1.0  # px per um -> 1 px = 1 um, 1 mm = 1000 px


# ----------------------------------------------------------------------
# synthetic label images
# ----------------------------------------------------------------------

def square_grid(h, w, d, off, gap=0):
    yy, xx = np.mgrid[0:h, 0:w]
    lab = (((yy + off) // d) * 1000 + (xx + off) // d + 1).astype(np.int32)
    if gap:
        lab[((yy + off) % d) < gap] = 0
        lab[((xx + off) % d) < gap] = 0
    return lab


def _voronoi_labels(pts, h, w, gap):
    yy, xx = np.mgrid[0:h, 0:w]
    _, idx = cKDTree(pts).query(np.stack([yy.ravel(), xx.ravel()], 1))
    lab = (idx.reshape(h, w) + 1).astype(np.int32)
    if gap:
        e = np.zeros((h, w), bool)
        e[:, :-1] |= lab[:, :-1] != lab[:, 1:]
        e[:-1, :] |= lab[:-1, :] != lab[1:, :]
        lab[e] = 0
    return lab


def poisson_voronoi(h, w, cell_area_px, seed, gap=0, margin=100):
    """Stationary Poisson-Voronoi: seeds also outside the frame, so the
    frame cuts cells at random (as a micrograph does)."""
    rng = np.random.default_rng(seed)
    n = rng.poisson((h + 2 * margin) * (w + 2 * margin) / cell_area_px)
    pts = np.stack([rng.uniform(-margin, h + margin, n),
                    rng.uniform(-margin, w + margin, n)], 1)
    return _voronoi_labels(pts, h, w, gap)


def hex_tiling(h, w, a, gap=0, margin=100):
    pts = []
    for j in range(-4, int((h + 2 * margin) / (a * 0.8660254)) + 4):
        for i in range(-4, int((w + 2 * margin) / a) + 4):
            pts.append((j * a * 0.8660254 - margin,
                        i * a + (a / 2 if j % 2 else 0) - margin))
    return _voronoi_labels(np.array(pts) + [0.37, 0.61], h, w, gap)


# ----------------------------------------------------------------------
# 1. formula anchors (E112-13 Table 6 / eq. 10)
# ----------------------------------------------------------------------

def test_formula_anchors():
    assert A.G_from_NA(7.75) == pytest.approx(0.0, abs=0.01)
    assert A.G_from_NA(1984) == pytest.approx(8.0, abs=0.01)
    assert A.G_from_mean_intercept(0.32) == pytest.approx(0.0, abs=0.02)
    assert A.G_from_mean_intercept(0.020) == pytest.approx(8.0, abs=0.02)
    assert A.G_from_NA(0) is None and A.G_from_mean_intercept(0) is None
    assert A.G_from_mean_area(1 / 1984) == pytest.approx(8.0, abs=0.01)


# ----------------------------------------------------------------------
# 2. analytic ground truth
# ----------------------------------------------------------------------

@pytest.mark.parametrize("gap", [0, 1, 2])
def test_square_grid_intercept_and_planimetric(gap):
    d = 32
    # offset d/2: frame edges cut grains in half; lines (spacing 2d) run
    # mid-cell, avoiding the degenerate case of a line lying ON a boundary
    lab = square_grid(1024, 1024, d, d // 2, gap)
    r0 = A.intercept(lab, None, PPU, spacing_px=2 * d, angles_deg=(0, 90))
    assert r0["mean_intercept_px"] == pytest.approx(d, rel=0.02)
    r4 = A.intercept(lab, None, PPU, spacing_px=2 * d)
    assert r4["mean_intercept_px"] == pytest.approx(2 * d / (1 + 2 ** 0.5),
                                                    rel=0.02)
    g_true_i = A.G_from_mean_intercept(d / 1000.0)
    assert r0["G"] == pytest.approx(g_true_i, abs=0.25)

    p = A.planimetric(lab, None, PPU)
    # 31x31 inside, 4x31 edge halves, 4 corner quarters = exactly 32^2
    assert p["grains_counted"] == pytest.approx(32 * 32)
    assert p["N_corner"] == 4
    g_true_p = A.G_from_NA(1.0 / (d / 1000.0) ** 2)
    assert p["G"] == pytest.approx(g_true_p, abs=0.01)


@pytest.mark.parametrize("gap", [0, 1])
def test_hex_tiling_matches_analytic(gap):
    a = 32.0
    lab = hex_tiling(768, 768, a, gap)
    r = A.compute_astm(lab, None, PPU)
    s = a / math.sqrt(3)
    g_p = A.G_from_NA(1e6 / (a * a * 0.8660254))
    g_i = A.G_from_mean_intercept(math.pi * math.sqrt(3) / 4 * s / 1000)
    assert r.G_planimetric == pytest.approx(g_p, abs=0.25)
    assert r.G_intercept == pytest.approx(g_i, abs=0.25)


@pytest.mark.parametrize("gap,seed", [(0, 1), (1, 2)])
def test_poisson_voronoi_matches_analytic(gap, seed):
    cell = 900.0
    lab = poisson_voronoi(768, 768, cell, seed, gap)
    r = A.compute_astm(lab, None, PPU)
    lam_mm = 1e6 / cell
    g_p = A.G_from_NA(lam_mm)
    g_i = A.G_from_mean_intercept(math.pi / (4 * math.sqrt(lam_mm)))
    assert r.G_planimetric == pytest.approx(g_p, abs=0.25)
    assert r.G_intercept == pytest.approx(g_i, abs=0.25)
    assert r.std_intercept_um > 0 and len(r.intercept_lengths_um) > 100


def test_methods_agree_on_equiaxed_mosaic():
    """E112: planimetric and intercept G agree on equiaxed structures."""
    _, lab = make_mosaic(512, 512, n_grains=250, seed=11)
    r = A.compute_astm(lab.astype(np.int32), None, 2.0)
    assert abs(r.G_planimetric - r.G_intercept) < 0.5
    names = {c["check"]: c for c in r.compliance}
    assert names["Planimetric vs intercept agreement"]["status"] == "PASS"


def test_three_circle_pattern():
    lab = poisson_voronoi(768, 768, 900.0, 4, gap=1)
    r = A.compute_astm(lab, None, PPU, method="intercept_circles")
    lam_mm = 1e6 / 900.0
    g_i = A.G_from_mean_intercept(math.pi / (4 * math.sqrt(lam_mm)))
    assert r.pattern == "circles"
    assert r.G_planimetric is None
    assert r.G_intercept == pytest.approx(g_i, abs=0.3)
    # circumferences 3:2:1 of the largest (0.9 * 768 diameter)
    assert r.test_length_px == pytest.approx(2 * math.pi * 0.45 * 768 * 2,
                                             rel=0.01)


# ----------------------------------------------------------------------
# 3. counting rules
# ----------------------------------------------------------------------

def test_line_ends_count_half_intercept():
    lab = np.ones((5, 60), np.int32)
    lab[:, 20:40] = 2
    lab[:, 40:] = 3
    s = A.count_path(lab, np.full(60, 2), np.arange(60))
    assert s["P"] == 2.0            # two boundary crossings
    assert s["N"] == 2.0            # 1/2 + 1 + 1/2 intercepts
    assert s["length_px"] == 60.0
    assert s["intercept_lengths_px"] == [20.0]


def test_triple_point_counts_one_and_half():
    lab = np.array([[2, 2, 2, 2, 0, 3, 3],
                    [0, 0, 0, 2, 0, 3, 3],
                    [1, 1, 1, 0, 3, 3, 3],
                    [1, 1, 1, 0, 3, 3, 3],
                    [1, 1, 1, 0, 3, 3, 3]], np.int32)
    s = A.count_path(lab, np.full(7, 2), np.arange(7), normalize=False)
    assert s["triple"] == 1 and s["P"] == 1.5
    s = A.count_path(lab, np.full(7, 4), np.arange(7), normalize=False)
    assert s["triple"] == 0 and s["P"] == 1.0


def test_tangent_counts_one():
    lab = np.ones((61, 81), np.int32)
    for r in range(20, 31):                     # diamond grain 2, vertex on row 30
        half = 30 - r
        lab[r, 40 - half:40 + half + 1] = 2
    s = A.count_path(lab, np.full(81, 30), np.arange(81))
    assert s["tangent"] == 1 and s["P"] == A.TANGENT_WEIGHT == 1.0


def test_internal_hole_is_not_a_hit():
    lab = np.ones((21, 61), np.int32)
    lab[8:13, 25:35] = 0                        # unlabelled pore inside grain 1
    s = A.count_path(lab, np.full(61, 10), np.arange(61))
    assert s["P"] == 0.0


# ----------------------------------------------------------------------
# 4. invalid (black) regions
# ----------------------------------------------------------------------

def test_black_region_reduces_test_length_and_area_not_G():
    lab = poisson_voronoi(768, 768, 900.0, 5, gap=1)
    vm = np.ones(lab.shape, bool)
    vm[600:, :] = False                         # info bar
    vm[100:250, 400:600] = False                # detector drop-out
    lab_b = lab.copy()
    lab_b[~vm] = 0
    inv = 1 - vm.mean()
    full = A.compute_astm(lab, None, PPU, spacing_px=60)
    part = A.compute_astm(lab_b, vm, PPU, spacing_px=60)
    assert part.test_length_px == pytest.approx(
        full.test_length_px * (1 - inv), rel=0.02)
    assert part.field_area_px == pytest.approx(full.field_area_px * (1 - inv))
    assert part.field_area_mm2 < full.field_area_mm2
    assert abs(part.G_planimetric - full.G_planimetric) < 0.15
    assert abs(part.G_intercept - full.G_intercept) < 0.15
    assert not part.rectangular_field and part.N_corner == 0
    # grains cut by the invalid regions are counted 1/2
    assert part.N_intercepted > full.N_intercepted


# ----------------------------------------------------------------------
# 5. calibration / adequacy / compliance
# ----------------------------------------------------------------------

def test_uncalibrated_gives_none_with_reason():
    lab = poisson_voronoi(256, 256, 400.0, 6)
    r = A.compute_astm(lab, None, 0.0)
    assert r.G_planimetric is None and r.G_intercept is None
    assert r.G_primary is None and r.N_A_per_mm2 is None
    assert r.mean_intercept_um is None and r.test_length_um is None
    assert "uncalibrated" in r.reason
    assert r.N_inside > 0 and r.n_hits > 0      # counts still reported
    cal = {c["check"]: c for c in r.compliance}["Calibration present"]
    assert cal["status"] == "FAIL" and cal["passed"] is False


def test_adequacy_flags_too_few_grains():
    lab = square_grid(200, 200, 45, 0)          # ~20 grains
    r = A.compute_astm(lab, None, PPU)
    chk = {c["check"]: c for c in r.compliance}
    g = chk[">= 50 grains per field (planimetric)"]
    assert g["status"] == "WARN" and not g["passed"]
    assert chk[">= 50 intercepts per field"]["status"] == "WARN"
    for c in r.compliance:
        assert set(c) == {"check", "status", "passed", "detail"}
        assert c["status"] in ("PASS", "WARN", "FAIL", "INFO")


def test_adequacy_valid_area_and_aspect_ratio():
    lab = poisson_voronoi(400, 400, 400.0, 7)
    vm = np.zeros(lab.shape, bool)
    vm[:150, :] = True
    lab[~vm] = 0

    class G:  # minimal grain stand-in
        aspect_ratio = 2.2

    r = A.compute_astm(lab, vm, PPU, grains=[G()] * 10)
    chk = {c["check"]: c for c in r.compliance}
    assert chk["Valid area >= 50% of frame"]["status"] == "WARN"
    assert chk["Equiaxed structure"]["status"] == "WARN"
    assert r.mean_aspect_ratio == pytest.approx(2.2)


def test_mean_area_and_ala():
    lab = square_grid(320, 320, 32, 0)          # 100 equal 32x32 grains
    lab[:32, :64] = lab[0, 0]                   # one double grain (ALA)
    r = A.compute_astm(lab, None, PPU)
    assert r.ALA_area_um2 == pytest.approx(2 * 32 * 32)
    assert r.G_ALA == pytest.approx(A.G_from_NA(1e6 / (2 * 1024)), abs=1e-6)
    assert r.G_mean_area == pytest.approx(
        A.G_from_NA(1e6 / (320 * 320 / 99)), abs=1e-6)


def test_result_is_json_able_and_deterministic():
    import json
    lab = poisson_voronoi(384, 384, 400.0, 8)
    d1 = A.compute_astm(lab, None, 1.5).to_dict()
    d2 = A.compute_astm(lab, None, 1.5).to_dict()
    assert json.dumps(d1) == json.dumps(d2)
    assert "twin boundaries not distinguished" in d1["notes"]


# ----------------------------------------------------------------------
# 6. detector integration, persistence, performance
# ----------------------------------------------------------------------

def test_detector_populates_astm(mosaic_bgr):
    res = GrainDetector().analyze(mosaic_bgr, px_per_um=2.0)
    assert res.astm and res.astm["calibrated"]
    assert res.astm_g == res.astm["G_primary"] == res.astm["G_planimetric"]
    assert res.astm["G_intercept"] is not None
    assert A.astm_g_from_result(res) == res.astm_g

    un = GrainDetector().analyze(mosaic_bgr, px_per_um=0.0)
    assert un.astm_g is None and "uncalibrated" in un.astm["reason"]
    assert A.astm_g_from_result(un) is None


def test_detector_method_param(mosaic_bgr):
    p = DetectionParams(astm_method="intercept_lines")
    res = GrainDetector().analyze(mosaic_bgr, px_per_um=2.0, params=p)
    assert res.astm["G_planimetric"] is None
    assert res.astm_g == res.astm["G_intercept"]


def test_astm_g_from_legacy_result_computes_on_the_fly():
    lab = poisson_voronoi(256, 256, 400.0, 9)
    legacy = AnalysisResult(label_image=lab, px_per_um=1.0,
                            has_calibration=True)
    assert legacy.astm == {} and legacy.astm_g is None
    g = A.astm_g_from_result(legacy)
    assert g == pytest.approx(A.compute_astm(lab, None, 1.0).G_planimetric)


def test_session_roundtrip_keeps_astm(tmp_path, mosaic_bgr):
    from data.models import ImageEntry
    from data.session_io import load_session, save_session
    from data.workspace import Workspace

    ws = Workspace(tmp_path)
    proj = ws.create_project("P")
    sample = ws.create_sample(proj, "S")
    lot = ws.create_lot(proj, sample, "L")
    res = GrainDetector().analyze(mosaic_bgr, px_per_um=2.0)
    ref = save_session(lot, {}, [ImageEntry(image_bgr=mosaic_bgr, result=res,
                                            filename="f.png")])
    back = load_session(ref.path).images[0].result
    assert back.astm_g == pytest.approx(res.astm_g)
    assert back.astm["G_intercept"] == pytest.approx(res.astm["G_intercept"])
    assert back.astm["compliance"] == res.astm["compliance"]
    assert back.astm["intercept_lengths_um"] == pytest.approx(
        res.astm["intercept_lengths_um"])


def test_performance_2048x1536_2000_grains():
    lab = poisson_voronoi(1536, 2048, 1536 * 2048 / 2000.0, 5, gap=1,
                          margin=0)
    vm = np.ones(lab.shape, bool)
    vm[1300:, :] = False
    A.compute_astm(lab, vm, 2.0)                # warm-up
    t = time.perf_counter()
    r = A.compute_astm(lab, vm, 2.0)
    dt = time.perf_counter() - t
    assert r.G_primary is not None
    assert dt < 0.5, f"compute_astm took {dt:.3f} s"
