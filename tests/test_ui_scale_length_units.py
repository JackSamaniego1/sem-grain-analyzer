"""
UPDATE 4 item 6: the scale-bar length is a number box plus a separate unit
dropdown (nm / µm / mm); after Auto-find the length box pulses and takes
focus so the user sees where to type.
"""
import pytest

pytest.importorskip("pytestqt")

from tests.test_ui_ux_v301 import TIMEOUT, _lots, _open, env  # noqa: E402,F401


def test_split_length_um_picks_natural_unit():
    from ui.calibration_dialog import LENGTH_UNITS, split_length_um
    assert LENGTH_UNITS == ("nm", "µm", "mm")
    assert split_length_um(0.5) == (500.0, "nm")
    assert split_length_um(20.0) == (20.0, "µm")
    assert split_length_um(2500.0) == (2.5, "mm")


def _tile(qtbot):
    from ui.pages.analyze_page import SetupTile
    t = SetupTile()
    qtbot.addWidget(t)
    return t


def test_setup_tile_number_and_unit_are_separate(qapp, qtbot):
    t = _tile(qtbot)
    assert t.bar_len.suffix() == ""                          # no unit inside the box
    assert [t.bar_unit.itemText(i) for i in range(t.bar_unit.count())] == ["nm", "µm", "mm"]
    assert t.bar_unit.currentText() == "µm" and t.bar_length_um() == 0.0
    # programmatic setters (auto-detect / session values) split value + unit
    t.set_bar_length_um(0.2)
    assert (t.bar_len.value(), t.bar_unit.currentText()) == (200.0, "nm")
    assert t.bar_length_um() == pytest.approx(0.2)
    assert t.set_bar_length_text("2.5 mm")
    assert (t.bar_len.value(), t.bar_unit.currentText()) == (2.5, "mm")
    assert t.set_bar_length_text("10 um") and t.bar_unit.currentText() == "µm"
    assert t.set_bar_length_text("50") and t.bar_length_um() == pytest.approx(50.0)
    assert not t.set_bar_length_text("ten microns")
    # clearing keeps the unit the user chose
    t.bar_unit.setCurrentText("nm")
    t.set_bar_length_um(0.0)
    assert t.bar_len.value() == 0.0 and t.bar_unit.currentText() == "nm"


def test_setup_tile_apply_emits_micrometres(qapp, qtbot):
    t = _tile(qtbot)
    t.bar_row.show()
    got = []
    t.bar_length_entered.connect(lambda um, same: got.append((um, same)))
    t.btn_bar.click()                                        # nothing entered -> nothing
    assert got == []
    t.bar_len.setValue(500.0)
    t.bar_unit.setCurrentText("nm")
    t.bar_same.setChecked(False)
    t.btn_bar.click()
    assert got[-1][0] == pytest.approx(0.5) and got[-1][1] is False
    t.bar_unit.setCurrentText("mm")
    t.bar_len.setValue(1.0)
    t.btn_bar.click()
    assert got[-1][0] == pytest.approx(1000.0)


def test_attention_ring_pulses_then_goes(qapp, qtbot):
    from ui.design.theme import reduced_motion, set_reduced_motion
    from ui.widgets.attention import AttentionRing
    t = _tile(qtbot)
    t.resize(900, 200)
    t.show()
    assert not t.draw_attention_to_length()                  # row hidden -> nothing
    t.bar_row.show()
    was = reduced_motion()
    set_reduced_motion(False)
    try:
        assert t.draw_attention_to_length()
        ring = t.bar_len._attention_ring
        assert isinstance(ring, AttentionRing) and ring.isVisible()
        assert ring.geometry().contains(t.bar_len.geometry())
        qtbot.waitUntil(lambda: ring.strength() > 0.5, timeout=2000)   # it animates
        qtbot.waitUntil(lambda: t.bar_len._attention_ring is None, timeout=4000)
    finally:
        set_reduced_motion(was)


def test_autofind_highlights_and_focuses_length_box(env, qtbot):  # noqa: F811
    sample = _lots(env, 1, 2, bar=True)
    lot = next(p for p in sample.iterdir() if p.is_dir() and (p / "lot.json").exists())
    rec = next(d for d in lot.iterdir() if (d / "manifest.json").exists())
    shell = _open(qtbot, rec)
    st, tile = shell.state, shell.analyze.setup_tile
    shell.activateWindow()
    tile.btn_auto.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=TIMEOUT)
    qtbot.waitUntil(lambda: getattr(tile.bar_len, "_attention_ring", None) is not None,
                    timeout=5000)
    assert tile.bar_row.isVisible()
    assert tile.bar_len.hasFocus() or shell.focusWidget() is tile.bar_len
    # typing a number + choosing a unit sets the scale
    tile.bar_len.setValue(20.0)
    tile.bar_unit.setCurrentText("µm")
    tile.btn_bar.click()
    for im in st.images():
        assert st.px_for(im) == pytest.approx(im.bar_px / 20.0)
    shell.close()
