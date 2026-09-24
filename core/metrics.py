"""
Grain statistics — the single implementation of summary metrics.

Every place that needs summary statistics for an ``AnalysisResult`` (the
detector after segmentation, border-grain discard, manual grain deletion in
the UI) must call :func:`compute_statistics` so the numbers cannot drift.

Coverage definition
-------------------
Area fraction (ASTM E1245 / E562 "area fraction" A_A) is the grain area
divided by the area of the *test field*.  Pixels that carry no specimen
information — pure-black info bars, detector drop-outs, masked-off regions
(the detector's ``valid_mask``) — are not part of the test field, so:

    grain_coverage_pct      = sum(grain area) / valid area * 100
    total_analyzed_area_um2 = valid area in um^2

Lot statistics (INN-27): :func:`sample_statistics` -> :class:`SampleStatistics`
(ASTM E112 sec. 15 95 % CI / %RA over fields, see the section below).

No Qt imports here (core/ is UI-agnostic).
"""
from __future__ import annotations

import math as _math
from dataclasses import asdict as _asdict, dataclass as _dataclass, field as _field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import numpy as np


def _frame_px(result, image_shape: Optional[Tuple[int, ...]]) -> float:
    if image_shape is not None:
        return float(image_shape[0] * image_shape[1])
    if getattr(result, "label_image", None) is not None:
        h, w = result.label_image.shape[:2]
        return float(h * w)
    return 0.0


def valid_area_px(result, image_shape: Optional[Tuple[int, ...]] = None) -> float:
    """Area of the analysed test field in pixels.

    Priority: the scalar ``result.valid_area_px`` set by the detector, then
    ``result.valid_mask``, then the whole frame (legacy behaviour).
    """
    v = float(getattr(result, "valid_area_px", 0.0) or 0.0)
    if v > 0:
        return v
    vm = getattr(result, "valid_mask", None)
    if vm is not None:
        return float(np.count_nonzero(vm))
    return _frame_px(result, image_shape)


def compute_statistics(result, image_shape: Optional[Tuple[int, ...]] = None):
    """Recompute all summary fields of ``result`` in place and return it.

    ``image_shape`` is only used as a fallback test-field size when the
    result carries no valid-area information (results produced by older
    code paths).
    """
    frame = _frame_px(result, image_shape)
    vpx = valid_area_px(result, image_shape)
    result.valid_area_px = vpx
    ppu = float(result.px_per_um or 0.0)
    calibrated = bool(result.has_calibration) and ppu > 0
    result.valid_area_um2 = vpx / ppu ** 2 if calibrated else 0.0
    if frame > 0:
        result.invalid_area_pct = max(0.0, (frame - vpx) / frame * 100.0)

    grains = list(result.grains or [])
    result.grain_count = len(grains)

    # reset everything so stale values never survive a recompute
    for name in ("mean_area_um2", "std_area_um2", "median_area_um2",
                 "min_area_um2", "max_area_um2", "mean_diameter_um",
                 "std_diameter_um", "mean_circularity", "mean_aspect_ratio",
                 "grain_coverage_pct"):
        setattr(result, name, 0.0)
    result.total_analyzed_area_um2 = result.valid_area_um2

    if not grains:
        return result

    area_px = np.array([g.area_px for g in grains], dtype=np.float64)
    if vpx > 0:
        # dimensionless, identical whether computed in px or um^2
        result.grain_coverage_pct = float(area_px.sum() / vpx * 100.0)

    if calibrated:
        areas = np.array([g.area_um2 for g in grains], dtype=np.float64)
        diams = np.array([g.equivalent_diameter_um for g in grains],
                         dtype=np.float64)
        result.mean_area_um2 = float(np.mean(areas))
        result.std_area_um2 = float(np.std(areas))
        result.median_area_um2 = float(np.median(areas))
        result.min_area_um2 = float(np.min(areas))
        result.max_area_um2 = float(np.max(areas))
        result.mean_diameter_um = float(np.mean(diams))
        result.std_diameter_um = float(np.std(diams))

    result.mean_circularity = float(np.mean([g.circularity for g in grains]))
    result.mean_aspect_ratio = float(np.mean([g.aspect_ratio for g in grains]))
    return result


# ======================================================================
# INN-27: lot / sample statistics and E112 measurement uncertainty
# ======================================================================
#
# Standards
# ---------
# ASTM E112-13 sec. 15 ("Statistical Analysis") and ASTM E1382 field-to-
# field statistics: measure n >= 5 fields, then
#
#     x_bar = mean(x_i),  s = std(x_i, ddof=1)          (sample std-dev)
#     95 % CI = t * s / sqrt(n),  t = Student t(0.975, n - 1)
#     %RA     = 100 * CI / x_bar          (%RA <= 10 % generally acceptable)
#
# E112 applies this to the *measured* quantity -- the number of grains per
# unit area N_A (planimetric) or the mean lineal intercept l_bar
# (intercept) -- not to G, which is a logarithm.  So %RA is computed on
# N_A or l_bar (whichever the primary G method uses) and the G interval is
# obtained by pushing the N_A / l_bar CI bounds through the E112 G
# formula.  A direct G +/- t * s_G / sqrt(n) is also given for readability.
#
# The per-field N_A / l_bar is recovered by inverting the E112 relation
# from the field's primary G, which reproduces the stored N_A / l_bar
# exactly and also works for results saved without them:
#     planimetric: N_A   = 10 ** ((G + 2.954) / 3.321928)       (per mm^2)
#     intercept:   l_bar = 10 ** (-(G + 3.288) / 6.643856)      (mm)
#
# Fields needed (E112 sec. 15 rearranged): n_req = ceil((t*s / (RA*x_bar))^2)
# iterated because t depends on n; reported as max(required_fields, n_req).

def _t975(dof: int) -> float:
    """Two-sided 95 % Student-t quantile t(0.975, dof)."""
    from scipy import stats as _st
    return float(_st.t.ppf(0.975, dof))


def _na_from_G(G: float) -> float:
    return 10.0 ** ((G + 2.954) / 3.321928)


def _G_from_na(na: float) -> Optional[float]:
    return 3.321928 * _math.log10(na) - 2.954 if na > 0 else None


def _lbar_mm_from_G(G: float) -> float:
    return 10.0 ** (-(G + 3.288) / 6.643856)


def _G_from_lbar_mm(lbar: float) -> Optional[float]:
    return -6.643856 * _math.log10(lbar) - 3.288 if lbar > 0 else None


@_dataclass
class FieldResult:
    """One analysed field (latest analysis of one image) as fed to
    :func:`sample_statistics`.  Plain data, JSON-able via ``to_dict``."""
    field_id: str = ""
    G: Optional[float] = None               # primary ASTM E112 G (None = uncalibrated)
    method: str = ""                        # "planimetric" | "intercept" | ""
    ecd_mean_um: Optional[float] = None     # mean equivalent circle diameter
    grain_count: int = 0
    valid_area_pct: float = 100.0
    included: bool = True
    exclusion_reason: Optional[str] = None
    session_id: str = ""
    session_path: str = ""
    image_name: str = ""
    thumb_path: str = ""
    ecds_um: List[float] = _field(default_factory=list)  # pooled distribution (INN-37)

    def to_dict(self) -> dict:
        return _asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "FieldResult":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in valid})

    @classmethod
    def from_analysis(cls, result, field_id: str = "", **kw) -> "FieldResult":
        """Build from an ``AnalysisResult`` (uses the stored primary G)."""
        from core.astm import astm_g_from_result
        try:
            g = astm_g_from_result(result)
        except Exception:
            g = None
        astm = getattr(result, "astm", None) or {}
        calibrated = bool(getattr(result, "has_calibration", False)) and \
            float(getattr(result, "px_per_um", 0.0) or 0.0) > 0
        grains = list(getattr(result, "grains", None) or [])
        ecds = [float(x.equivalent_diameter_um) for x in grains] if calibrated else []
        return cls(
            field_id=field_id, G=None if g is None else float(g),
            method=str(astm.get("primary_method", "") or "") if isinstance(astm, dict) else "",
            ecd_mean_um=float(result.mean_diameter_um) if calibrated and grains else None,
            grain_count=int(getattr(result, "grain_count", 0) or 0),
            valid_area_pct=100.0 - float(getattr(result, "invalid_area_pct", 0.0) or 0.0),
            ecds_um=ecds, **kw)


@_dataclass
class StatsConfig:
    required_fields: int = 5
    target_RA_pct: float = 10.0
    scope: str = "lot"                      # "lot" | "session"
    session_id: Optional[str] = None        # used when scope == "session"


@_dataclass
class SampleStatistics:
    scope: str = "lot"
    n_fields: int = 0
    required_fields: int = 5
    G_mean: Optional[float] = None
    G_ci95: Optional[float] = None          # direct t * s_G / sqrt(n)
    G_ci_low: Optional[float] = None        # from N_A / l_bar CI bounds
    G_ci_high: Optional[float] = None
    RA_pct: Optional[float] = None          # on N_A or l_bar (E112 practice)
    ecd_mean_um: Optional[float] = None
    ecd_ci95_um: Optional[float] = None
    n_needed: int = 5                       # max(required_fields, n_req)
    adequate: bool = False
    outlier_field_ids: List[str] = _field(default_factory=list)
    method: str = ""                        # primary G method of the fields
    # extras
    basis: str = ""                         # "N_A" | "l_bar" -- quantity %RA is computed on
    G_std: Optional[float] = None
    t_value: Optional[float] = None
    target_RA_pct: float = 10.0
    status: str = ""                        # "adequate" | "need ≥ k more fields" | "uncalibrated"
    status_level: str = "amber"             # "green" | "amber" | "grey"
    n_excluded: int = 0
    n_uncalibrated: int = 0
    field_ids: List[str] = _field(default_factory=list)
    label: str = ""                         # display label (e.g. lot number)

    def to_dict(self) -> dict:
        return _asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SampleStatistics":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in valid})

    def summary_text(self) -> str:
        """e.g. 'G 7.40 ± 0.39 (95 % CI), %RA 3.1 %, 5 of 5 required fields'."""
        if self.G_mean is None:
            return f"G n/a ({self.status})"
        ci = f" ± {self.G_ci95:.2f} (95 % CI)" if self.G_ci95 is not None else " (CI n/a)"
        ra = f", %RA {self.RA_pct:.1f} %" if self.RA_pct is not None else ""
        return (f"G {self.G_mean:.2f}{ci}{ra}, {self.n_fields} of "
                f"{self.n_needed} required fields")


def _cfg_get(cfg, name, default):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def fields_needed(cv: float, target_RA_pct: float, n_max: int = 10000) -> int:
    """Smallest n >= 2 with n >= (t(0.975, n-1) * cv / RA)^2 (E112 sec. 15).

    ``cv`` = s / x_bar of the measured quantity.  Fixed-point on n because
    the Student t quantile depends on n.
    """
    ra = float(target_RA_pct) / 100.0
    if not cv > 0:
        return 2
    if not ra > 0:
        return n_max
    for n in range(2, n_max + 1):
        if n >= _math.ceil((_t975(n - 1) * cv / ra) ** 2 - 1e-9):
            return n
    return n_max


def pooled_ecds_um(field_results: Iterable) -> List[float]:
    """All grain ECDs (um) of the included fields -- pooled distribution
    for D10/D50/D90 (INN-37)."""
    out: List[float] = []
    for f in field_results:
        f = f if isinstance(f, FieldResult) else FieldResult.from_dict(f)
        if f.included:
            out.extend(float(x) for x in f.ecds_um)
    return out


def sample_statistics(field_results: Sequence, cfg: Any = None) -> SampleStatistics:
    """Lot (or single-session) statistics over the *included* fields.

    ``field_results``: ``FieldResult`` objects or dicts.  ``cfg``:
    ``StatsConfig``, ``AppSettings`` or a dict with ``required_fields``,
    ``target_RA_pct``, ``scope`` and (for scope "session") ``session_id``.
    Uncalibrated fields (G None) and excluded fields do not count toward n.
    See the section header above for the E112 / E1382 formulas.
    """
    required = int(_cfg_get(cfg, "required_fields", 5) or 5)
    target = float(_cfg_get(cfg, "target_RA_pct", 10.0) or 10.0)
    scope = str(_cfg_get(cfg, "scope", "lot") or "lot")
    session_id = _cfg_get(cfg, "session_id", None)

    fields = [f if isinstance(f, FieldResult) else FieldResult.from_dict(f)
              for f in (field_results or [])]
    if scope == "session" and session_id is not None:
        fields = [f for f in fields if f.session_id == session_id]
    included = [f for f in fields if f.included]
    measured = [f for f in included if f.G is not None]

    st = SampleStatistics(scope=scope if scope != "session" else f"session:{session_id}",
                          required_fields=required, target_RA_pct=target,
                          n_excluded=len(fields) - len(included),
                          n_uncalibrated=len(included) - len(measured),
                          n_needed=required)
    n = len(measured)
    st.n_fields = n
    st.field_ids = [f.field_id for f in measured]

    methods = [f.method for f in measured if f.method]
    st.method = max(sorted(set(methods)), key=methods.count) if methods else \
        ("planimetric" if n else "")
    st.basis = "l_bar" if st.method == "intercept" else "N_A"

    if n == 0:
        if included:
            st.status, st.status_level = "uncalibrated", "grey"
        else:
            st.status, st.status_level = f"need ≥ {required} more fields", "amber"
        return st

    G = np.array([f.G for f in measured], dtype=np.float64)
    st.G_mean = float(G.mean())
    ecd = np.array([f.ecd_mean_um for f in measured
                    if f.ecd_mean_um is not None and f.ecd_mean_um > 0], dtype=np.float64)
    if ecd.size:
        st.ecd_mean_um = float(ecd.mean())
    if ecd.size >= 2:
        st.ecd_ci95_um = float(_t975(ecd.size - 1) * ecd.std(ddof=1) / _math.sqrt(ecd.size))

    if n >= 2:
        t = _t975(n - 1)
        st.t_value = t
        s_G = float(G.std(ddof=1))
        st.G_std = s_G
        st.G_ci95 = t * s_G / _math.sqrt(n)

        if st.basis == "l_bar":
            x = np.array([_lbar_mm_from_G(g) for g in G])
        else:
            x = np.array([_na_from_G(g) for g in G])
        xbar = float(x.mean())
        sx = float(x.std(ddof=1))
        ci_x = t * sx / _math.sqrt(n)
        st.RA_pct = 100.0 * ci_x / xbar
        if st.basis == "l_bar":
            # larger mean intercept -> coarser grain -> lower G
            st.G_ci_low = _G_from_lbar_mm(xbar + ci_x)
            st.G_ci_high = _G_from_lbar_mm(xbar - ci_x)
        else:
            st.G_ci_low = _G_from_na(xbar - ci_x)
            st.G_ci_high = _G_from_na(xbar + ci_x)

        st.n_needed = max(required, fields_needed(sx / xbar, target))
        if s_G > 0:
            st.outlier_field_ids = [f.field_id for f, g in zip(measured, G)
                                    if abs(g - st.G_mean) > 2.5 * s_G]

    st.adequate = bool(n >= 2 and n >= st.n_needed and st.RA_pct is not None
                       and st.RA_pct <= target)
    if st.adequate:
        st.status, st.status_level = "adequate", "green"
    else:
        k = max(st.n_needed - n, 1)
        st.status, st.status_level = f"need ≥ {k} more fields", "amber"
    return st
