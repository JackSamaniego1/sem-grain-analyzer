"""
Calibration verification against a certified reference standard (INN-29).

Measures the pitch of a periodic reference specimen (line grating, square
grid, stage micrometer) imaged in the SEM and compares it with the pitch on
the standard's certificate. This is the magnification check described in
ASTM E766 ("Standard Practice for Calibrating the Magnification of a
Scanning Electron Microscope") and ISO 16700: image a traceable pitch
standard at the working magnification, measure the image pitch, and the
relative difference to the certified pitch is the scale error.

Algorithm (pure numpy / scipy / cv2, no Qt, no network):

1. Grayscale; invalid (large pure-black) pixels from the DET-01 valid mask
   are replaced by the mean of the valid pixels so they add no spectral
   energy; mean removed; 2-D Hann window (suppresses spectral leakage from
   the frame edges, which would otherwise dominate the low frequencies).
2. 2-D FFT magnitude, DC neighbourhood and periods < 2.5 px suppressed;
   the strongest peak in the upper half-plane is the fundamental of the
   dominant line family. Sub-bin frequency: parabolic fit on the
   log-magnitude (exact for the Gaussian-like Hann main lobe), then refined
   by maximising the continuous-frequency DTFT magnitude
   ``|sum w f exp(-2 pi i k.r)|`` - for a single sinusoid this is the
   maximum-likelihood frequency estimate and removes the residual
   interpolation bias. ``period_px = 1 / |k|`` (k in cycles/px).
   For a square grid the second, orthogonal (90 +/- 20 deg) peak is also
   measured and the two periods are averaged.
3. Cross-check: the (window-normalised) autocorrelation is sampled along
   the wave-vector direction; its first maximum is the period in the
   spatial domain. FFT and ACF disagreeing by more than 1 % -> "low"
   confidence and the UI should offer the manual fallback.
4. ``measured_pitch_um = period_px / px_per_um``;
   ``error_pct = 100 (measured - certified) / certified``;
   PASS if ``|error_pct| <= tolerance_pct`` (compared after rounding to
   1e-9 % so a nominal 2.0 % error passes a 2 % limit despite float noise).
5. Uncertainty (GUM, simple RSS of standard uncertainties, reported as an
   expanded uncertainty with k = 2): certificate U / 2; pitch-fit
   repeatability = std-dev of the period over 3 sub-images; pixel
   quantisation = 0.5 px over the measured span, rectangular -> / sqrt(3).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from scipy import fft as sfft
from scipy import ndimage as ndi
from scipy import optimize

PATTERN_AUTO = "auto"
PATTERN_GRATING = "line grating"
PATTERN_GRID = "square grid"
PATTERN_MICROMETER = "stage micrometer"
PATTERN_TYPES = (PATTERN_GRATING, PATTERN_GRID, PATTERN_MICROMETER)

DEFAULT_TOLERANCE_PCT = 2.0
LOW_CONFIDENCE_DISAGREEMENT_PCT = 1.0
MIN_PERIOD_PX = 2.5
# Peak must stand this far above the median spectrum level to count as a
# periodic pattern at all.
MIN_PEAK_PROMINENCE = 8.0
# A second orthogonal peak this strong (relative to the first) = grid.
GRID_SECOND_PEAK_RATIO = 0.25


# ======================================================================
# Result types
# ======================================================================

@dataclass
class AxisMeasurement:
    """One line family (wave vector) of the reference pattern."""
    period_px: float = 0.0
    wave_angle_deg: float = 0.0      # direction of the wave vector, [0, 180)
    line_angle_deg: float = 0.0      # direction the lines run (wave + 90)
    kx: float = 0.0                  # cycles / px
    ky: float = 0.0
    phase_rad: float = 0.0           # crest where 2 pi k.r + phase = 2 pi m
    strength: float = 0.0            # peak magnitude / median magnitude
    acf_period_px: float = 0.0
    disagreement_pct: float = 0.0    # 100 |fft - acf| / fft
    sub_periods_px: List[float] = field(default_factory=list)


@dataclass
class PitchResult:
    period_px: float = 0.0
    pattern: str = PATTERN_GRATING
    axes: List[AxisMeasurement] = field(default_factory=list)
    confidence: str = "high"         # "high" | "low"
    warnings: List[str] = field(default_factory=list)
    repeatability_px: float = 0.0    # std-dev over sub-images
    span_px: float = 0.0             # extent along the wave vector
    method: str = "fft"
    px_per_um: Optional[float] = None
    measured_pitch_um: Optional[float] = None
    repeatability_um: Optional[float] = None

    @property
    def low_confidence(self) -> bool:
        return self.confidence != "high"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationResult:
    measured_pitch_um: float
    certified_pitch_um: float
    error_pct: float
    tolerance_pct: float
    passed: bool
    method: str = "fft"
    uncertainty: dict = field(default_factory=dict)
    pitch: Optional[PitchResult] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ======================================================================
# Pass / fail and uncertainty
# ======================================================================

def error_percent(measured_pitch_um: float, certified_pitch_um: float) -> float:
    """Signed scale error in percent: positive = image pitch too large."""
    if certified_pitch_um is None or certified_pitch_um <= 0:
        raise ValueError("certified pitch must be > 0")
    return 100.0 * (float(measured_pitch_um) - float(certified_pitch_um)) / float(certified_pitch_um)


def passes_tolerance(error_pct: float, tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> bool:
    """``|error| <= tolerance``; compared at 1e-9 % resolution so float noise
    (e.g. 1.02 / 1.00 -> 2.0000000000000018 %) does not fail a nominal
    boundary value."""
    return round(abs(float(error_pct)), 9) <= round(float(tolerance_pct), 9)


def uncertainty_budget(measured_pitch_um: float, *, cert_expanded_um: float = 0.0,
                       repeatability_um: float = 0.0, span_px: float = 0.0,
                       coverage_k: float = 2.0) -> dict:
    """GUM-style RSS budget (all components as standard uncertainties, µm).

    * certificate: expanded U (k=2) on the certificate -> U / 2
    * repeatability: std-dev of the pitch over sub-images (type A)
    * quantisation: +/-0.5 px uncertainty of the end points over the
      measured span, rectangular distribution -> (0.5 / span) * pitch / sqrt(3)
    """
    u_cert = max(0.0, float(cert_expanded_um)) / 2.0
    u_rep = max(0.0, float(repeatability_um))
    u_q = 0.0
    if span_px and span_px > 0:
        u_q = (0.5 / float(span_px)) * float(measured_pitch_um) / math.sqrt(3.0)
    u_c = math.sqrt(u_cert ** 2 + u_rep ** 2 + u_q ** 2)
    U = coverage_k * u_c
    rel = 100.0 * U / measured_pitch_um if measured_pitch_um else 0.0
    return {
        "u_certificate_um": u_cert,
        "u_repeatability_um": u_rep,
        "u_quantisation_um": u_q,
        "u_combined_um": u_c,
        "coverage_k": coverage_k,
        "expanded_um": U,
        "expanded_pct": rel,
    }


# ======================================================================
# Image preparation
# ======================================================================

def _to_gray(image) -> np.ndarray:
    img = np.asarray(image)
    if img.ndim == 3:
        if img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _prepare(gray: np.ndarray, valid: Optional[np.ndarray]):
    """Fill invalid pixels with the valid mean, remove the mean, apply a
    2-D Hann window. Returns (windowed image, window * valid)."""
    g = gray.astype(np.float64)
    h, w = g.shape
    if valid is not None and valid.any() and not valid.all():
        mean = float(g[valid].mean())
        g = np.where(valid, g, mean)
    else:
        mean = float(g.mean())
    g = g - mean
    win = np.outer(np.hanning(h), np.hanning(w))
    wv = win if valid is None else win * valid.astype(np.float64)
    return g * win, wv


def _dtft_mag(f: np.ndarray, kx: float, ky: float) -> complex:
    h, w = f.shape
    ax = -2.0 * np.pi * kx * np.arange(w)
    ey = np.exp(-2j * np.pi * ky * np.arange(h))
    # two real mat-vecs (BLAS) instead of promoting f to complex each call
    row = f @ np.cos(ax) + 1j * (f @ np.sin(ax))
    return complex(ey @ row)


def _refine_peak(f: np.ndarray, kx0: float, ky0: float) -> Tuple[float, float, complex]:
    """Maximise |DTFT| around (kx0, ky0) (cycles/px). Optimised in bin units."""
    h, w = f.shape

    def neg(p):
        return -abs(_dtft_mag(f, p[0] / w, p[1] / h))

    res = optimize.minimize(neg, x0=[kx0 * w, ky0 * h], method="Nelder-Mead",
                            options={"xatol": 1e-5, "fatol": 1e-9 * max(1.0, abs(neg([kx0 * w, ky0 * h]))),
                                     "initial_simplex": [[kx0 * w, ky0 * h],
                                                         [kx0 * w + 0.3, ky0 * h],
                                                         [kx0 * w, ky0 * h + 0.3]],
                                     "maxiter": 400})
    kx, ky = res.x[0] / w, res.x[1] / h
    return kx, ky, _dtft_mag(f, kx, ky)


def _spectrum(f: np.ndarray):
    """Shifted magnitude spectrum with DC / too-high frequencies / lower
    half-plane zeroed. Returns (mag, fx grid, fy grid, median level)."""
    h, w = f.shape
    F = np.fft.fftshift(sfft.fft2(f, workers=-1))
    mag = np.abs(F)
    fy = np.fft.fftshift(np.fft.fftfreq(h))[:, None]
    fx = np.fft.fftshift(np.fft.fftfreq(w))[None, :]
    r = np.sqrt(fx ** 2 + fy ** 2)
    med = float(np.median(mag[r > 0])) or 1e-12
    rmin = 3.0 / min(h, w)
    bad = (r < rmin) | (r > 1.0 / MIN_PERIOD_PX)
    half = (fy > 0) | ((fy == 0) & (fx > 0))
    mag = np.where(bad | ~half, 0.0, mag)
    return mag, fx, fy, med


def _parabolic(lm1: float, l0: float, lp1: float) -> float:
    den = lm1 - 2.0 * l0 + lp1
    if den >= 0 or not np.isfinite(den):
        return 0.0
    d = 0.5 * (lm1 - lp1) / den
    return float(np.clip(d, -0.5, 0.5))


def _coarse_peak(mag: np.ndarray, fx, fy, iy: int, ix: int) -> Tuple[float, float]:
    h, w = mag.shape
    logm = np.log(np.maximum(mag, 1e-12))
    dx = dy = 0.0
    if 0 < ix < w - 1 and mag[iy, ix - 1] > 0 and mag[iy, ix + 1] > 0:
        dx = _parabolic(logm[iy, ix - 1], logm[iy, ix], logm[iy, ix + 1])
    if 0 < iy < h - 1 and mag[iy - 1, ix] > 0 and mag[iy + 1, ix] > 0:
        dy = _parabolic(logm[iy - 1, ix], logm[iy, ix], logm[iy + 1, ix])
    return float(fx[0, ix]) + dx / w, float(fy[iy, 0]) + dy / h


def _wave_angle(kx: float, ky: float) -> float:
    return float(math.degrees(math.atan2(ky, kx)) % 180.0)


def _acf_period(f: np.ndarray, wv: np.ndarray, kx: float, ky: float, period: float) -> float:
    """Period from the first autocorrelation maximum along the wave vector.
    The ACF is divided by the ACF of the window so the window envelope does
    not bias the peak toward shorter lags."""
    h, w = f.shape
    rad = int(math.ceil(1.6 * period)) + 4
    if rad >= min(h, w) // 2:
        return float("nan")
    # zero-padding by rad (not 2x) is enough: lags <= rad never wrap
    s = (sfft.next_fast_len(h + rad + 1, True), sfft.next_fast_len(w + rad + 1, True))

    def _acf(a):
        A = sfft.rfft2(a, s=s, workers=-1)
        return sfft.irfft2(A.real ** 2 + A.imag ** 2, s=s, workers=-1)

    acf, acw = _acf(f), _acf(wv)
    ri = np.arange(-rad, rad + 1)
    ix = np.ix_(ri % s[0], ri % s[1])
    wcrop = acw[ix]
    norm = acf[ix] / np.maximum(wcrop, 1e-12 * float(acw[0, 0]))
    ux, uy = kx * period, ky * period  # unit vector along k
    t = np.arange(0.5 * period, 1.5 * period, 0.02)
    coords = np.vstack([rad + t * uy, rad + t * ux])
    prof = ndi.map_coordinates(norm, coords, order=3, mode="nearest")
    i = int(np.argmax(prof))
    if 0 < i < len(t) - 1:
        d = _parabolic(prof[i - 1], prof[i], prof[i + 1])
        return float(t[i] + d * 0.02)
    return float(t[i])


def _sub_images(shape, n: int) -> List[Tuple[slice, slice]]:
    h, w = shape
    out = []
    if w >= h:
        edges = np.linspace(0, w, n + 1).astype(int)
        for a, b in zip(edges[:-1], edges[1:]):
            out.append((slice(0, h), slice(a, b)))
    else:
        edges = np.linspace(0, h, n + 1).astype(int)
        for a, b in zip(edges[:-1], edges[1:]):
            out.append((slice(a, b), slice(0, w)))
    return out


# ======================================================================
# Public API
# ======================================================================

def measure_pitch(image, *, px_per_um: Optional[float] = None,
                  pattern: str = PATTERN_AUTO,
                  valid_mask: Optional[np.ndarray] = None,
                  black_threshold: int = 12,
                  n_subimages: int = 3,
                  progress=None) -> PitchResult:
    """Measure the period (px) of a periodic reference image.

    ``pattern``: ``"auto"`` (grid if an orthogonal second peak is found),
    ``"line grating"``/``"stage micrometer"`` (dominant direction only) or
    ``"square grid"`` (average of the two orthogonal periods).
    ``valid_mask`` defaults to DET-01 ``compute_valid_mask(gray,
    black_threshold)``. ``progress(pct, msg)`` is optional.
    Raises ``ValueError`` if no periodic pattern is present.
    """
    def _p(pct, msg):
        if progress is not None:
            progress(pct, msg)

    gray = _to_gray(image)
    if gray.ndim != 2 or min(gray.shape) < 32:
        raise ValueError("image too small for pitch measurement")
    if valid_mask is None:
        from core.grain_detector import compute_valid_mask
        valid_mask = compute_valid_mask(gray, threshold=black_threshold)
    valid = np.asarray(valid_mask, dtype=bool)
    if valid.shape != gray.shape:
        raise ValueError("valid_mask shape does not match image")
    if valid.mean() < 0.1:
        raise ValueError("less than 10 % of the image carries signal")

    _p(5, "Computing spectrum")
    f, wv = _prepare(gray, valid)
    mag, fx, fy, med = _spectrum(f)
    iy, ix = np.unravel_index(int(np.argmax(mag)), mag.shape)
    strength1 = float(mag[iy, ix]) / med
    if strength1 < MIN_PEAK_PROMINENCE:
        raise ValueError("no periodic pattern found in the image")

    peaks = [(iy, ix, strength1)]
    want_grid = pattern in (PATTERN_AUTO, PATTERN_GRID)
    warnings: List[str] = []
    if want_grid:
        a1 = _wave_angle(float(fx[0, ix]), float(fy[iy, 0]))
        ang = np.degrees(np.arctan2(np.broadcast_to(fy, mag.shape),
                                    np.broadcast_to(fx, mag.shape))) % 180.0
        dang = np.abs(((ang - a1) % 180.0) - 90.0)
        cand = np.where(dang < 20.0, mag, 0.0)
        iy2, ix2 = np.unravel_index(int(np.argmax(cand)), cand.shape)
        s2 = float(cand[iy2, ix2]) / med
        if s2 >= MIN_PEAK_PROMINENCE and cand[iy2, ix2] >= GRID_SECOND_PEAK_RATIO * mag[iy, ix]:
            peaks.append((iy2, ix2, s2))
        elif pattern == PATTERN_GRID:
            warnings.append("second (orthogonal) grid axis not found")
    detected = PATTERN_GRID if len(peaks) == 2 else (
        pattern if pattern in (PATTERN_GRATING, PATTERN_MICROMETER) else PATTERN_GRATING)
    if pattern == PATTERN_GRID:
        detected = PATTERN_GRID

    subs = _sub_images(gray.shape, max(1, int(n_subimages)))
    sub_prepped = []
    if n_subimages >= 2:
        for sy, sx in subs:
            sv = valid[sy, sx]
            sub_prepped.append(_prepare(gray[sy, sx], sv)[0] if sv.mean() >= 0.1 else None)

    axes: List[AxisMeasurement] = []
    for n, (py, px_, s) in enumerate(peaks):
        _p(15 + 35 * n, f"Refining peak {n + 1}")
        kx0, ky0 = _coarse_peak(mag, fx, fy, py, px_)
        kx, ky, F = _refine_peak(f, kx0, ky0)
        if ky < 0 or (ky == 0 and kx < 0):
            kx, ky = -kx, -ky
            F = F.conjugate()
        knorm = math.hypot(kx, ky)
        period = 1.0 / knorm
        acf_p = _acf_period(f, wv, kx, ky, period)
        dis = 100.0 * abs(period - acf_p) / period if np.isfinite(acf_p) else float("inf")
        sub_p: List[float] = []
        for sf in sub_prepped:
            if sf is None:
                continue
            skx, sky, _ = _refine_peak(sf, kx, ky)
            sk = math.hypot(skx, sky)
            if sk > 0:
                sub_p.append(1.0 / sk)
        wa = _wave_angle(kx, ky)
        axes.append(AxisMeasurement(
            period_px=period, wave_angle_deg=wa, line_angle_deg=(wa + 90.0) % 180.0,
            kx=kx, ky=ky, phase_rad=float(np.angle(F)), strength=s,
            acf_period_px=acf_p, disagreement_pct=dis, sub_periods_px=sub_p))

    _p(90, "Cross-checking")
    period = float(np.mean([a.period_px for a in axes]))
    # repeatability: std over sub-images of the (axis-averaged) period
    rep = 0.0
    nsub = min((len(a.sub_periods_px) for a in axes), default=0)
    if nsub >= 2:
        per_sub = np.mean([a.sub_periods_px[:nsub] for a in axes], axis=0)
        rep = float(np.std(per_sub, ddof=1))
    a0 = axes[0]
    th = math.radians(a0.wave_angle_deg)
    h, w = gray.shape
    span = float(min(abs(w * math.cos(th)) + abs(h * math.sin(th)), math.hypot(w, h)))

    confidence = "high"
    worst = max(a.disagreement_pct for a in axes)
    if not np.isfinite(worst) or worst > LOW_CONFIDENCE_DISAGREEMENT_PCT:
        confidence = "low"
        warnings.append(
            "FFT and autocorrelation disagree by more than "
            f"{LOW_CONFIDENCE_DISAGREEMENT_PCT:g} % - use the manual measurement")
    if len(axes) == 2:
        d = 100.0 * abs(axes[0].period_px - axes[1].period_px) / period
        if d > LOW_CONFIDENCE_DISAGREEMENT_PCT:
            warnings.append(f"grid axes differ by {d:.2f} % (scan distortion?)")
    if detected == PATTERN_GRID and len(axes) < 2:
        confidence = "low"

    res = PitchResult(period_px=period, pattern=detected, axes=axes,
                      confidence=confidence, warnings=warnings,
                      repeatability_px=rep, span_px=span, method="fft")
    if px_per_um:
        res.px_per_um = float(px_per_um)
        res.measured_pitch_um = period / float(px_per_um)
        res.repeatability_um = rep / float(px_per_um)
    _p(100, "Done")
    return res


def manual_pitch_px(p0: Sequence[float], p1: Sequence[float], n_periods: int) -> float:
    """Manual fallback: the user drags across ``n_periods`` periods."""
    if n_periods <= 0:
        raise ValueError("n_periods must be >= 1")
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / float(n_periods)


def evaluate(measured_pitch_um: float, certified_pitch_um: float, *,
             tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
             cert_expanded_um: float = 0.0, repeatability_um: float = 0.0,
             span_px: float = 0.0, method: str = "fft",
             pitch: Optional[PitchResult] = None) -> VerificationResult:
    """Compare a measured pitch with the certificate (step 4 + 5)."""
    err = error_percent(measured_pitch_um, certified_pitch_um)
    return VerificationResult(
        measured_pitch_um=float(measured_pitch_um),
        certified_pitch_um=float(certified_pitch_um),
        error_pct=err, tolerance_pct=float(tolerance_pct),
        passed=passes_tolerance(err, tolerance_pct), method=method,
        uncertainty=uncertainty_budget(measured_pitch_um, cert_expanded_um=cert_expanded_um,
                                       repeatability_um=repeatability_um, span_px=span_px),
        pitch=pitch)


def verify_calibration(image, *, px_per_um: float, certified_pitch_um: float,
                       tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
                       cert_expanded_um: float = 0.0, pattern: str = PATTERN_AUTO,
                       valid_mask: Optional[np.ndarray] = None,
                       progress=None) -> VerificationResult:
    """One-call FFT verification: measure the pitch, then :func:`evaluate`."""
    if not px_per_um or px_per_um <= 0:
        raise ValueError("px_per_um must be > 0")
    p = measure_pitch(image, px_per_um=px_per_um, pattern=pattern,
                      valid_mask=valid_mask, progress=progress)
    return evaluate(p.measured_pitch_um, certified_pitch_um, tolerance_pct=tolerance_pct,
                    cert_expanded_um=cert_expanded_um,
                    repeatability_um=p.repeatability_um or 0.0,
                    span_px=p.span_px, method="fft", pitch=p)


def period_lines(axis: AxisMeasurement, shape: Tuple[int, int]
                 ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Crest lines of one detected line family, clipped to the image, as
    ``((x0, y0), (x1, y1))`` segments - for the dialog preview overlay."""
    h, w = shape[:2]
    kx, ky = axis.kx, axis.ky
    if kx == 0 and ky == 0:
        return []
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    vals = [kx * x + ky * y for x, y in corners]
    off = -axis.phase_rad / (2 * math.pi)
    m0, m1 = math.ceil(min(vals) - off), math.floor(max(vals) - off)
    out = []
    for m in range(int(m0), int(m1) + 1):
        c = m + off
        pts = []
        if ky != 0:
            for x in (0.0, w - 1.0):
                y = (c - kx * x) / ky
                if -1e-9 <= y <= h - 1 + 1e-9:
                    pts.append((x, y))
        if kx != 0:
            for y in (0.0, h - 1.0):
                x = (c - ky * y) / kx
                if -1e-9 <= x <= w - 1 + 1e-9:
                    pts.append((x, y))
        uniq = []
        for p in pts:
            if all(abs(p[0] - q[0]) + abs(p[1] - q[1]) > 1e-6 for q in uniq):
                uniq.append(p)
        if len(uniq) >= 2:
            out.append((uniq[0], uniq[1]))
    return out
