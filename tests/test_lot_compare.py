"""INN-43: lot comparison matrix, Welch ANOVA, TOST equivalence (core/lot_compare.py)."""
import math

import numpy as np
import pytest
from scipy import stats

from core.lot_compare import (
    Band, Verdict, band_for, compare_lots, delta_matrix, fields_needed_tost,
    find_baseline_lot, lot_summary, pair_diff, tost_equivalence, welch_anova)
from core.metrics import FieldResult

LOT_A = [6.9, 7.0, 7.1, 6.95, 7.05, 7.0]          # 7.0 +/- 0.1, 6 fields


def _shift(vals, d):
    return [v + d for v in vals]


# ---------------------------------------------------------------- acceptance

def test_equivalent_same_lot_level():
    b = [7.02, 6.93, 7.08, 6.97, 7.06, 6.99]
    e = tost_equivalence(lot_summary(b, "new"), lot_summary(LOT_A, "base"))
    assert e.verdict is Verdict.EQUIVALENT
    assert e.p_tost < 0.05
    assert -0.5 < e.ci_low < e.ci_high < 0.5
    assert e.sentence.startswith("Equivalent to baseline within ±0.5 G (90 % confidence)")


def test_not_equivalent_7_vs_7_8():
    e = tost_equivalence(lot_summary(_shift(LOT_A, 0.8), "new"), lot_summary(LOT_A, "base"))
    assert e.verdict is Verdict.NOT_EQUIVALENT
    assert e.ci_low > 0.5
    assert "finer" in e.sentence and "0.80 G" in e.sentence
    assert e.n_fields_needed is None


def test_inconclusive_7_vs_7_3_two_fields_with_n_estimate():
    e = tost_equivalence(lot_summary([7.2, 7.4], "new"), lot_summary([6.9, 7.1], "base"))
    assert e.verdict is Verdict.INCONCLUSIVE
    assert e.n_fields_needed is not None and e.n_fields_needed > 2
    assert e.more_fields_test == e.n_fields_needed - 2
    assert f"about {e.more_fields_test} more fields" in e.sentence
    assert e.sentence.startswith("Can't tell yet")


# ---------------------------------------------------------------- scipy reference

@pytest.mark.parametrize("x,y,margin", [
    ([7.02, 6.93, 7.08, 6.97, 7.06, 6.99], LOT_A, 0.5),
    ([7.2, 7.4], [6.9, 7.1], 0.5),
    ([7.8, 7.7, 7.95, 7.85], LOT_A, 0.5),
    ([6.5, 7.3, 6.9, 7.6, 7.1], [7.0, 7.2, 6.8], 0.3),
])
def test_tost_p_values_match_scipy_ttest_ind(x, y, margin):
    e = tost_equivalence(lot_summary(x), lot_summary(y), margin=margin)
    x, y = np.asarray(x), np.asarray(y)
    p_lo = stats.ttest_ind(x + margin, y, equal_var=False, alternative="greater").pvalue
    p_hi = stats.ttest_ind(x - margin, y, equal_var=False, alternative="less").pvalue
    assert e.p_lower == pytest.approx(p_lo, abs=1e-6)
    assert e.p_upper == pytest.approx(p_hi, abs=1e-6)
    assert e.p_tost == pytest.approx(max(p_lo, p_hi), abs=1e-6)
    # 90 % CI equivalence <=> p_tost < 0.05
    inside = -margin < e.ci_low and e.ci_high < margin
    assert inside == (e.p_tost < 0.05)


# ---------------------------------------------------------------- Welch ANOVA

def _welch_anova_reference(groups):
    """Independent loop implementation from Welch (1951) / textbook form."""
    k = len(groups)
    ns = [len(g) for g in groups]
    ms = [sum(g) / len(g) for g in groups]
    vs = [sum((v - m) ** 2 for v in g) / (len(g) - 1) for g, m in zip(groups, ms)]
    ws = [n / v for n, v in zip(ns, vs)]
    W = sum(ws)
    xw = sum(w * m for w, m in zip(ws, ms)) / W
    num = sum(w * (m - xw) ** 2 for w, m in zip(ws, ms)) / (k - 1)
    lam = sum((1 - w / W) ** 2 / (n - 1) for w, n in zip(ws, ns))
    F = num / (1 + 2 * (k - 2) / (k ** 2 - 1) * lam)
    df2 = (k ** 2 - 1) / (3 * lam)
    return F, k - 1, df2, stats.f.sf(F, k - 1, df2)


def test_welch_anova_two_groups_equals_welch_t():
    a, b = [6.9, 7.1, 7.0, 7.3], [7.4, 7.2, 7.9, 7.6, 7.5]
    res = welch_anova([lot_summary(a), lot_summary(b)])
    t = stats.ttest_ind(a, b, equal_var=False)
    assert res.F == pytest.approx(t.statistic ** 2, rel=1e-10)
    assert res.p == pytest.approx(t.pvalue, abs=1e-10)
    assert res.df2 == pytest.approx(t.df, rel=1e-10)


def test_welch_anova_three_groups_matches_reference():
    groups = [[6.9, 7.1, 7.0, 7.3, 6.8], [7.4, 7.2, 7.9, 7.6], [7.0, 7.5, 6.6, 7.2, 7.1, 6.9]]
    res = welch_anova([lot_summary(g) for g in groups])
    F, df1, df2, p = _welch_anova_reference(groups)
    assert res.k == 3 and res.df1 == df1
    assert res.F == pytest.approx(F, rel=1e-10)
    assert res.df2 == pytest.approx(df2, rel=1e-10)
    assert res.p == pytest.approx(p, abs=1e-12)
    # equal-variance sanity: close to classic one-way ANOVA
    assert 0 < res.p < 1


def test_welch_anova_edge_cases():
    assert welch_anova([lot_summary(LOT_A)]).F is None
    r = welch_anova([lot_summary(LOT_A), lot_summary([7.0])])
    assert r.F is None and "at least 2 lots" in r.note
    r = welch_anova([lot_summary(LOT_A), lot_summary([7.0, 7.0]), lot_summary(LOT_A)])
    assert r.F is None and "zero" in r.note
    r = welch_anova([lot_summary(LOT_A), lot_summary(_shift(LOT_A, 1)), lot_summary([7.0])])
    assert r.k == 2 and r.p < 0.001 and "not included" in r.note


# ---------------------------------------------------------------- matrix / bands

def test_band_edges():
    assert band_for(0.0) is Band.GREEN and band_for(0.25) is Band.GREEN
    assert band_for(-0.3) is Band.AMBER and band_for(0.5) is Band.AMBER
    assert band_for(0.51) is Band.RED and band_for(None) is Band.NONE
    assert band_for(float("nan")) is Band.NONE


def test_delta_matrix_sign_and_ci():
    s = [lot_summary(LOT_A, "A"), lot_summary(_shift(LOT_A, 0.3), "B"),
         lot_summary(_shift(LOT_A, 0.8), "C")]
    m = delta_matrix(s)
    assert m[0][1].dG == pytest.approx(0.3) and m[1][0].dG == pytest.approx(-0.3)
    assert m[0][0].dG == 0 and m[0][0].band is Band.GREEN
    assert m[0][1].band is Band.AMBER and m[0][2].band is Band.RED
    p = m[0][1]
    t = stats.ttest_ind(_shift(LOT_A, 0.3), LOT_A, equal_var=False)
    ci = t.confidence_interval(0.95)
    assert p.ci_low == pytest.approx(ci.low, abs=1e-9)
    assert p.ci_high == pytest.approx(ci.high, abs=1e-9)
    assert m[0][2].to_dict()["band"] == "red"


# ---------------------------------------------------------------- edge cases

def test_single_field_lot_is_inconclusive():
    e = tost_equivalence(lot_summary([7.0], "new"), lot_summary(LOT_A, "base"))
    assert e.verdict is Verdict.INCONCLUSIVE and e.p_tost is None
    assert "at least 2 measured fields" in e.sentence
    assert e.n_fields_needed and e.more_fields_test == e.n_fields_needed - 1
    s = lot_summary([7.0])
    assert s.mean == 7.0 and s.se is None and s.ci_low is None
    assert pair_diff(s, lot_summary(LOT_A)).ci_low is None


def test_zero_variance_lots():
    e = tost_equivalence(lot_summary([7.1] * 3), lot_summary([7.0] * 4))
    assert e.verdict is Verdict.EQUIVALENT and e.se == 0 and e.p_tost == 0.0
    e = tost_equivalence(lot_summary([7.8] * 3), lot_summary([7.0] * 4))
    assert e.verdict is Verdict.NOT_EQUIVALENT
    # one lot zero variance, other not: ordinary Welch still defined
    e = tost_equivalence(lot_summary([7.0] * 3), lot_summary(LOT_A))
    assert e.verdict is Verdict.EQUIVALENT and e.se > 0
    assert fields_needed_tost(0.0, 0.1) == 2


def test_nan_excluded_and_uncalibrated_fields_dropped():
    fields = [FieldResult(field_id="1", G=7.0), FieldResult(field_id="2", G=float("nan")),
              FieldResult(field_id="3", G=None), FieldResult(field_id="4", G=7.2),
              FieldResult(field_id="5", G=9.9, included=False),
              {"G": 7.1}, {"G": "bad"}]
    s = lot_summary(fields)
    assert s.n == 3 and s.n_dropped == 4 and s.mean == pytest.approx(7.1)
    e = tost_equivalence(lot_summary([float("nan")], "new"), lot_summary(LOT_A))
    assert e.verdict is Verdict.INCONCLUSIVE and e.dG is None
    assert "no calibrated G" in e.sentence


def test_not_equivalent_via_clear_difference_rule():
    # best estimate beyond margin and CI excludes 0, but CI straddles +margin
    x = [7.3, 7.9, 7.5, 7.8, 7.6]
    e = tost_equivalence(lot_summary(x), lot_summary(LOT_A))
    assert e.dG > 0.5 and e.ci_low < 0.5 and e.ci_low > 0
    assert e.verdict is Verdict.NOT_EQUIVALENT


def test_fields_needed_behaviour():
    assert fields_needed_tost(0.1, 0.6) is None                # beyond margin
    n_small = fields_needed_tost(0.14, 0.1)
    n_big = fields_needed_tost(0.14, 0.4)
    assert 2 <= n_small < n_big
    assert fields_needed_tost(0.3, 0.0) < fields_needed_tost(0.3, 0.2)


# ---------------------------------------------------------------- compare_lots / baseline

def test_compare_lots_end_to_end():
    lots = {"L1": LOT_A, "L2": [7.02, 6.93, 7.08, 6.97, 7.06, 6.99],
            "L3": _shift(LOT_A, 0.8), "L4": [7.3]}
    c = compare_lots(lots, baseline="L1")
    assert [s.label for s in c.summaries] == ["L1", "L2", "L3", "L4"]
    assert len(c.matrix) == 4 and len(c.matrix[0]) == 4
    assert c.anova.k == 3 and c.anova.p < 1e-6
    assert set(c.equivalence) == {"L2", "L3", "L4"}
    assert c.equivalence["L2"].verdict is Verdict.EQUIVALENT
    assert c.equivalence["L3"].verdict is Verdict.NOT_EQUIVALENT
    assert c.equivalence["L4"].verdict is Verdict.INCONCLUSIVE
    d = c.to_dict()
    assert d["equivalence"]["L3"]["verdict"] == "Not equivalent"
    assert compare_lots(lots).equivalence == {}
    with pytest.raises(KeyError):
        compare_lots(lots, baseline="nope")


def test_find_baseline_lot():
    recs = [{"lot_number": "A", "material": "316L"},
            {"lot_number": "B", "material": "316L", "is_baseline": True},
            {"lot_number": "C", "material": "718", "is_baseline": True}]
    assert find_baseline_lot(recs, "316L")["lot_number"] == "B"
    assert find_baseline_lot(recs, "718")["lot_number"] == "C"
    assert find_baseline_lot(recs, "Ti64") is None
    assert find_baseline_lot(recs)["lot_number"] == "C"
    assert find_baseline_lot([]) is None
