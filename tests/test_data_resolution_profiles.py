"""Resolution Profiles store, fit check, and manifest snapshot."""
import json

import numpy as np
import pytest

from data.models import CLEAR, ImageEntry, ImageManifestEntry
from data.resolution_profiles import (FIT_OK, FIT_SIZE_MISMATCH, ProfileError,
                                      ProfileStore, check_fit, nm_per_px_from)
from data.session_io import load_session, save_session, update_session
from data.workspace import Workspace


def _store(tmp_path, name="p.json"):
    return ProfileStore(tmp_path / name)


def _add(s, name="A", **kw):
    kw.setdefault("nm_per_px", 10.0)
    return s.add(name, scan_rect=(0, 0, 90, 80), image_size=(100, 90), **kw)


def test_roundtrip_sorted_and_units(tmp_path):
    s = _store(tmp_path)
    _add(s, "beta", unit="nm")
    a = _add(s, "Alpha", instrument="Zeiss", magnification="5000x")
    s2 = _store(tmp_path)
    assert [p.name for p in s2.list()] == ["Alpha", "beta"]
    got = s2.get(a.id)
    assert got.scan_rect == (0, 0, 90, 80) and (got.image_w, got.image_h) == (100, 90)
    assert got.px_per_um == pytest.approx(100.0)
    assert got.instrument == "Zeiss"
    assert nm_per_px_from(1, "um", 50) == pytest.approx(20.0)
    with pytest.raises(ProfileError):
        nm_per_px_from(0, "um", 50)


def test_duplicate_and_empty_names(tmp_path):
    s = _store(tmp_path)
    a = _add(s, "Alpha")
    with pytest.raises(ProfileError):
        _add(s, "  alpha ")
    with pytest.raises(ProfileError):
        _add(s, "   ")
    b = _add(s, "Beta")
    with pytest.raises(ProfileError):
        s.rename(b.id, "ALPHA")
    assert s.rename(a.id, "ALPHA").name == "ALPHA"   # own name, case change ok
    assert s.rename(b.id, "Gamma").name == "Gamma"
    assert s.delete(b.id) and not s.delete(b.id)


def test_corrupt_and_missing_file(tmp_path):
    assert _store(tmp_path).list() == [] and _store(tmp_path).warnings == []
    (tmp_path / "bad.json").write_text("{{nope", encoding="utf-8")
    s = _store(tmp_path, "bad.json")
    assert s.list() == [] and s.warnings
    _add(s, "ok")     # recovers by overwriting atomically
    assert len(_store(tmp_path, "bad.json").list()) == 1


def test_bad_entries_skipped_unknown_keys_kept(tmp_path):
    doc = {"schema_version": 1, "future_top": 5, "profiles": [
        {"name": "good", "nm_per_px": 5, "future": {"x": 1}},
        {"name": "", "nm_per_px": 5}, {"name": "neg", "nm_per_px": -1},
        "junk", {"name": "GOOD", "nm_per_px": 3}]}
    p = tmp_path / "p.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    s = ProfileStore(p)
    assert [x.name for x in s.list()] == ["good"] and s.warnings
    s.save()
    out = json.loads(p.read_text(encoding="utf-8"))
    assert out["future_top"] == 5 and out["profiles"][0]["future"] == {"x": 1}
    assert not list(tmp_path.glob(".*.tmp*"))


def test_export_import(tmp_path):
    s1 = _store(tmp_path, "a.json")
    a = _add(s1, "Alpha")
    _add(s1, "Beta")
    assert s1.export_to(tmp_path / "usb.json") == 2
    s2 = _store(tmp_path, "b.json")
    _add(s2, "alpha")            # name clash, different id
    r = s2.import_from(tmp_path / "usb.json")
    assert (r.added, r.renamed) == (2, 1)
    assert {p.name for p in s2.list()} == {"alpha", "Alpha (2)", "Beta"}
    r = s2.import_from(tmp_path / "usb.json")      # same ids again
    assert r.added == 0 and r.skipped_existing == 2
    (tmp_path / "x.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ProfileError):
        s2.import_from(tmp_path / "x.json")
    assert s1.export_to(tmp_path / "one.json", ids=[a.id]) == 1


def test_check_fit(tmp_path):
    p = _add(_store(tmp_path))
    ok = check_fit(p, 100, 90)
    assert ok.ok and ok.status == FIT_OK and ok.scan_rect == (0, 0, 90, 80)
    assert ok.px_per_um == pytest.approx(100.0)
    bad = check_fit(p, 200, 180)
    assert bad.status == FIT_SIZE_MISMATCH and not bad.ok and "200 x 180" in bad.message
    assert bad.px_per_um == 0.0 and bad.scan_rect is None


def test_manifest_snapshot_survives_profile_delete_and_old_manifest(tmp_path):
    ws = Workspace(tmp_path / "ws")
    proj = ws.create_project("P")
    lot = ws.create_lot(proj, ws.create_sample(proj, "S"), "L")
    store = _store(tmp_path)
    prof = _add(store, "Prof")
    img = np.full((40, 40, 3), 120, np.uint8)
    e = ImageEntry(image_bgr=img, filename="a.png", resolution_profile=prof.snapshot())
    ref = save_session(lot, {"project": "P", "sample_id": "S", "lot_number": "L"}, [e])
    store.delete(prof.id)
    m = load_session(ref.path).manifest.images[0]
    assert m.resolution_profile["id"] == prof.id
    assert m.resolution_profile["name"] == "Prof"
    assert m.resolution_profile["nm_per_px"] == 10.0
    # update leaves it alone; CLEAR removes it
    update_session(ref.path, images=[ImageEntry(filename="a.png")])
    assert load_session(ref.path).manifest.images[0].resolution_profile is not None
    update_session(ref.path, images=[ImageEntry(filename="a.png", resolution_profile=CLEAR)])
    assert load_session(ref.path).manifest.images[0].resolution_profile is None
    # old manifest without the key
    assert ImageManifestEntry.from_dict({"filename": "x.png"}).resolution_profile is None


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------
import data.resolution_profiles as rp
from data.resolution_profiles import FIT_SCAN_OUTSIDE, MAX_IMPORT_BYTES, ResolutionProfile

UM = "µm"


def _write(tmp_path, profiles, name="p.json", **top):
    doc = {"schema_version": 1, "profiles": profiles}
    doc.update(top)
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _boom(*a, **k):
    raise OSError("read-only")


@pytest.mark.parametrize("spelling", ["um", "µm", "μm", " UM "])
def test_unit_aliases(tmp_path, spelling):
    assert nm_per_px_from(1, spelling, 50) == pytest.approx(20.0)
    s = _store(tmp_path)
    p = _add(s, "A", unit=spelling)
    assert p.unit == UM
    assert s.update(p.id, unit="nm").unit == "nm"
    assert s.update(p.id, unit=spelling).unit == UM
    assert nm_per_px_from(1, "mm", 1) == pytest.approx(1e6)
    with pytest.raises(ProfileError):
        nm_per_px_from(1, "km", 5)


def test_old_um_file_loads_and_display(tmp_path):
    p = _write(tmp_path, [{"name": "old", "nm_per_px": 12.5, "unit": "um"},
                          {"name": "mu", "nm_per_px": 12.5, "unit": "μm"},
                          {"name": "n", "nm_per_px": 12.5, "unit": "nm"},
                          {"name": "weird", "nm_per_px": 12.5, "unit": "km"}])
    s = ProfileStore(p)
    by = {x.name: x for x in s.list()}
    assert by["old"].unit == UM and by["mu"].unit == UM and by["weird"].unit == UM
    assert by["old"].display_ratio() == "0.0125 µm/px"
    assert by["n"].display_ratio() == "12.5 nm/px"
    assert ResolutionProfile(nm_per_px=2_000_000, unit="mm").display_ratio() == "2 mm/px"


def test_save_failure_rolls_back(tmp_path, monkeypatch):
    s = _store(tmp_path)
    a = _add(s, "A")
    b = _add(s, "B")
    src = _store(tmp_path, "src.json")
    _add(src, "Other")
    src.export_to(tmp_path / "usb.json")
    monkeypatch.setattr(rp, "write_json_atomic", _boom)
    with pytest.raises(ProfileError):
        _add(s, "C")
    assert [x.name for x in s.list()] == ["A", "B"]
    with pytest.raises(ProfileError):
        s.update(a.id, name="Z", nm_per_px=99)
    assert s.get(a.id).name == "A" and s.get(a.id).nm_per_px == 10.0
    with pytest.raises(ProfileError):
        s.rename(a.id, "Z")
    with pytest.raises(ProfileError):
        s.delete(b.id)
    assert s.get(b.id) is not None
    with pytest.raises(ProfileError):
        s.import_from(tmp_path / "usb.json")
    assert [x.name for x in s.list()] == ["A", "B"]
    with pytest.raises(ProfileError):
        s.export_to(tmp_path / "out.json")
    monkeypatch.undo()
    assert [x.name for x in _store(tmp_path).list()] == ["A", "B"]   # disk unchanged


def test_unwritable_location_is_profile_error(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    s = ProfileStore(blocker / "p.json")     # parent is a file: cannot be written
    with pytest.raises(ProfileError):
        s.add("A", 5.0)
    assert s.list() == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 0, -3, "abc", None, True])
def test_bad_ratio_via_api(tmp_path, bad):
    s = _store(tmp_path)
    with pytest.raises(ProfileError):
        s.add("A", bad)
    a = _add(s, "B")
    with pytest.raises(ProfileError):
        s.update(a.id, nm_per_px=bad)
    assert s.get(a.id).nm_per_px == 10.0
    with pytest.raises(ProfileError):
        nm_per_px_from(bad, "nm", 5)
    with pytest.raises(ProfileError):
        nm_per_px_from(5, "nm", bad)


def test_string_number_ratio_coerced(tmp_path):
    s = _store(tmp_path)
    a = _add(s, "A")
    assert s.update(a.id, nm_per_px="7.5").nm_per_px == 7.5


def test_bad_ratios_in_file_skipped(tmp_path):
    good = {"name": "good", "nm_per_px": 5}
    vals = [float("nan"), float("inf"), 0, -1, "x", None, "nan", "inf"]
    bads = [{"name": f"b{i}", "nm_per_px": v} for i, v in enumerate(vals)]
    p = tmp_path / "p.json"
    # json.dumps writes NaN / Infinity tokens, which Python reads back
    p.write_text(json.dumps({"schema_version": 1, "profiles": [good] + bads}),
                 encoding="utf-8")
    s = ProfileStore(p)
    assert [x.name for x in s.list()] == ["good"]
    assert "8" in " ".join(s.warnings)
    with pytest.raises(ProfileError):
        ResolutionProfile.from_dict({"name": "n", "nm_per_px": float("nan")})


def test_duplicate_ids_in_file(tmp_path):
    p = _write(tmp_path, [{"id": "x", "name": "A", "nm_per_px": 1},
                          {"id": "x", "name": "B", "nm_per_px": 2}])
    s = ProfileStore(p)
    assert [x.name for x in s.list()] == ["A"] and s.warnings


def test_wrong_type_fields_in_entry(tmp_path):
    p = _write(tmp_path, [
        {"name": "w", "nm_per_px": 1, "image_w": "x", "image_h": 5},
        {"name": "r3", "nm_per_px": 1, "scan_rect": [1, 2, 3]},
        {"name": "r5", "nm_per_px": 1, "scan_rect": [1, 2, 3, 4, 5]},
        {"name": "rs", "nm_per_px": 1, "scan_rect": "abcd"},
        {"name": "rn", "nm_per_px": 1, "scan_rect": [1, 2, "a", 4]},
        {"name": "neg", "nm_per_px": 1, "image_w": -4},
        {"name": "ok", "nm_per_px": 1, "scan_rect": [1, 2, 3, 4], "image_w": "100"}])
    s = ProfileStore(p)
    assert [x.name for x in s.list()] == ["ok"]
    assert s.list()[0].scan_rect == (1, 2, 3, 4) and s.list()[0].image_w == 100
    assert "6" in " ".join(s.warnings)


def test_update_validation(tmp_path):
    s = _store(tmp_path)
    a = _add(s, "A")
    for kw in ({"bogus": 1}, {"unit": "km"}, {"unit": 5}, {"name": 5}, {"name": " "},
               {"image_w": "x"}, {"image_h": -1}, {"image_w": True},
               {"scan_rect": (1, 2, 3)}, {"scan_rect": "abcd"},
               {"scan_rect": (1, 2, "a", 4)}):
        with pytest.raises(ProfileError):
            s.update(a.id, **kw)
    with pytest.raises(ProfileError):
        s.update("nope", name="X")
    unchanged = s.get(a.id)
    assert unchanged.name == "A" and unchanged.scan_rect == (0, 0, 90, 80)
    u = s.update(a.id, image_w="200", image_h=150.0, scan_rect=[1, 2, 30, 40],
                 instrument="Zeiss")
    assert (u.image_w, u.image_h, u.scan_rect, u.instrument) == (200, 150, (1, 2, 30, 40), "Zeiss")
    assert s.update(a.id, scan_rect=None).scan_rect is None
    assert _store(tmp_path).get(a.id).image_w == 200


def test_newer_schema_read_only(tmp_path):
    p = _write(tmp_path, [{"name": "A", "nm_per_px": 1}], schema_version=99)
    before = p.read_text(encoding="utf-8")
    s = ProfileStore(p)
    assert s.read_only and s.read_only_reason and s.warnings
    assert [x.name for x in s.list()] == ["A"]
    a = s.list()[0]
    src = _store(tmp_path, "src.json")
    _add(src, "Q")
    src.export_to(tmp_path / "usb.json")
    for call in (lambda: s.add("B", 2), lambda: s.update(a.id, name="Z"),
                 lambda: s.rename(a.id, "Z"), lambda: s.delete(a.id), s.save,
                 lambda: s.import_from(tmp_path / "usb.json")):
        with pytest.raises(ProfileError):
            call()
    assert p.read_text(encoding="utf-8") == before and s.get(a.id) is not None
    assert s.export_to(tmp_path / "copy.json") == 1     # export still allowed


def test_import_rename_collisions(tmp_path):
    s = _store(tmp_path, "a.json")
    for n in ("Name", "Name (2)"):
        _add(s, n)
    inc = _write(tmp_path, [{"id": "i1", "name": "name", "nm_per_px": 1},
                            {"id": "i2", "name": "Fresh", "nm_per_px": 2}], "inc.json")
    r = s.import_from(inc)
    assert (r.added, r.renamed) == (2, 1)
    assert {p.name for p in s.list()} == {"Name", "Name (2)", "name (3)", "Fresh"}
    inc2 = _write(tmp_path, [{"id": "i3", "name": "NAME", "nm_per_px": 1}], "inc2.json")
    s.import_from(inc2)
    assert s.find_by_name("NAME (4)") is not None


def test_import_bad_files(tmp_path):
    s = _store(tmp_path)
    (tmp_path / "np.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    (tmp_path / "junk.json").write_text("not json {", encoding="utf-8")
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"profiles": [], "pad": "x" * (MAX_IMPORT_BYTES + 10)}),
                   encoding="utf-8")
    for f in ("np.json", "junk.json", "big.json", "missing.json"):
        with pytest.raises(ProfileError):
            s.import_from(tmp_path / f)
    with pytest.raises(ProfileError, match="5 MB"):
        s.import_from(big)
    assert s.list() == []


def test_check_fit_scan_outside_and_no_size(tmp_path):
    s = _store(tmp_path)
    p = s.add("A", 10.0, scan_rect=(50, 50, 80, 80), image_size=(200, 200))
    out = check_fit(ResolutionProfile(name="A", nm_per_px=10, scan_rect=(50, 50, 80, 80)),
                    100, 100)
    assert out.status == FIT_SCAN_OUTSIDE and not out.ok
    assert out.px_per_um == 0.0 and out.scan_rect is None and "outside" in out.message
    assert check_fit(p, 200, 200).ok
    # profile with no image size: size check skipped, bounds still checked
    n = s.add("NoSize", 10.0, scan_rect=(0, 0, 50, 50))
    assert check_fit(n, 60, 60).ok
    assert check_fit(n, 40, 40).status == FIT_SCAN_OUTSIDE
    assert check_fit(s.add("NoRect", 10.0), 5, 5).ok
    neg = ResolutionProfile(name="n", nm_per_px=10, scan_rect=(-1, 0, 5, 5))
    assert check_fit(neg, 100, 100).status == FIT_SCAN_OUTSIDE


def test_display_ratio():
    assert ResolutionProfile(nm_per_px=412, unit=UM).display_ratio() == "0.412 µm/px"
    assert ResolutionProfile(nm_per_px=412, unit="nm").display_ratio() == "412 nm/px"
    assert ResolutionProfile(nm_per_px=412, unit="um").display_ratio() == "0.412 µm/px"


def test_session_resave_keeps_and_clears_profile(tmp_path):
    ws = Workspace(tmp_path / "ws")
    proj = ws.create_project("P")
    lot = ws.create_lot(proj, ws.create_sample(proj, "S"), "L")
    prof = _add(_store(tmp_path), "Prof").snapshot()
    img = np.full((30, 30, 3), 100, np.uint8)
    ref = save_session(lot, {"project": "P", "sample_id": "S", "lot_number": "L"},
                       [ImageEntry(image_bgr=img, filename="a.png", resolution_profile=prof)])
    new = dict(prof, name="Other")
    update_session(ref.path, images=[ImageEntry(filename="a.png", resolution_profile=new)])
    assert load_session(ref.path).manifest.images[0].resolution_profile["name"] == "Other"
    update_session(ref.path, images=[ImageEntry(filename="a.png")])
    assert load_session(ref.path).manifest.images[0].resolution_profile["name"] == "Other"
    update_session(ref.path, images=[ImageEntry(filename="a.png", resolution_profile=CLEAR)])
    assert load_session(ref.path).manifest.images[0].resolution_profile is None


@pytest.mark.parametrize("junk", ["text", 5, [1, 2], ("a", "b")])
def test_save_session_non_dict_profile(tmp_path, junk):
    ws = Workspace(tmp_path / "ws")
    proj = ws.create_project("P")
    lot = ws.create_lot(proj, ws.create_sample(proj, "S"), "L")
    img = np.full((30, 30, 3), 100, np.uint8)
    ref = save_session(lot, {"project": "P", "sample_id": "S", "lot_number": "L"},
                       [ImageEntry(image_bgr=img, filename="a.png", resolution_profile=junk)])
    assert load_session(ref.path).manifest.images[0].resolution_profile is None
