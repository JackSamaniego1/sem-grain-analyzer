"""
INN-43: lot comparison matrix, Welch ANOVA and TOST equivalence vs a
baseline lot.  Pure math (numpy / scipy.stats), no Qt.

Statistical model
-----------------
The unit of replication is the *field* (one analysed SEM image), exactly as
in ASTM E112-13 sec. 15 / ASTM E1382: each field yields one ASTM grain size
number G, and a lot is summarised by

    G_bar = mean(G_i),   s = std(G_i, ddof=1),   SE = s / sqrt(n)

(the same per-field G values that :func:`core.metrics.sample_statistics`
uses; included, calibrated, finite fields only).  Grains inside one field
are *not* independent replicates, so pooling grain counts across fields
would understate the uncertainty.

Two lots are compared with the Welch (unequal-variance) t framework:

    dG = G_bar_j - G_bar_i,   SE_d = sqrt(SE_i^2 + SE_j^2)
    df = SE_d^4 / (SE_i^4/(n_i-1) + SE_j^4/(n_j-1))      (Welch-Satterthwaite)

Several lots are tested together with Welch's (1951) heteroscedastic
one-way ANOVA.  Equivalence to a baseline uses Schuirmann's two one-sided
tests (TOST) with margin +/- delta (default 0.5 G) at level alpha (default
0.05), i.e. equivalence <=> the (1 - 2 alpha) = 90 % CI of dG lies inside
(-delta, +delta).

Sign convention: higher G = finer grains (E112), so dG > 0 means lot j is
finer than lot i.

Baseline storage: lot metadata lives in ``data/models.py`` (``LotMeta``),
which is outside ``core/``.  This module therefore only offers the pure
helper :func:`find_baseline_lot` that reads an ``is_baseline`` flag from
whatever lot records it is given; persisting the flag is left to the data
layer (see note in the INN-43 report).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy import stats

__all__ = [
    "Band", "Verdict", "LotSummary", "PairDiff", "WelchAnova", "Equivalence",
    "LotComparison", "lot_summary", "band_for", "pair_diff", "delta_matrix",
    "welch_anova", "tost_equivalence", "fields_needed_tost", "compare_lots",
    "find_baseline_lot", "DEFAULT_MARGIN_G", "DEFAULT_ALPHA",
]

DEFAULT_MARGIN_G = 0.5
DEFAULT_ALPHA = 0.05
GREEN_MAX = 0.25
AMBER_MAX = 0.5


class Band(str, Enum):
    """|dG| band for the matrix cell (UI maps it to a colour)."""
    GREEN = "green"      # |dG| <= 0.25
    AMBER = "amber"      # 0.25 < |dG| <= 0.5
    RED = "red"          # |dG| > 0.5
    NONE = "none"        # dG undefined (a lot has no G)


class Verdict(str, Enum):
    EQUIVALENT = "Equivalent"
    NOT_EQUIVALENT = "Not equivalent"
    INCONCLUSIVE = "Inconclusive"


# ---------------------------------------------------------------- summaries

@dataclass
class LotSummary:
    label: str = ""
    n: int = 0
    mean: Optional[float] = None
    sd: Optional[float] = None          # sample std-dev (ddof=1); None if n < 2
    se: Optional[float] = None          # sd / sqrt(n)
    ci_low: Optional[float] = None      # two-sided t CI at ``conf``
    ci_high: Optional[float] = None
    conf: float = 0.95
    values: List[float] = field(default_factory=list)
    n_dropped: int = 0                  # excluded / uncalibrated / NaN fields

    @property
    def df(self) -> int:
        return max(self.n - 1, 0)

    def to_dict(self) -> dict:
        return asdict(self)


def _field_G(f: Any) -> Tuple[Optional[float], bool]:
    """(G, included) from a FieldResult, dict or plain number."""
    if isinstance(f, (int, float, np.floating, np.integer)) or f is None:
        return (None if f is None else float(f)), True
    if isinstance(f, Mapping):
        return f.get("G"), bool(f.get("included", True))
    return getattr(f, "G", None), bool(getattr(f, "included", True))


def lot_summary(fields: Iterable[Any], label: str = "", conf: float = 0.95) -> LotSummary:
    """Per-lot mean G, field-level SE and t CI.

    ``fields``: ``core.metrics.FieldResult`` objects, their dicts, or plain
    G numbers.  Excluded fields, uncalibrated fields (G None) and non-finite
    G are dropped (counted in ``n_dropped``).  n = 1 gives a mean but no
    SE / CI; n = 0 gives mean None.  Zero variance gives SE 0 and a
    zero-width CI (legitimate for identical fields, e.g. synthetic data).
    """
    vals: List[float] = []
    dropped = 0
    for f in fields or []:
        g, inc = _field_G(f)
        try:
            g = None if g is None else float(g)
        except (TypeError, ValueError):
            g = None
        if inc and g is not None and math.isfinite(g):
            vals.append(g)
        else:
            dropped += 1
    s = LotSummary(label=label, n=len(vals), conf=conf, values=vals, n_dropped=dropped)
    if not vals:
        return s
    x = np.asarray(vals, dtype=np.float64)
    s.mean = float(x.mean())
    if s.n >= 2:
        s.sd = float(x.std(ddof=1))
        if s.sd <= 1e-12 * max(1.0, abs(s.mean)):   # float round-off of identical fields
            s.sd = 0.0
        s.se = s.sd / math.sqrt(s.n)
        h = float(stats.t.ppf(0.5 + conf / 2.0, s.n - 1)) * s.se
        s.ci_low, s.ci_high = s.mean - h, s.mean + h
    return s


# ---------------------------------------------------------------- pairwise

def band_for(abs_dG: Optional[float], green_max: float = GREEN_MAX,
             amber_max: float = AMBER_MAX) -> Band:
    if abs_dG is None or not math.isfinite(abs_dG):
        return Band.NONE
    a = abs(abs_dG)
    if a <= green_max + 1e-12:
        return Band.GREEN
    if a <= amber_max + 1e-12:
        return Band.AMBER
    return Band.RED


def _welch_se_df(a: LotSummary, b: LotSummary) -> Tuple[Optional[float], Optional[float]]:
    """SE of the difference of means and Welch-Satterthwaite df.
    (None, None) if either lot has n < 2; df = inf when both SE are 0."""
    if a.se is None or b.se is None:
        return None, None
    va, vb = a.se ** 2, b.se ** 2
    se = math.sqrt(va + vb)
    den = (va ** 2 / (a.n - 1)) + (vb ** 2 / (b.n - 1))
    df = (va + vb) ** 2 / den if den > 0 else math.inf
    return se, df


@dataclass
class PairDiff:
    """dG = G_j - G_i with its Welch t CI."""
    label_i: str = ""
    label_j: str = ""
    dG: Optional[float] = None
    se: Optional[float] = None
    df: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    conf: float = 0.95
    band: Band = Band.NONE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["band"] = self.band.value
        return d


def pair_diff(i: LotSummary, j: LotSummary, conf: float = 0.95) -> PairDiff:
    p = PairDiff(label_i=i.label, label_j=j.label, conf=conf)
    if i.mean is None or j.mean is None:
        return p
    p.dG = j.mean - i.mean
    p.band = band_for(p.dG)
    p.se, p.df = _welch_se_df(i, j)
    if p.se is not None:
        h = 0.0 if p.se == 0 else float(stats.t.ppf(0.5 + conf / 2.0, p.df)) * p.se
        p.ci_low, p.ci_high = p.dG - h, p.dG + h
    return p


def delta_matrix(summaries: Sequence[LotSummary], conf: float = 0.95) -> List[List[PairDiff]]:
    """Square matrix M[i][j] = pair_diff(lot i, lot j), i.e. G_j - G_i
    (diagonal is dG 0 / green)."""
    return [[pair_diff(a, b, conf) for b in summaries] for a in summaries]


# ---------------------------------------------------------------- Welch ANOVA

@dataclass
class WelchAnova:
    k: int = 0
    F: Optional[float] = None
    df1: Optional[float] = None
    df2: Optional[float] = None
    p: Optional[float] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def welch_anova(summaries: Sequence[LotSummary]) -> WelchAnova:
    """Welch (1951) one-way ANOVA for unequal variances on per-field G.

        w_i = n_i / s_i^2,  W = sum w_i,  m = sum w_i x_i / W
        A   = sum w_i (x_i - m)^2 / (k - 1)
        L   = sum (1 - w_i/W)^2 / (n_i - 1)
        F   = A / (1 + 2 (k - 2) L / (k^2 - 1))
        df1 = k - 1,  df2 = (k^2 - 1) / (3 L)

    For k = 2 this reduces exactly to the Welch t-test (F = t^2).  Needs
    k >= 2 lots with n >= 2 and s > 0 each; otherwise F/p are None and
    ``note`` says why (lots with fewer than 2 fields are skipped).
    """
    used = [s for s in summaries if s.n >= 2 and s.sd is not None]
    res = WelchAnova(k=len(used))
    skipped = len(summaries) - len(used)
    if len(used) < 2:
        res.note = "need at least 2 lots with 2 or more measured fields each"
        return res
    if any(s.sd == 0 for s in used):
        res.note = "a lot has zero field-to-field scatter; Welch ANOVA undefined"
        return res
    k = len(used)
    n = np.array([s.n for s in used], dtype=np.float64)
    x = np.array([s.mean for s in used], dtype=np.float64)
    v = np.array([s.sd ** 2 for s in used], dtype=np.float64)
    w = n / v
    W = w.sum()
    m = float((w * x).sum() / W)
    A = float((w * (x - m) ** 2).sum() / (k - 1))
    L = float((((1.0 - w / W) ** 2) / (n - 1)).sum())
    res.F = A / (1.0 + 2.0 * (k - 2) * L / (k * k - 1))
    res.df1 = float(k - 1)
    res.df2 = (k * k - 1) / (3.0 * L)
    res.p = float(stats.f.sf(res.F, res.df1, res.df2))
    if skipped:
        res.note = f"{skipped} lot(s) with fewer than 2 fields not included"
    return res


# ---------------------------------------------------------------- TOST

@dataclass
class Equivalence:
    test_label: str = ""
    baseline_label: str = ""
    verdict: Verdict = Verdict.INCONCLUSIVE
    margin: float = DEFAULT_MARGIN_G
    alpha: float = DEFAULT_ALPHA
    dG: Optional[float] = None               # G_test - G_baseline
    se: Optional[float] = None
    df: Optional[float] = None
    ci_low: Optional[float] = None           # (1 - 2 alpha) CI, 90 % by default
    ci_high: Optional[float] = None
    p_lower: Optional[float] = None          # H0: dG <= -margin
    p_upper: Optional[float] = None          # H0: dG >= +margin
    p_tost: Optional[float] = None           # max(p_lower, p_upper)
    n_fields_needed: Optional[int] = None    # per lot, when Inconclusive
    more_fields_test: Optional[int] = None
    more_fields_baseline: Optional[int] = None
    sentence: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


def fields_needed_tost(sd: float, dG: float, margin: float = DEFAULT_MARGIN_G,
                       alpha: float = DEFAULT_ALPHA, power: float = 0.80,
                       n_max: int = 1000) -> Optional[int]:
    """Fields per lot (equal n) for a TOST to show equivalence with the
    given power, assuming the true difference is ``dG`` and both lots have
    field-to-field std-dev ``sd`` (Chow, Shao & Wang, *Sample Size
    Calculations in Clinical Research*, sec. 3.2.3):

        n = 2 sd^2 (t(1-alpha, 2n-2) + t(1-beta', 2n-2))^2 / (margin - |dG|)^2
        beta' = beta / 2 if dG == 0 else beta

    solved by fixed-point on n (t depends on n).  Returns None when
    |dG| >= margin (equivalence can't be expected however many fields are
    measured) or sd is unusable; 2 when sd == 0.
    """
    if sd is None or not math.isfinite(sd) or sd < 0:
        return None
    gap = margin - abs(dG)
    if gap <= 0:
        return None
    if sd == 0:
        return 2
    beta = 1.0 - power
    b = beta / 2.0 if abs(dG) < 1e-12 else beta
    for n in range(2, n_max + 1):
        df = 2 * n - 2
        q = stats.t.ppf(1 - alpha, df) + stats.t.ppf(1 - b, df)
        if n >= 2.0 * sd * sd * q * q / (gap * gap):
            return n
    return n_max


def _fmt_g(x: float) -> str:
    return f"{x:+.2f}".replace("-", "−")


def tost_equivalence(test: LotSummary, baseline: LotSummary,
                     margin: float = DEFAULT_MARGIN_G, alpha: float = DEFAULT_ALPHA,
                     power: float = 0.80) -> Equivalence:
    """Schuirmann TOST of ``test`` vs ``baseline`` on per-field G (Welch).

    With dG = G_test - G_base, SE and df from :func:`_welch_se_df`:

        p_lower = P(T_df >= (dG + margin) / SE)     H0: dG <= -margin
        p_upper = P(T_df <= (dG - margin) / SE)     H0: dG >= +margin
        p_tost  = max(p_lower, p_upper)
        CI      = dG +/- t(1 - alpha, df) SE        ((1 - 2 alpha), 90 % default)

    Verdict (precise definitions):

    * **Equivalent** -- p_tost < alpha, i.e. the 90 % CI lies strictly
      inside (-margin, +margin).
    * **Not equivalent** -- either (a) the 90 % CI lies entirely outside
      the margin (ci_low > +margin or ci_high < -margin: the difference is
      shown to exceed the tolerance at level alpha), or (b) the best
      estimate is outside the tolerance (|dG| > margin) *and* the 90 % CI
      excludes 0 (the lots demonstrably differ, and most likely by more
      than the tolerance).
    * **Inconclusive** -- everything else, including a lot with fewer than
      2 measured fields (SE undefined) or no finite G.  An estimate of the
      fields needed per lot (:func:`fields_needed_tost`, 80 % power, using
      the observed dG and the pooled field std-dev) is attached when
      |dG| < margin.

    Zero field-to-field scatter in both lots (SE = 0) is handled as a
    degenerate CI [dG, dG]; p-values are then 0 or 1.
    """
    e = Equivalence(test_label=test.label, baseline_label=baseline.label,
                    margin=float(margin), alpha=float(alpha))
    tol = f"±{margin:g} G"
    conf_pct = round(100 * (1 - 2 * alpha))
    if test.mean is None or baseline.mean is None:
        e.sentence = ("Can't compare yet — "
                      + ("this lot" if test.mean is None else "the baseline lot")
                      + " has no calibrated G values.")
        return e
    e.dG = test.mean - baseline.mean
    e.se, e.df = _welch_se_df(baseline, test)

    if e.se is None:
        e.verdict = Verdict.INCONCLUSIVE
        sd = test.sd if test.sd is not None else baseline.sd
        _attach_n(e, test, baseline, sd, power)
        e.sentence = ("Can't tell yet — each lot needs at least 2 measured fields "
                      "(ASTM E112 recommends 5 or more).")
        if e.more_fields_test or e.more_fields_baseline:
            e.sentence += " " + _more_sentence(e, tol)
        return e

    if e.se == 0:
        e.ci_low = e.ci_high = e.dG
        e.p_lower = 0.0 if e.dG + margin > 0 else 1.0
        e.p_upper = 0.0 if e.dG - margin < 0 else 1.0
    else:
        e.p_lower = float(stats.t.sf((e.dG + margin) / e.se, e.df))
        e.p_upper = float(stats.t.cdf((e.dG - margin) / e.se, e.df))
        h = float(stats.t.ppf(1 - alpha, e.df)) * e.se
        e.ci_low, e.ci_high = e.dG - h, e.dG + h
    e.p_tost = max(e.p_lower, e.p_upper)

    ci_txt = (f"ΔG {_fmt_g(e.dG)} ({conf_pct} % CI {_fmt_g(e.ci_low)} to "
              f"{_fmt_g(e.ci_high)})")
    outside = e.ci_low > margin or e.ci_high < -margin
    differ = abs(e.dG) > margin and (e.ci_low > 0 or e.ci_high < 0)
    if e.p_tost < alpha:
        e.verdict = Verdict.EQUIVALENT
        e.sentence = (f"Equivalent to baseline within {tol} ({conf_pct} % confidence). "
                      f"{ci_txt}.")
    elif outside or differ:
        e.verdict = Verdict.NOT_EQUIVALENT
        word = "finer" if e.dG > 0 else "coarser"
        e.sentence = (f"Not equivalent — grains are {word} than the baseline by about "
                      f"{abs(e.dG):.2f} G, outside the {tol} tolerance. {ci_txt}.")
    else:
        e.verdict = Verdict.INCONCLUSIVE
        pooled = math.sqrt((test.sd ** 2 + baseline.sd ** 2) / 2.0)
        _attach_n(e, test, baseline, pooled, power)
        if e.n_fields_needed is None:
            e.sentence = (f"Can't tell yet — the difference is close to the {tol} "
                          f"tolerance; more fields are unlikely to settle it. {ci_txt}.")
        else:
            e.sentence = _more_sentence(e, tol) + f" {ci_txt}."
    return e


def _attach_n(e: Equivalence, test: LotSummary, base: LotSummary,
              sd: Optional[float], power: float) -> None:
    n_req = fields_needed_tost(sd, e.dG, e.margin, e.alpha, power) if sd is not None \
        else None
    if n_req is None:
        if sd is None:          # no scatter estimate at all: ask for the E112 minimum
            n_req = 5
        else:
            return
    n_req = max(n_req, 2)
    e.n_fields_needed = n_req
    e.more_fields_test = max(n_req - test.n, 0)
    e.more_fields_baseline = max(n_req - base.n, 0)
    if e.more_fields_test == 0 and e.more_fields_baseline == 0:
        # already at the estimate yet still undecided (unlucky scatter): ask for more
        e.more_fields_test = 1
        e.n_fields_needed = max(n_req, test.n + 1)


def _more_sentence(e: Equivalence, tol: str) -> str:
    t, b = e.more_fields_test or 0, e.more_fields_baseline or 0
    fld = lambda k: f"{k} more field{'s' if k != 1 else ''}"
    if t and b:
        where = f"about {fld(t)} on this lot and {fld(b)} on the baseline"
    elif b:
        where = f"about {fld(b)} on the baseline lot"
    else:
        where = f"about {fld(t)} on this lot"
    return f"Can't tell yet — measure {where} to decide at {tol}."


# ---------------------------------------------------------------- one-shot

@dataclass
class LotComparison:
    summaries: List[LotSummary] = field(default_factory=list)
    matrix: List[List[PairDiff]] = field(default_factory=list)
    anova: WelchAnova = field(default_factory=WelchAnova)
    baseline: Optional[str] = None
    equivalence: Dict[str, Equivalence] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "summaries": [s.to_dict() for s in self.summaries],
            "matrix": [[c.to_dict() for c in row] for row in self.matrix],
            "anova": self.anova.to_dict(),
            "baseline": self.baseline,
            "equivalence": {k: v.to_dict() for k, v in self.equivalence.items()},
        }


def compare_lots(lots: Mapping[str, Iterable[Any]], baseline: Optional[str] = None,
                 margin: float = DEFAULT_MARGIN_G, alpha: float = DEFAULT_ALPHA,
                 conf: float = 0.95) -> LotComparison:
    """Everything the comparison page needs.  ``lots``: {label: fields}
    (insertion order = matrix order).  ``baseline``: a key of ``lots`` or
    None (then no equivalence verdicts)."""
    summaries = [lot_summary(v, label=k, conf=conf) for k, v in lots.items()]
    out = LotComparison(summaries=summaries, matrix=delta_matrix(summaries, conf),
                        anova=welch_anova(summaries))
    if baseline is not None:
        if baseline not in lots:
            raise KeyError(f"baseline lot {baseline!r} not among the compared lots")
        out.baseline = baseline
        base = next(s for s in summaries if s.label == baseline)
        for s in summaries:
            if s.label != baseline:
                out.equivalence[s.label] = tost_equivalence(s, base, margin, alpha)
    return out


def find_baseline_lot(lots: Iterable[Any], material: Optional[str] = None) -> Optional[Any]:
    """Pure helper: the lot record flagged as baseline (``is_baseline``
    truthy, attribute or dict key), optionally restricted to records whose
    ``material`` matches.  The most recent flag wins if several are set
    (last in iteration order).  Persisting the flag belongs to the data
    layer (``data/models.py`` ``LotMeta``); records without the key are
    simply not baselines, so old manifests stay valid."""
    def get(r, k, d=None):
        return r.get(k, d) if isinstance(r, Mapping) else getattr(r, k, d)
    found = None
    for r in lots or []:
        if not get(r, "is_baseline", False):
            continue
        if material is not None and (get(r, "material", "") or "") != material:
            continue
        found = r
    return found
