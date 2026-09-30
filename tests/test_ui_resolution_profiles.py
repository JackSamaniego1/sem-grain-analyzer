"""UPDATE 4 item 5 (UI): the Resolution Profiles card in the Analyze sidebar."""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data.models import AppSettings, ImageEntry  # noqa: E402
from data.resolution_profiles import ProfileStore, get_profiles_path  # noqa: E402
from data.settings import save_settings  # noqa: E402
from tests.conftest import make_mosaic  # noqa: E402

TIMEOUT = 60000


@pytest.fixture
def env(tmp_path, monkeypatch, qapp):
    from ui.ai_probe import reset_device_probe
    from ui.design.theme import apply_theme, set_reduced_motion
    import core.ai_device as aid
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: aid.AiDeviceInfo(
        torch_available=True, cpu_available=True, gpu_reason=aid.REASON_NO_GPU_BUILD))
    reset_device_probe()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    set_reduced_motion(True)
    apply_theme(qapp, "dark")
    yield root
    set_reduced_motion(False)
    reset_device_probe()


def _session(root: Path, sizes=((480, 360), (480, 360))) -> Path:
    from data.catalog import Catalog
    from data.session_io import save_session
    from data.workspace import Workspace
    ws = Workspace(root)
    pp = ws.create_project("Proj")
    sp = ws.create_sample(pp, "S-1")
    lp = ws.create_lot(pp, sp, "L-1")
    src = root.parent / "src"
    src.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (w, h) in enumerate(sizes):
        g, _ = make_mosaic(h=h, w=w, n_grains=40, seed=3 + i)
        p = src / f"img_{i}.png"
        cv2.imwrite(str(p), np.repeat(g[:, :, None], 3, axis=2))
        paths.append(str(p))
    ref = save_session(lp, {"operator": "Tester"}, [ImageEntry(source_path=p) for p in paths],
                       label="Run", catalog=Catalog(root))
    return Path(ref.path)


def _open(qtbot, path: Path):
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.resize(1500, 950)
    shell.show()
    shell.open_session(path, prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None, timeout=15000)
    qtbot.waitUntil(lambda: not shell.state.is_loading(), timeout=TIMEOUT)
    return shell


def _card(shell):
    return shell.analyze.profiles_card


def _values(st):
    return [(im.px_override, im.scan_rect, im.profile, im.status) for im in st.images()]


def test_card_off_by_default_and_inert(env, qtbot):
    shell = _open(qtbot, _session(env))
    card, page, st = _card(shell), shell.analyze, shell.state
    assert not card.is_on()
    assert card.body.isHidden() and not card.off_hint.isHidden()
    assert not page.setup_tile.isHidden() and page.profile_tile.isHidden()
    assert not get_profiles_path().exists()          # nothing touched on disk
    # switch persists per PC
    card.set_enabled(True)
    assert st.ui_state["resolution_profiles_on"] is True
    assert not card.body.isHidden()
    card.set_enabled(False)
    assert st.ui_state["resolution_profiles_on"] is False


def test_create_apply_persist_and_label_survives_delete(env, qtbot):
    path = _session(env)
    shell = _open(qtbot, path)
    card, page, st = _card(shell), shell.analyze, shell.state
    card.set_enabled(True)
    im = st.current_image()
    st.set_scan_rect((10, 20, 400, 300), im.uid)
    p = card.create_profile("Zeiss 5kx", 20.0, "µm", 100.0)       # 5 px/µm
    assert p is not None and card.combo.count() == 1
    assert "Zeiss 5kx" in card.combo.itemText(0) and "400 × 300" in card.combo.itemText(0)
    assert p.scan_rect == (10, 20, 400, 300) and p.image_w == 480 and p.image_h == 360
    # apply on the other image
    other = st.images()[1]
    other.status = "done"
    st.set_current_image(other.uid)
    done = card.apply_selected()
    assert done == [other.uid]
    assert st.px_for(other) == pytest.approx(5.0)
    assert st.scan_for(other) == (10, 20, 400, 300)
    assert other.profile["name"] == "Zeiss 5kx"
    assert other.status == "pending"                  # "Not analysed"
    from ui.pages.image_tree import image_status
    assert image_status(other)[1] == "Not analysed"
    assert page.setup_tile.isHidden() and not page.profile_tile.isHidden()
    assert "Zeiss 5kx" in page.profile_tile.title.text()
    # one undo step restores everything
    assert st.undo()
    assert other.profile is None and st.px_for(other) != pytest.approx(5.0)
    assert not page.setup_tile.isHidden()
    assert st.redo()
    assert other.profile["name"] == "Zeiss 5kx"
    # save + reopen: snapshot restored
    st.save_now()
    st.flush()
    shell.close()
    shell2 = _open(qtbot, path)
    st2 = shell2.state
    o2 = [i for i in st2.images() if i.filename == other.filename][0]
    assert o2.profile and o2.profile["name"] == "Zeiss 5kx"
    assert st2.px_for(o2) == pytest.approx(5.0)
    # deleting the profile keeps the label from the snapshot
    card2 = _card(shell2)
    assert card2.is_on()                              # switch remembered
    assert card2.delete_selected() and card2.selected() is None
    st2.set_current_image(o2.uid)
    assert not shell2.analyze.profile_tile.isHidden()
    assert "Zeiss 5kx" in shell2.analyze.profile_tile.title.text()


def test_size_mismatch_changes_nothing(env, qtbot):
    shell = _open(qtbot, _session(env, sizes=((480, 360), (320, 240))))
    card, st = _card(shell), shell.state
    card.set_enabled(True)
    msgs = []
    shell.analyze.toasts.show_toast = lambda t, m="", s="info", *a, **k: msgs.append((t, m, s))
    assert card.create_profile("Big", 1.0, "µm", 10.0) is not None
    small = [i for i in st.images() if i.shape[:2] == (240, 320)][0]
    st.set_current_image(small.uid)
    before = _values(st)
    assert card.apply_selected() == []
    assert _values(st) == before
    assert any(s == "warning" and "480 x 360" in m for _t, m, s in msgs)


def test_rename_delete_import_export(env, qtbot, tmp_path):
    shell = _open(qtbot, _session(env))
    card = _card(shell)
    card.set_enabled(True)
    msgs = []
    shell.analyze.toasts.show_toast = lambda t, m="", s="info", *a, **k: msgs.append((t, m, s))
    card.create_profile("A", 1.0, "µm", 10.0)
    card.create_profile("B", 500.0, "nm", 50.0)
    assert card.combo.count() == 2
    # duplicate name (case-insensitive) -> ProfileError text shown as-is
    assert card.create_profile("a", 1.0, "µm", 10.0) is None
    assert msgs[-1][2] == "danger" and msgs[-1][1]
    card.combo.setCurrentIndex(card.combo.findData(ProfileStore().find_by_name("A").id))
    assert card.rename_selected("A renamed")
    assert "A renamed" in card.combo.currentText()
    out = tmp_path / "export.json"
    assert card.export_to(out) == 2 and json.loads(out.read_text(encoding="utf-8"))
    assert card.delete_selected()
    assert card.combo.count() == 1
    rep = card.import_from(out)
    assert rep is not None and rep.added == 1 and card.combo.count() == 2


def test_new_profile_runs_auto_find_then_opens_window(env, qtbot):
    shell = _open(qtbot, _session(env))
    card, st = _card(shell), shell.state
    card.set_enabled(True)
    assert card.start_new_profile()
    qtbot.waitUntil(lambda: card.dialog is not None, timeout=TIMEOUT)
    dlg = card.dialog
    assert dlg.scan_rect is not None and dlg.image_size == (480, 360)
    dlg.name.setText("From dialog")
    dlg.length.setValue(10.0)
    dlg.pixels.setValue(50.0)
    dlg._check()
    assert card.store.find_by_name("From dialog") is not None
    assert "From dialog" in card.combo.currentText()
