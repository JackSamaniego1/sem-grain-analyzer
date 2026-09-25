"""
Bug fix: a report over images from more than one lot showed the SAME
sample/lot on every Overview row.

Root cause: ``ui.pages.report_builder.collect_inputs()`` stamped the single
session-wide ``sample``/``lot`` (``_session_ids``) onto every
``ReportImageInput`` and never set ``levels``, so ``ReportModel.row_levels``
always fell back to the report-level hierarchy value (one value for the
whole report) -- even though a per-image lookup (``image_levels``) already
existed. This module proves the fix: each image now gets its own
project/sample/lot from ``image_levels`` (``AppState.session.record_for``),
falling back to the session's own value only when the image carries none.
"""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")

import cv2  # noqa: E402

from tests.conftest import make_mosaic  # noqa: E402
from core.grain_detector import GrainDetector, DetectionParams  # noqa: E402
from data.models import SessionMeta  # noqa: E402
from reports.model import ReportModel  # noqa: E402
from ui.app_state import AppState, ImageDoc, RecordRef, SessionDoc  # noqa: E402
from ui.pages import report_builder as rb  # noqa: E402


def _result(tmp_path, seed, name, px_per_um=8.0):
    det = GrainDetector()
    gray, _ = make_mosaic(seed=seed, h=160, w=160, n_grains=15)
    bgr = np.repeat(gray[:, :, None], 3, axis=2)
    res = det.analyze(bgr, px_per_um=px_per_um, params=DetectionParams())
    p = tmp_path / name
    cv2.imwrite(str(p), bgr)
    return p, bgr, res


def _two_lot_state(tmp_path, monkeypatch):
    """A session whose images belong to two different lots (``L-1``/``L-2``
    under the same project/sample), the ``AppState.session.records``
    (UX-09) machinery uses to tell them apart. The session's OWN
    (single-value) meta is deliberately stamped with the first lot only --
    exactly the shape that used to leak onto every image."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    st = AppState(settings_path=tmp_path / "settings.json")
    doc = SessionDoc(path=tmp_path / "sess",
                     meta=SessionMeta(project="24-117", sample_id="7718-A",
                                      lot_number="L-1", operator="Tester"))
    st.session = doc

    root = tmp_path / "ws" / "24-117" / "7718-A"
    lot1, lot2 = root / "L-1", root / "L-2"
    for lot in (lot1, lot2):
        lot.mkdir(parents=True)
        (lot / "lot.json").write_text("{}", encoding="utf-8")
    rec1 = RecordRef(path=lot1, meta=SessionMeta())
    rec2 = RecordRef(path=lot2, meta=SessionMeta())
    doc.records = [rec1, rec2]

    images = []
    for i, rec in enumerate((rec1, rec1, rec2, rec2)):
        p, bgr, res = _result(tmp_path, seed=i + 1, name=f"img_{i}.png")
        im = ImageDoc(filename=p.name, path=p, image_bgr=bgr, raw=res, result=res, status="done")
        im.record = rec
        doc.images.append(im)
        images.append(im)
    return st, images


def test_image_levels_are_distinct_per_lot(tmp_path, qapp, monkeypatch):
    st, images = _two_lot_state(tmp_path, monkeypatch)
    lots = [rb.image_levels(st, im)["lot"] for im in images]
    assert lots == ["L-1", "L-1", "L-2", "L-2"]
    samples = {rb.image_levels(st, im)["sample"] for im in images}
    assert samples == {"7718-A"}                 # same part, different lots


def test_collect_inputs_tags_each_image_with_its_own_lot(tmp_path, qapp, monkeypatch):
    st, images = _two_lot_state(tmp_path, monkeypatch)
    inputs = rb.collect_inputs(st)
    assert len(inputs) == 4
    lots = [i.lot_number for i in inputs]
    assert lots == ["L-1", "L-1", "L-2", "L-2"]
    assert all(i.sample_id == "7718-A" for i in inputs)
    assert all(i.levels.get("lot") == lot for i, lot in zip(inputs, lots))


def test_collect_inputs_falls_back_to_session_ids_for_a_plain_single_lot_session(
        tmp_path, qapp, monkeypatch):
    """No per-image record info at all (a plain, non-hierarchy session):
    every image still falls back to the session's own sample/lot -- the
    fix must not regress the common single-lot case."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    st = AppState(settings_path=tmp_path / "settings.json")
    doc = SessionDoc(path=tmp_path / "sess",
                     meta=SessionMeta(sample_id="S-9", lot_number="L-9"))
    st.session = doc
    for i in range(2):
        p, bgr, res = _result(tmp_path, seed=i + 1, name=f"plain_{i}.png")
        im = ImageDoc(filename=p.name, path=p, image_bgr=bgr, raw=res, result=res, status="done")
        doc.images.append(im)
    inputs = rb.collect_inputs(st)
    assert [i.sample_id for i in inputs] == ["S-9", "S-9"]
    assert [i.lot_number for i in inputs] == ["L-9", "L-9"]


def _render_overview_rows(model, tmp_path, name="report.xlsx"):
    import openpyxl
    from reports.excel_renderer import render_excel
    out = str(tmp_path / name)
    render_excel(model, out)
    ws = openpyxl.load_workbook(out)["Overview"]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows) if r and r[0] == "#")
    header = rows[hdr_i]
    data = [r for r in rows[hdr_i + 1:] if r[1] and r[1] != "Combined (all images)"]
    return header, data


def test_overview_sheet_shows_distinct_sample_and_lot_per_row_legacy_columns(tmp_path, monkeypatch):
    """No hierarchy set (legacy Sample/Lot columns): each row must show its
    OWN image's sample/lot, not the report-wide value every row used to
    fall back to."""
    st, images = _two_lot_state(tmp_path, monkeypatch)
    inputs = rb.collect_inputs(st)
    model = ReportModel.from_results(inputs, title="Two Lots", asset_dir=str(tmp_path / "assets"))
    header, data = _render_overview_rows(model, tmp_path)
    assert header[2] == "Sample" and header[3] == "Lot"
    assert [r[3] for r in data] == ["L-1", "L-1", "L-2", "L-2"]
    assert all(r[2] == "7718-A" for r in data)


def test_overview_sheet_shows_distinct_lot_per_row_with_hierarchy(tmp_path, monkeypatch):
    """HIER-01 hierarchy columns (Job #/Part Number/Lot): the root-cause
    line -- ``ReportModel.row_levels`` via ``ImageSummary.levels`` -- must
    resolve per row, not to the single report-level hierarchy value."""
    st, images = _two_lot_state(tmp_path, monkeypatch)
    inputs = rb.collect_inputs(st)
    hierarchy = [{"key": "project", "label": "Job #", "value": "24-117"},
                {"key": "sample", "label": "Part Number", "value": "7718-A"},
                {"key": "lot", "label": "Lot", "value": "L-1"}]   # report-level fallback: L-1
    model = ReportModel.from_results(inputs, title="Two Lots", asset_dir=str(tmp_path / "assets"),
                                     hierarchy=hierarchy)
    header, data = _render_overview_rows(model, tmp_path)
    lot_col = header.index("Lot")
    assert [r[lot_col] for r in data] == ["L-1", "L-1", "L-2", "L-2"]


def test_report_context_aggregates_distinct_lots_instead_of_just_the_first(
        tmp_path, qapp, monkeypatch):
    """The report-level header/title (``ReportModel.hierarchy``'s "value")
    must show every distinct lot among the open images ("L-1, L-2"), not
    silently collapse to the session's single first-lot value."""
    from ui import hierarchy_ui as hui
    st, images = _two_lot_state(tmp_path, monkeypatch)
    monkeypatch.setattr(hui, "context_for_path",
                        lambda path, profile: {"project": "24-117", "sample": "7718-A",
                                               "lot": "L-1", "operator": "Tester"})
    ctx = rb.report_context(st)
    assert ctx["lot"] == "L-1, L-2"
    assert ctx["sample"] == "7718-A"              # only one distinct value -- unchanged


def test_report_context_single_lot_session_unchanged(tmp_path, qapp, monkeypatch):
    from ui import hierarchy_ui as hui
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    st = AppState(settings_path=tmp_path / "settings.json")
    doc = SessionDoc(path=tmp_path / "sess",
                     meta=SessionMeta(sample_id="S-9", lot_number="L-9", operator="Tester"))
    st.session = doc
    for i in range(2):
        p, bgr, res = _result(tmp_path, seed=i + 1, name=f"plain_{i}.png")
        im = ImageDoc(filename=p.name, path=p, image_bgr=bgr, raw=res, result=res, status="done")
        doc.images.append(im)
    monkeypatch.setattr(hui, "context_for_path",
                        lambda path, profile: {"sample": "S-9", "lot": "L-9", "operator": "Tester"})
    ctx = rb.report_context(st)
    assert ctx["lot"] == "L-9"
    assert ctx["sample"] == "S-9"
