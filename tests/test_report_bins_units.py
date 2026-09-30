"""Units & bins bug (UPDATE 4 item 16): unit switch only rescales; bin edges
follow the data range in the displayed unit; more bins = smaller step."""
import numpy as np
import pytest

from reports.charts import bin_labels, build_bins

AREA_F = 1e6     # µm² -> nm²
DIAM_F = 1e3     # µm  -> nm


def _data(seed=0, n=200):
    rng = np.random.default_rng(seed)
    return rng.normal(0.02, 0.005, n).clip(0.005, 0.05)      # µm² (= ~20,000 nm²)


@pytest.mark.parametrize("factor", [AREA_F, DIAM_F])
@pytest.mark.parametrize("nb", [8, 20])
def test_unit_switch_only_rescales(factor, nb):
    base = _data()
    _, c_um, e_um = build_bins(base, nb)
    _, c_nm, e_nm = build_bins(base * factor, nb)
    assert len(c_um) == len(c_nm) == nb
    assert c_um == c_nm                                        # same grains per bin
    assert np.allclose(np.array(e_nm), np.array(e_um) * factor, rtol=1e-12)


def test_more_bins_means_smaller_step_in_every_unit():
    base = _data()
    for factor in (1.0, AREA_F, 1e-3):
        steps = []
        for nb in (5, 10, 20, 40):
            _, counts, edges = build_bins(base * factor, nb)
            assert len(counts) == nb
            steps.append(edges[1] - edges[0])
        assert all(a > b for a, b in zip(steps, steps[1:]))


def test_no_single_bin_collapse_for_20000_nm2_in_um2():
    """Regression: grains ~20,000 nm² viewed in µm² all landed in one 0-1 bin
    and raising the bin count did nothing."""
    um2 = _data() * 1.0                                        # 0.005-0.05 µm²
    for nb in (10, 25):
        labels, counts, edges = build_bins(um2, nb)
        assert len(counts) == nb
        assert sum(1 for c in counts if c > 0) > 3
        assert max(counts) < len(um2)
        assert edges[0] == pytest.approx(um2.min())
        assert edges[-1] == pytest.approx(um2.max())
        assert edges[-1] < 1.0
    # and the edges actually move when the count changes
    assert build_bins(um2, 10)[2] != build_bins(um2, 25)[2]


def test_labels_have_enough_digits_for_small_values():
    labels, _, edges = build_bins(np.linspace(0.005, 0.05, 100), 9)
    assert labels[0] == "0.0050-0.0100"
    assert all(a != b for a, b in (lb.split("-") for lb in labels))
    assert bin_labels([0, 5000, 10000]) == ["0-5000", "5000-10000"]


def test_auto_bins_and_degenerate_input():
    assert build_bins([1.0], 5) == ([], [], [])
    _, counts, edges = build_bins([3.0, 3.0, 3.0], 4)
    assert sum(counts) == 3 and edges[0] < 3.0 < edges[-1]
    _, counts, _ = build_bins(_data(), 0)
    assert 5 <= len(counts) <= 25


from reports.charts import normal_fit


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_values_are_ignored(bad):
    base = list(_data(n=50))
    _, c_clean, e_clean = build_bins(base, 10)
    _, c_bad, e_bad = build_bins(base + [bad], 10)
    assert c_clean == c_bad and e_clean == e_bad
    fit = normal_fit(base + [bad], e_bad)
    assert np.all(np.isfinite(fit)) and max(fit) > 0
    assert fit == normal_fit(base, e_bad)


@pytest.mark.parametrize("nb", [4, 12])
def test_negative_values(nb):
    vals = np.linspace(-5, 5, 101)
    _, counts, edges = build_bins(vals, nb)
    assert len(counts) == nb and sum(counts) == 101
    assert edges[0] == -5 and edges[-1] == 5


def test_bin_count_capped_at_200():
    _, counts, _ = build_bins(np.linspace(0, 1, 5000), 5000)
    assert len(counts) == 200


@pytest.mark.parametrize("edges,expected", [
    ([0, 10, 20], ["0-10", "10-20"]),
    ([0, 25.5, 51], ["0-26", "26-51"]),
    ([0, 0.9, 1.8], ["0.00-0.90", "0.90-1.80"]),
    ([0, 1.05, 2.1], ["0.0-1.1", "1.1-2.1"]),
])
def test_bin_labels_steps(edges, expected):
    assert bin_labels(edges) == expected
