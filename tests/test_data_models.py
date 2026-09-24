"""Unit tests for data/models.py: sanitization, dedupe, and the
forward-compatible dataclass <-> dict conversions."""
import numpy as np
import pytest

from core.grain_detector import AnalysisResult, GrainResult
from data.models import (
    AppSettings, ProjectMeta, SessionMeta,
    analysis_summary_from_dict, analysis_summary_to_dict,
    dedupe_name, grain_from_dict, grain_to_dict, sanitize_name,
)


def test_sanitize_name_strips_unsafe_characters():
    safe = sanitize_name("LOT/44:A")
    assert "/" not in safe and ":" not in safe
    assert safe  # non-empty


def test_sanitize_name_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_name("   ...   ")


def test_sanitize_name_reserved_device_name():
    safe = sanitize_name("CON")
    assert safe != "CON"


def test_dedupe_name_suffix(tmp_path):
    (tmp_path / "Sample-1").mkdir()
    name = dedupe_name(tmp_path, "Sample-1")
    assert name == "Sample-1 (2)"
    (tmp_path / "Sample-1 (2)").mkdir()
    name = dedupe_name(tmp_path, "Sample-1")
    assert name == "Sample-1 (3)"


def test_project_meta_roundtrip():
    pm = ProjectMeta(name="Alloy-718", description="d", customer="c")
    d = pm.to_dict()
    assert "path" not in d
    pm2 = ProjectMeta.from_dict(d)
    assert pm2.name == "Alloy-718"
    assert pm2.customer == "c"


def test_project_meta_forward_compat_unknown_and_missing_keys():
    d = {"schema_version": 1, "name": "X", "future_field_not_known_yet": 123}
    pm = ProjectMeta.from_dict(d)
    assert pm.name == "X"
    assert pm.description == ""  # default filled in
    assert not hasattr(pm, "future_field_not_known_yet")


def test_session_meta_images_roundtrip():
    d = {
        "schema_version": 1,
        "session_id": "2026-01-01_000000",
        "images": [{"filename": "a.png", "sha256": "abc", "grain_count": 5}],
    }
    meta = SessionMeta.from_dict(d)
    assert len(meta.images) == 1
    assert meta.images[0].filename == "a.png"
    assert meta.images[0].grain_count == 5
    out = meta.to_dict()
    assert out["images"][0]["filename"] == "a.png"


def test_grain_dict_roundtrip():
    g = GrainResult(
        grain_id=3, area_px=10.0, area_um2=1.0, perimeter_px=5.0, perimeter_um=0.5,
        equivalent_diameter_px=2.0, equivalent_diameter_um=0.2, major_axis_um=0.3,
        minor_axis_um=0.1, aspect_ratio=3.0, circularity=0.8, eccentricity=0.9,
        centroid_x=1.5, centroid_y=2.5, bbox=(0, 0, 5, 5),
    )
    d = grain_to_dict(g)
    assert d["bbox"] == [0, 0, 5, 5]
    g2 = grain_from_dict(d)
    assert g2 == g


def test_grain_from_dict_missing_and_unknown_keys():
    g = grain_from_dict({"grain_id": 7, "not_a_real_field": "x"})
    assert g.grain_id == 7
    assert g.area_px == 0.0
    assert g.bbox == (0, 0, 0, 0)


def test_analysis_summary_forward_compat():
    result = AnalysisResult(px_per_um=2.0, has_calibration=True, mean_area_um2=42.0)
    d = analysis_summary_to_dict(result)
    assert "grains" not in d and "label_image" not in d
    d["some_future_field"] = "ignored"
    kwargs = analysis_summary_from_dict(d)
    assert kwargs["mean_area_um2"] == 42.0
    rebuilt = AnalysisResult(**kwargs)
    assert rebuilt.px_per_um == 2.0

    # Missing keys (older manifest) fall back to AnalysisResult defaults.
    partial_kwargs = analysis_summary_from_dict({"px_per_um": 5.0})
    assert partial_kwargs["mean_area_um2"] == 0.0
    assert partial_kwargs["px_per_um"] == 5.0


def test_app_settings_defaults():
    s = AppSettings()
    assert s.recent_sessions == []
    assert s.theme == "system"
    assert "GrainAnalyzer" in s.workspace_root
