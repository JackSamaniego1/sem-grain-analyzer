"""
ASTM E112 / E1382 grain-size engine (DET-04 + INN-26).

Pure functions on a grain label image (0 = boundary / background, k > 0 =
grain k) plus the DET-01 valid-pixel mask.  numpy / scipy only, no Qt.

Standards implemented
---------------------
ASTM E112-13 "Standard Test Methods for Determining Average Grain Size"

* Planimetric (Jeffries) procedure, E112-13 section 11:
      N_A = (N_inside + 0.5 * N_intercepted) / A          (eq. 4)
  and, for a rectangular test field, the four corner grains are each
  counted 1/4 (section 11.3, eq. 5):
      N_A = (N_inside + 0.5 * N_intercepted + 1) / A
  Grain size number (Table 6 / eq. 1 of section 11.4):
      G = 3.321928 * log10(N_A) - 2.954            (N_A in grains / mm^2)

* Intercept (Heyn / Abrams) procedure, E112-13 sections 12-13:
      P_L = P / L,  l_bar = 1 / P_L                 (L in mm)
      G = -6.643856 * log10(l_bar) - 3.288          (l_bar in mm, eq. 10)
  Counting rules for intersections P (section 13.3.3 / Fig. 7):
      boundary crossing ................ 1
      tangent to a boundary ............ 1   (``TANGENT_WEIGHT``)
      apparent triple-point crossing ... 1.5 (``TRIPLE_WEIGHT``)
  Counting rule for intercepts N (section 13.3.2): a line end falling
  inside a grain scores 1/2.  For a single-phase structure P and N are
  equivalent; l_bar is computed from P (P_L), N is reported for audit.
  Test patterns: straight lines at 0/45/90/135 deg spaced >= 2x the mean
  grain diameter (E1382 practice, INN-26 spec), or the E112 three-circle
  pattern (section 13.4, circumferences in ratio 3:2:1, total 500 mm at
  100x).  Only parts of the pattern inside the valid mask contribute to
  the test length L (lines are broken at invalid regions).

ASTM E1382-97 (field statistics): individual intercept lengths, mean and
standard deviation per field; mean-area derived G (N_A = 1 / A_bar).

ASTM E930-99 (ALA, "as large as" grain): the largest grain's area A_max
converted through N_A = 1 / A_max -> G_ALA.

Twin boundaries are not distinguished (INN-34 archived); this is stated in
the method notes of every result.

Public API
----------
``G_from_NA``, ``G_from_mean_intercept``, ``G_from_mean_area``,
``planimetric``, ``intercept``, ``compute_astm`` -> ``AstmResult``,
``update_astm(result, params)``, ``astm_g_from_result(result)``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage as ndi

# E112-13 counting weights for intersections P (section 13.3.3).
CROSSING_WEIGHT = 1.0
TANGENT_WEIGHT = 1.0
TRIPLE_WEIGHT = 1.5

# E112-13 adequacy guidance.
MIN_GRAINS_PER_FIELD = 50        # section 11.2: >= 50 grains per field
MIN_INTERCEPTS_PER_FIELD = 50    # section 13.2: 50 intercepts per field
MIN_VALID_FRACTION = 0.50        # INN-26 adequacy: valid area >= 50 % frame
MAX_EQUIAXED_ASPECT = 1.5        # E112 section 1.4 / 16: equiaxed structures
MIN_INTERCEPT_PX = 10.0          # boundaries resolvable: l_bar >= ~10 px
MIN_COVERAGE_PCT = 80.0          # unsegmented area biases N_A low
MAX_METHOD_DELTA_G = 0.5         # planimetric vs intercept agreement

ASTM_METHODS = ("both", "planimetric", "intercept_lines", "intercept_circles")

TWIN_NOTE = "twin boundaries not distinguished"


# ======================================================================
# Formula anchors
# ======================================================================

def G_from_NA(n_a_per_mm2: float) -> Optional[float]:
    """E112-13 planimetric relation: G = 3.321928 log10(N_A) - 2.954.

    ``N_A`` in grains per mm^2 at 1x.  Anchors: N_A = 7.75 -> G 0.00,
    N_A = 1984 -> G 8.00.  Returns None for N_A <= 0.
    """
    if n_a_per_mm2 is None or not n_a_per_mm2 > 0:
        return None
    return 3.321928 * math.log10(n_a_per_mm2) - 2.954


def G_from_mean_intercept(l_bar_mm: float) -> Optional[float]:
    """E112-13 intercept relation (eq. 10): G = -6.643856 log10(l_bar) - 3.288.

    ``l_bar`` = mean lineal intercept length in mm.  Anchors:
    l_bar = 0.32 mm -> G 0.00, l_bar = 0.020 mm -> G 8.00.
    """
    if l_bar_mm is None or not l_bar_mm > 0:
        return None
    return -6.643856 * math.log10(l_bar_mm) - 3.288


def G_from_mean_area(a_bar_mm2: float) -> Optional[float]:
    """E112 / E1382: mean grain area A_bar = 1 / N_A -> G via N_A."""
    if a_bar_mm2 is None or not a_bar_mm2 > 0:
        return None
    return G_from_NA(1.0 / a_bar_mm2)


# ======================================================================
# Result container
# ======================================================================

@dataclass
class AstmResult:
    """Per-field ASTM E112 / E1382 result.  ``to_dict()`` is JSON-able and
    is what ``AnalysisResult.astm`` stores."""
    calibrated: bool = False
    reason: str = ""                     # why G is None, if it is
    method: str = "both"                 # requested method
    pattern: str = "lines"               # intercept pattern used
    primary_method: str = ""             # "planimetric" | "intercept" | ""
    G_primary: Optional[float] = None
    # planimetric (Jeffries)
    G_planimetric: Optional[float] = None
    N_A_per_mm2: Optional[float] = None
    N_inside: int = 0
    N_intercepted: int = 0
    N_corner: int = 0
    grains_counted: float = 0.0          # N_inside + 0.5 N_intercepted (+ corners)
    field_area_mm2: Optional[float] = None
    field_area_px: float = 0.0
    frame_area_px: float = 0.0
    valid_fraction: float = 0.0
    rectangular_field: bool = False
    # intercept (Heyn)
    G_intercept: Optional[float] = None
    mean_intercept_um: Optional[float] = None
    mean_intercept_px: Optional[float] = None
    std_intercept_um: Optional[float] = None
    P_L_per_mm: Optional[float] = None
    n_hits: float = 0.0                  # P (weighted intersections)
    n_crossings: int = 0
    n_triple: int = 0
    n_tangent: int = 0
    n_intercepts: float = 0.0            # N (end segments 1/2)
    n_segments: int = 0
    test_length_um: Optional[float] = None
    test_length_px: float = 0.0
    line_spacing_px: float = 0.0
    P_L_by_angle: Dict[str, float] = field(default_factory=dict)  # per px
    anisotropy_index: Optional[float] = None   # P_L(90)/P_L(0) (E112 sec. 16)
    intercept_lengths_um: List[float] = field(default_factory=list)
    # E1382 / E930
    G_mean_area: Optional[float] = None
    mean_area_um2: Optional[float] = None
    G_ALA: Optional[float] = None
    ALA_area_um2: Optional[float] = None
    ALA_grain_id: int = 0
    mean_aspect_ratio: Optional[float] = None
    coverage_pct: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    compliance: List[Dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _jsonable(asdict(self))

    @property
    def adequacy(self) -> List[Dict]:
        return self.compliance


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def _check(checks, name, status, detail):
    # status: PASS / WARN / FAIL, or INFO for statements that are not checks.
    checks.append({"check": name, "status": status,
                   "passed": status in ("PASS", "INFO"), "detail": detail})


# ======================================================================
# Planimetric (Jeffries) — E112-13 section 11
# ======================================================================

def _field_mask(labels, valid_mask):
    if valid_mask is None or valid_mask.shape != labels.shape:
        return np.ones(labels.shape, dtype=bool)
    return valid_mask.astype(bool, copy=False)


def planimetric(labels: np.ndarray, valid_mask: Optional[np.ndarray] = None,
                px_per_um: float = 0.0, border_tol_px: int = 2) -> dict:
    """Jeffries planimetric count (E112-13 sec. 11).

    The test field is the valid mask (whole frame if None).  A grain is
    *intercepted* (counted 1/2) if any of its pixels lies within
    ``border_tol_px`` of the field exterior - the frame edge or an invalid
    (black) region - otherwise it is *inside* (counted 1).  Invalid pixels
    are excluded from the field area A.  When the field is the full
    rectangular frame, the grains at the four corners are counted 1/4 each
    (sec. 11.3) instead of 1/2.

    Returns counts; ``N_A_per_mm2`` / ``G`` only when ``px_per_um > 0``.
    """
    lab = np.asarray(labels)
    fld = _field_mask(lab, valid_mask)
    h, w = lab.shape
    rect = bool(fld.all())
    area_px = float(np.count_nonzero(fld))
    nmax = int(lab.max()) if lab.size else 0
    out = dict(N_inside=0, N_intercepted=0, N_corner=0, grains_counted=0.0,
               field_area_px=area_px, rectangular_field=rect,
               field_area_mm2=None, N_A_per_mm2=None, G=None)
    if nmax <= 0 or area_px <= 0:
        return out

    # grains of the field (label pixels outside the field are ignored)
    in_field = np.where(fld, lab, 0)
    present = np.bincount(in_field.ravel(), minlength=nmax + 1) > 0
    present[0] = False

    # exterior = outside the field, padded by the frame border
    ext = np.pad(~fld, 1, constant_values=True)
    t = max(1, int(border_tol_px))
    ext = ndi.binary_dilation(ext, structure=np.ones((3, 3), bool),
                              iterations=t)[1:-1, 1:-1]
    touch = np.zeros(nmax + 1, dtype=bool)
    touch[np.unique(in_field[ext])] = True
    touch[0] = False
    touch &= present

    corners = set()
    if rect:
        k = t + 1
        for win in (in_field[:k, :k], in_field[:k, -k:],
                    in_field[-k:, :k], in_field[-k:, -k:]):
            v = win[win > 0]
            if v.size:
                corners.add(int(np.bincount(v).argmax()))
    n_corner = len(corners)
    n_int = int(np.count_nonzero(touch)) - n_corner
    n_in = int(np.count_nonzero(present & ~touch))
    counted = n_in + 0.5 * n_int + 0.25 * n_corner
    out.update(N_inside=n_in, N_intercepted=n_int, N_corner=n_corner,
               grains_counted=float(counted))
    if px_per_um and px_per_um > 0:
        a_mm2 = area_px / (px_per_um * 1000.0) ** 2
        out["field_area_mm2"] = a_mm2
        na = counted / a_mm2 if a_mm2 > 0 else 0.0
        out["N_A_per_mm2"] = na if na > 0 else None
        out["G"] = G_from_NA(na)
    return out


# ======================================================================
# Intercept (Heyn) — E112-13 sections 12-13
# ======================================================================

def _runs(a: np.ndarray):
    """Run-length encode 1-D array -> (values, starts, lengths)."""
    n = a.size
    if n == 0:
        return a[:0], np.zeros(0, int), np.zeros(0, int)
    change = np.flatnonzero(a[1:] != a[:-1]) + 1
    starts = np.concatenate(([0], change))
    lengths = np.diff(np.concatenate((starts, [n])))
    return a[starts], starts, lengths


_NB_R = np.array([0, -1, 1, 0, 0])
_NB_C = np.array([0, 0, 0, -1, 1])


def _neigh_labels(lab, rr, cc):
    """Distinct non-zero labels 4-adjacent (plus shape) to the given
    boundary pixels of the normalised 1-px boundary network.  Three or
    more grains there = the line passes through a junction at image
    resolution ("apparent triple point").  The plus shape (rather than
    3x3) keeps the junction zone ~1 px wide; on stationary Poisson-Voronoi
    and hexagonal mosaics it gives an intercept G within 0.1 of the
    analytic value (tests/test_astm_engine.py), whereas 3x3 over-counts
    triple points/tangents by ~+0.1 G."""
    h, w = lab.shape
    r = np.asarray(rr)[:, None] + _NB_R[None, :]
    c = np.asarray(cc)[:, None] + _NB_C[None, :]
    np.clip(r, 0, h - 1, out=r)
    np.clip(c, 0, w - 1, out=c)
    v = np.unique(lab[r, c])
    return v[v > 0]


def _walk_segment(lab, seq, rr, cc, step, closed, stats, chords):
    """Count E112 hits on one continuous valid segment of a test pattern.

    ``seq`` = labels sampled along the path, (rr, cc) the pixel coords.
    Updates ``stats`` in place and appends complete chord lengths (px).
    """
    n = seq.size
    stats["length_px"] += n * step
    stats["segments"] += 1
    if closed:
        # start at a transition so the wrap-around boundary is counted
        ch = np.flatnonzero(seq != np.roll(seq, 1))
        if ch.size == 0:
            return
        s = int(ch[0])
        seq = np.concatenate((np.roll(seq, -s), seq[s:s + 1]))
        rr = np.concatenate((np.roll(rr, -s), rr[s:s + 1]))
        cc = np.concatenate((np.roll(cc, -s), cc[s:s + 1]))
    vals, starts, lens = _runs(seq)
    g = np.flatnonzero(vals > 0)          # grain runs, gaps skipped
    if g.size == 0:
        return
    gl = vals[g]
    gs = starts[g]
    ge = starts[g] + lens[g]              # exclusive end
    glen = lens[g]

    # N (intercepts): merged grain runs, open ends inside a grain = 1/2
    merged = 1 + int(np.count_nonzero(gl[1:] != gl[:-1]))
    n_int = float(merged)
    if not closed:
        if seq[0] > 0:
            n_int -= 0.5
        if seq[-1] > 0:
            n_int -= 0.5
    else:
        n_int = float(merged - 1)
    stats["N"] += max(n_int, 0.0)

    hits = 0.0
    positions = []                        # crossing positions (px along path)
    k = 0
    m = g.size
    while k < m - 1:
        a, b = gl[k], gl[k + 1]
        # tangent: a <= 1-px graze of grain b between two runs of grain a
        if (a != b and k + 2 < m and gl[k + 2] == a and glen[k + 1] <= 1):
            hits += TANGENT_WEIGHT
            stats["tangent"] += 1
            k += 2
            continue
        i0, i1 = ge[k] - 1, gs[k + 1]     # last px of a, first px of b
        if a != b:
            # "apparent triple point": >= 3 grains around the boundary
            # pixels themselves (the gap run, or the a|b pixel pair)
            j0, j1 = (i0 + 1, i1) if i1 - i0 > 1 else (i0, i1 + 1)
            nb = _neigh_labels(lab, rr[j0:j1], cc[j0:j1])
            if nb.size >= 3:
                hits += TRIPLE_WEIGHT
                stats["triple"] += 1
            else:
                hits += CROSSING_WEIGHT
            stats["crossings"] += 1
            positions.append(0.5 * (i0 + i1 + 1) * step)
        elif i1 - i0 > 1:
            # same grain on both sides of a boundary gap: tangent if the
            # gap touches another grain, else an internal hole (no hit)
            nb = _neigh_labels(lab, rr[i0 + 1:i1], cc[i0 + 1:i1])
            if np.any(nb != a):
                hits += TANGENT_WEIGHT
                stats["tangent"] += 1
        k += 1
    stats["P"] += hits
    if len(positions) >= 2:
        chords.extend(np.diff(np.asarray(positions)).tolist())


def normalize_boundaries(labels: np.ndarray, fill_px: int = 3) -> np.ndarray:
    """Re-draw grain boundaries as 1-px zero lines.

    Segmentations differ in boundary style: watershed tilings have no
    boundary pixels (grains touch), threshold / groove pipelines leave 1-3 px
    zero-valued grooves.  Discrete intersection counting is biased by that
    style (touching grains inflate apparent triple points, thick grooves
    swallow grain tips), so before walking test lines the label image is
    normalised: zero gaps up to ``2*fill_px`` wide are closed by growing the
    neighbouring labels (3x3 grey dilation, ``fill_px`` passes), then every
    pixel whose right/lower neighbour is a different grain becomes a 1-px
    boundary.  Wider unlabelled areas stay 0 (crossing them between two
    different grains still counts one boundary).
    """
    work = np.asarray(labels).copy()
    for _ in range(max(0, int(fill_px))):
        z = work == 0
        if not z.any():
            break
        mx = ndi.maximum_filter(work, size=3)
        work[z] = mx[z]
    orig_zero = work == 0
    b = np.zeros(work.shape, dtype=bool)
    d = (work[:, :-1] != work[:, 1:]) & (work[:, :-1] > 0) & (work[:, 1:] > 0)
    b[:, :-1] |= d
    d = (work[:-1, :] != work[1:, :]) & (work[:-1, :] > 0) & (work[1:, :] > 0)
    b[:-1, :] |= d
    work[b] = 0
    work[orig_zero] = 0
    return work


def _line_paths(shape, angles_deg, spacing):
    """Straight test lines covering the frame, spaced ``spacing`` px."""
    h, w = shape
    corners = np.array([[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]], float)
    for ang in angles_deg:
        th = math.radians(ang)
        u = np.array([math.cos(th), -math.sin(th)])   # x right, y down; +ang = CCW
        nrm = np.array([-u[1], u[0]])
        proj = corners @ nrm
        cmin, cmax = proj.min(), proj.max()
        span = cmax - cmin
        nlines = max(1, int(span // spacing))
        c0 = cmin + (span - (nlines - 1) * spacing) / 2.0
        for i in range(nlines):
            c = c0 + i * spacing
            p = c * nrm
            # clip p + t u to the frame [0, w-1] x [0, h-1]
            tlo, thi = -np.inf, np.inf
            for d, lo, hi, pv in ((u[0], 0.0, w - 1.0, p[0]),
                                  (u[1], 0.0, h - 1.0, p[1])):
                if abs(d) < 1e-12:
                    if pv < lo - 1e-9 or pv > hi + 1e-9:
                        tlo, thi = 1.0, 0.0
                    continue
                t1, t2 = (lo - pv) / d, (hi - pv) / d
                tlo, thi = max(tlo, min(t1, t2)), min(thi, max(t1, t2))
            if thi - tlo < 1.0:
                continue
            t = np.arange(tlo, thi + 1e-9, 1.0)
            x = np.clip(np.rint(p[0] + t * u[0]).astype(np.int64), 0, w - 1)
            y = np.clip(np.rint(p[1] + t * u[1]).astype(np.int64), 0, h - 1)
            yield ang, y, x, 1.0, False


def _circle_paths(fld, diameter_frac=0.9):
    """E112 three-circle pattern (sec. 13.4): concentric circles with
    circumferences in ratio 3:2:1, largest diameter = ``diameter_frac``
    x the smaller frame dimension, centred on the valid-field centroid
    (frame centre if that would push the circles off the frame)."""
    h, w = fld.shape
    r_max = 0.5 * diameter_frac * min(h, w)
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    if fld.any() and not fld.all():
        ys, xs = np.nonzero(fld)
        my, mx = ys.mean(), xs.mean()
        if r_max <= my <= h - 1 - r_max and r_max <= mx <= w - 1 - r_max:
            cy, cx = my, mx
    for frac in (1.0, 2.0 / 3.0, 1.0 / 3.0):
        r = r_max * frac
        n = max(8, int(math.ceil(2 * math.pi * r)))
        a = np.arange(n) * (2 * math.pi / n)
        x = np.clip(np.rint(cx + r * np.cos(a)).astype(np.int64), 0, w - 1)
        y = np.clip(np.rint(cy - r * np.sin(a)).astype(np.int64), 0, h - 1)
        yield "circle", y, x, 2 * math.pi * r / n, True


def _mean_ecd_px(labels, fld):
    lab = np.where(fld, labels, 0)
    areas = np.bincount(lab.ravel())[1:]
    areas = areas[areas > 0]
    if areas.size == 0:
        return 0.0
    return float(np.sqrt(4.0 * areas.mean() / math.pi))


def intercept(labels: np.ndarray, valid_mask: Optional[np.ndarray] = None,
              px_per_um: float = 0.0, pattern: str = "lines",
              spacing_px: Optional[float] = None, spacing_factor: float = 2.0,
              angles_deg: Sequence[float] = (0, 45, 90, 135),
              boundary_fill_px: int = 3) -> dict:
    """Heyn lineal-intercept count (E112-13 sec. 12-13).

    Test lines (``pattern="lines"``, angles ``angles_deg``, spacing
    ``spacing_px`` or ``spacing_factor`` x mean ECD, min 4 px) or the three
    circles (``pattern="circles"``) are sampled at 1-px steps; samples in
    invalid pixels are dropped and the path is split there, so the test
    length L covers the valid field only.  A hit is a transition between two
    different grain labels (a zero-label boundary run between them counts
    once); weights: crossing 1, tangent 1, apparent triple point 1.5.
    """
    lab = np.asarray(labels)
    fld = _field_mask(lab, valid_mask)
    stats = dict(length_px=0.0, P=0.0, N=0.0, crossings=0, triple=0,
                 tangent=0, segments=0)
    chords: List[float] = []
    per_angle: Dict[str, List[float]] = {}
    spacing = 0.0
    if pattern == "circles":
        paths = _circle_paths(fld)
    else:
        spacing = float(spacing_px) if spacing_px else \
            max(4.0, spacing_factor * _mean_ecd_px(lab, fld))
        paths = _line_paths(lab.shape, angles_deg, spacing)

    wlab = normalize_boundaries(lab, boundary_fill_px)
    for key, rr, cc, step, closed in paths:
        seq = wlab[rr, cc]
        ok = fld[rr, cc]
        before = (stats["P"], stats["length_px"])
        if ok.all():
            _walk_segment(wlab, seq, rr, cc, step, closed, stats, chords)
        elif ok.any():
            if closed:  # rotate so the path starts on an invalid sample
                s = int(np.argmin(ok))
                seq, rr, cc, ok = (np.roll(v, -s) for v in (seq, rr, cc, ok))
            vals, starts, lens = _runs(ok.astype(np.int8))
            for v, s0, ln in zip(vals, starts, lens):
                if v and ln >= 2:
                    sl = slice(s0, s0 + ln)
                    _walk_segment(wlab, seq[sl], rr[sl], cc[sl], step, False,
                                  stats, chords)
        acc = per_angle.setdefault(str(key), [0.0, 0.0])
        acc[0] += stats["P"] - before[0]
        acc[1] += stats["length_px"] - before[1]

    L = stats["length_px"]
    P = stats["P"]
    out = dict(pattern=pattern, spacing_px=spacing, test_length_px=L,
               n_hits=P, n_intercepts=stats["N"],
               n_crossings=stats["crossings"], n_triple=stats["triple"],
               n_tangent=stats["tangent"], n_segments=stats["segments"],
               P_L_by_angle={k: (v[0] / v[1] if v[1] > 0 else 0.0)
                             for k, v in per_angle.items()},
               intercept_lengths_px=chords,
               mean_intercept_px=(L / P) if P > 0 else None,
               test_length_um=None, mean_intercept_um=None,
               P_L_per_mm=None, G=None)
    if px_per_um and px_per_um > 0 and P > 0 and L > 0:
        l_um = L / P / px_per_um
        out.update(test_length_um=L / px_per_um, mean_intercept_um=l_um,
                   P_L_per_mm=1000.0 / l_um,
                   G=G_from_mean_intercept(l_um / 1000.0))
    return out


def count_path(labels: np.ndarray, rows, cols, step: float = 1.0,
               closed: bool = False, normalize: bool = True) -> dict:
    """E112 counts along one explicit path (pixel ``rows``/``cols``).

    Exposes the counting rules for auditing and for overlays that mark
    each hit: returns ``{P, N, crossings, triple, tangent, length_px,
    intercept_lengths_px}``.  ``normalize=False`` walks the label image
    as given (hand-built test images)."""
    lab = np.asarray(labels)
    wlab = normalize_boundaries(lab) if normalize else lab
    rr = np.asarray(rows, dtype=np.int64)
    cc = np.asarray(cols, dtype=np.int64)
    stats = dict(length_px=0.0, P=0.0, N=0.0, crossings=0, triple=0,
                 tangent=0, segments=0)
    chords: List[float] = []
    _walk_segment(wlab, wlab[rr, cc], rr, cc, float(step), closed, stats,
                  chords)
    stats["intercept_lengths_px"] = chords
    return stats


# ======================================================================
# Combined per-field engine
# ======================================================================

def compute_astm(labels: Optional[np.ndarray],
                 valid_mask: Optional[np.ndarray] = None,
                 px_per_um: float = 0.0,
                 grains: Optional[Sequence] = None,
                 method: str = "both",
                 pattern: Optional[str] = None,
                 spacing_factor: float = 2.0,
                 spacing_px: Optional[float] = None,
                 border_tol_px: int = 2,
                 max_intercepts_stored: int = 20000) -> AstmResult:
    """Full E112 / E1382 evaluation of one field.

    ``method``: "both" (planimetric + intercept lines, planimetric primary),
    "planimetric", "intercept_lines", "intercept_circles".  ``grains`` (list
    of ``GrainResult``) is used for the equiaxed check (mean aspect ratio).
    Without calibration all G values are None and ``reason`` says why;
    counts are still reported.
    """
    method = method if method in ASTM_METHODS else "both"
    if pattern is None:
        pattern = "circles" if method == "intercept_circles" else "lines"
    ppu = float(px_per_um or 0.0)
    res = AstmResult(method=method, pattern=pattern, calibrated=ppu > 0)
    res.notes.append(TWIN_NOTE)
    checks: List[Dict] = []

    if labels is None or np.asarray(labels).size == 0:
        res.reason = "no label image"
        _check(checks, "Label image present", "FAIL", "no segmentation")
        res.compliance = checks
        return res
    lab = np.asarray(labels)
    if lab.dtype.kind not in "iu":
        lab = lab.astype(np.int32)
    fld = _field_mask(lab, valid_mask)
    res.frame_area_px = float(lab.size)

    do_plan = method in ("both", "planimetric")
    do_int = method in ("both", "intercept_lines", "intercept_circles")

    # --- planimetric -------------------------------------------------
    pl = planimetric(lab, fld, ppu, border_tol_px)
    res.N_inside = pl["N_inside"]
    res.N_intercepted = pl["N_intercepted"]
    res.N_corner = pl["N_corner"]
    res.grains_counted = pl["grains_counted"]
    res.field_area_px = pl["field_area_px"]
    res.field_area_mm2 = pl["field_area_mm2"]
    res.rectangular_field = pl["rectangular_field"]
    res.valid_fraction = res.field_area_px / res.frame_area_px
    if do_plan:
        res.N_A_per_mm2 = pl["N_A_per_mm2"]
        res.G_planimetric = pl["G"]
        res.notes.append(
            "Planimetric (Jeffries, E112-13 sec. 11): grains inside = 1, "
            "grains cut by the field/valid-area border = 1/2"
            + (", 4 corner grains = 1/4" if res.rectangular_field else "")
            + "; field area excludes invalid regions.")

    # --- per-grain areas (E1382 mean area, E930 ALA) ----------------
    areas = np.bincount(np.where(fld, lab, 0).ravel())
    areas[0] = 0
    ids = np.flatnonzero(areas)
    if ids.size:
        res.coverage_pct = float(areas.sum() / max(res.field_area_px, 1) * 100)
    if ids.size and ppu > 0:
        a_um2 = areas[ids] / ppu ** 2
        res.mean_area_um2 = float(a_um2.mean())
        res.G_mean_area = G_from_mean_area(res.mean_area_um2 * 1e-6)
        j = int(np.argmax(a_um2))
        res.ALA_grain_id = int(ids[j])
        res.ALA_area_um2 = float(a_um2[j])
        res.G_ALA = G_from_mean_area(res.ALA_area_um2 * 1e-6)

    # --- intercept ---------------------------------------------------
    it = None
    if do_int:
        it = intercept(lab, fld, ppu, pattern=pattern, spacing_px=spacing_px,
                       spacing_factor=spacing_factor)
        res.pattern = it["pattern"]
        res.n_hits = float(it["n_hits"])
        res.n_intercepts = float(it["n_intercepts"])
        res.n_crossings = it["n_crossings"]
        res.n_triple = it["n_triple"]
        res.n_tangent = it["n_tangent"]
        res.n_segments = it["n_segments"]
        res.test_length_px = it["test_length_px"]
        res.line_spacing_px = it["spacing_px"]
        res.P_L_by_angle = it["P_L_by_angle"]
        res.mean_intercept_px = it["mean_intercept_px"]
        pa = it["P_L_by_angle"]
        if pa.get("0", 0) > 0 and "90" in pa:
            res.anisotropy_index = pa["90"] / pa["0"]
        if ppu > 0:
            res.test_length_um = it["test_length_um"]
            res.mean_intercept_um = it["mean_intercept_um"]
            res.P_L_per_mm = it["P_L_per_mm"]
            res.G_intercept = it["G"]
            ch = np.asarray(it["intercept_lengths_px"], float) / ppu
            if ch.size >= 2:
                res.std_intercept_um = float(np.std(ch, ddof=1))
            res.intercept_lengths_um = [round(float(v), 4) for v in
                                        ch[:max_intercepts_stored]]
        res.notes.append(
            ("Intercept (Heyn, E112-13 sec. 13): "
             + ("three concentric circles (3:2:1)" if res.pattern == "circles"
                else f"lines at 0/45/90/135 deg, spacing {res.line_spacing_px:.1f} px")
             + "; test length restricted to the valid field; "
               "crossing = 1, tangent = 1, triple point = 1.5."))

    # --- primary G -----------------------------------------------------
    if res.G_planimetric is not None:
        res.primary_method, res.G_primary = "planimetric", res.G_planimetric
    elif res.G_intercept is not None:
        res.primary_method, res.G_primary = "intercept", res.G_intercept
    if ppu <= 0:
        res.reason = "uncalibrated: set the scale (px/um) to compute ASTM G"
    elif res.G_primary is None:
        res.reason = "no grains / intercepts counted in the valid field"

    # --- aspect ratio --------------------------------------------------
    if grains:
        ar = [float(getattr(g, "aspect_ratio", 0.0) or 0.0) for g in grains]
        ar = [v for v in ar if v > 0]
        if ar:
            res.mean_aspect_ratio = float(np.mean(ar))

    # --- compliance checklist -----------------------------------------
    _check(checks, "Calibration present", "PASS" if ppu > 0 else "FAIL",
           f"{ppu:.4g} px/um" if ppu > 0 else "no scale: G not computed")
    if do_plan:
        ng = res.N_inside + res.N_intercepted + res.N_corner
        _check(checks, f">= {MIN_GRAINS_PER_FIELD} grains per field "
                       "(planimetric)",
               "PASS" if ng >= MIN_GRAINS_PER_FIELD else "WARN",
               f"{ng} grains in field (E112 sec. 11: >= 50 recommended)")
        _check(checks, "Border grains counted 1/2", "INFO",
               f"{res.N_inside} inside x1, {res.N_intercepted} cut by border"
               f" x1/2" + (f", {res.N_corner} corners x1/4"
                           if res.N_corner else ""))
    if do_int:
        _check(checks, f">= {MIN_INTERCEPTS_PER_FIELD} intercepts per field",
               "PASS" if res.n_hits >= MIN_INTERCEPTS_PER_FIELD else "WARN",
               f"{res.n_hits:g} intersections on {res.test_length_px:.0f} px "
               f"of test pattern (E112 sec. 13: >= 50 recommended)")
    _check(checks, f"Valid area >= {MIN_VALID_FRACTION:.0%} of frame",
           "PASS" if res.valid_fraction >= MIN_VALID_FRACTION else "WARN",
           f"{res.valid_fraction * 100:.1f} % of frame analysed")
    if res.coverage_pct is not None:
        _check(checks, f"Segmented coverage >= {MIN_COVERAGE_PCT:.0f} %",
               "PASS" if res.coverage_pct >= MIN_COVERAGE_PCT else "WARN",
               f"{res.coverage_pct:.1f} % of valid field assigned to grains")
    lpx = res.mean_intercept_px
    if lpx is None and ids.size:
        lpx = 0.8 * _mean_ecd_px(lab, fld)
    if lpx:
        _check(checks, "Magnification adequate (grains resolved)",
               "PASS" if lpx >= MIN_INTERCEPT_PX else "WARN",
               f"mean intercept {lpx:.1f} px (>= {MIN_INTERCEPT_PX:.0f} px "
               "needed to resolve boundaries)")
    if res.mean_aspect_ratio is not None:
        _check(checks, "Equiaxed structure",
               "PASS" if res.mean_aspect_ratio <= MAX_EQUIAXED_ASPECT
               else "WARN",
               f"mean aspect ratio {res.mean_aspect_ratio:.2f} "
               f"(> {MAX_EQUIAXED_ASPECT} -> use E112 sec. 16 oriented method)")
    if res.G_planimetric is not None and res.G_intercept is not None:
        d = abs(res.G_planimetric - res.G_intercept)
        _check(checks, "Planimetric vs intercept agreement",
               "PASS" if d <= MAX_METHOD_DELTA_G else "WARN",
               f"|dG| = {d:.2f} (<= {MAX_METHOD_DELTA_G})")
    res.compliance = checks
    return res


def update_astm(result, params=None) -> dict:
    """(Re)compute ``result.astm`` / ``result.astm_g`` from the result's
    label image and valid mask.  Call after the grain list/labels are
    edited.  Returns the stored dict."""
    method = getattr(params, "astm_method", "both") if params else "both"
    factor = getattr(params, "astm_pattern_spacing_factor", 2.0) \
        if params else 2.0
    ppu = float(getattr(result, "px_per_um", 0.0) or 0.0)
    if not getattr(result, "has_calibration", ppu > 0):
        ppu = 0.0
    ar = compute_astm(getattr(result, "label_image", None),
                      getattr(result, "valid_mask", None), ppu,
                      grains=getattr(result, "grains", None),
                      method=method, spacing_factor=factor)
    d = ar.to_dict()
    result.astm = d
    result.astm_g = d.get("G_primary")
    return d


def astm_g_from_result(result) -> Optional[float]:
    """Primary ASTM G of an ``AnalysisResult`` (None if uncalibrated).

    Uses the stored ``astm_g`` / ``astm`` when present, otherwise computes
    it from the label image (results produced before DET-04)."""
    g = getattr(result, "astm_g", None)
    if g is not None:
        return float(g)
    d = getattr(result, "astm", None)
    if isinstance(d, dict) and d:
        return d.get("G_primary")
    if getattr(result, "label_image", None) is None:
        return None
    ppu = float(getattr(result, "px_per_um", 0.0) or 0.0)
    if ppu <= 0:
        return None
    return compute_astm(result.label_image, getattr(result, "valid_mask", None),
                        ppu, grains=getattr(result, "grains", None)).G_primary
