"""UPDATE 4 item 10b (UI): "AI-Assisted (GPU)" and "AI-Assisted (CPU)".

core.ai_device.ai_devices is mocked (no torch import, no GPU needed).
"""
from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core.ai_device as aid  # noqa: E402
from core.ai_device import AiDeviceInfo  # noqa: E402

NO_GPU_PACK = AiDeviceInfo(torch_available=True, cpu_available=True,
                           gpu_reason=aid.REASON_NO_GPU_BUILD)
NO_CARD = AiDeviceInfo(torch_available=True, cpu_available=True, torch_version="2",
                       cuda_version="12.1", gpu_reason=aid.REASON_NO_CARD)
GPU = AiDeviceInfo(torch_available=True, cpu_available=True, gpu_available=True,
                   gpu_name="NVIDIA RTX A2000", vram_total_mb=6144, gpu_reason="")
NO_TORCH = AiDeviceInfo()


@pytest.fixture
def modes(monkeypatch, qapp, tmp_path):
    import ui.detection_modes as dm
    import ui.pages.analyze_page as ap
    from ui.ai_probe import reset_device_probe
    monkeypatch.setattr(dm, "sam_model_available", lambda: True)
    monkeypatch.setattr(ap, "sam_model_available", lambda: True)
    # a probe that is never answered by the real (slow) torch import
    monkeypatch.setattr(aid, "ai_devices", lambda refresh=False: NO_GPU_PACK)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    reset_device_probe()
    yield ap
    reset_device_probe()


def _panel(ap, qtbot, pref=""):
    from ui.ai_probe import AiDeviceProbe
    probe = AiDeviceProbe()
    p = ap.ParamPanel(probe=probe)
    qtbot.addWidget(p)
    p.set_device_preference(pref)
    p.show()
    return p, probe


def _no_link(text: str) -> None:
    assert "http" not in text and "www." not in text and "download" not in text.lower()


def test_two_ai_entries_other_modes_unchanged(modes, qtbot):
    import ui.detection_modes as dm
    p, _ = _panel(modes, qtbot)
    assert [m[0] for m in dm.MODES] == ["sam_astm", "boundary", "threshold"]
    assert list(p.mode_cards) == ["sam_astm", "boundary", "threshold"]
    titles = {d: c.accessibleName() for d, c in p.device_cards.items()}
    assert titles == {"gpu": "AI-Assisted (GPU)", "cpu": "AI-Assisted (CPU)"}
    assert p.mode() == "sam_astm"


def test_gpu_greyed_out_with_plain_reason_and_cpu_used(modes, qtbot):
    p, probe = _panel(modes, qtbot)
    gpu, cpu = p.device_cards["gpu"], p.device_cards["cpu"]
    assert probe.info() is None and gpu.badge.text() == "Checking…"   # still probing
    probe.set_info(NO_GPU_PACK)
    assert not gpu.isEnabled() and cpu.isEnabled()
    tip = gpu.toolTip()
    assert aid.REASON_NO_GPU_BUILD in tip and "installer" in tip and "GPU pack" in tip
    _no_link(tip)
    assert p.device() == "cpu" and p.get_params().sam_device == "cpu"
    assert cpu.is_selected() and not gpu.is_selected()
    p.choose_device("gpu")                                        # greyed out: ignored
    assert p.device() == "cpu"
    probe.set_info(NO_CARD)
    assert gpu.toolTip() == aid.REASON_NO_CARD                    # no pack hint: no card


def test_fresh_install_defaults_to_gpu_when_usable(modes, qtbot):
    p, probe = _panel(modes, qtbot)
    probe.set_info(GPU)
    gpu = p.device_cards["gpu"]
    assert gpu.isEnabled() and gpu.is_selected() and gpu.badge.text() == "Default"
    assert "RTX A2000" in gpu.toolTip()
    params = p.get_params()
    assert params.detection_mode == "sam_astm" and params.sam_device == "gpu"
    got = []
    p.device_changed.connect(got.append)
    p.set_mode("boundary", emit=True)
    p.choose_device("cpu")                                        # also selects AI mode
    assert got == ["cpu"] and p.mode() == "sam_astm" and p.get_params().sam_device == "cpu"
    assert p.device_cards["cpu"].is_selected() and not gpu.is_selected()


def test_saved_gpu_on_pc_without_gpu_falls_back_to_cpu_quietly(modes, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    popups = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: popups.append(a))
    p, probe = _panel(modes, qtbot, pref="gpu")
    # before the answer: GPU shown, detector told "auto" (never fails)
    assert p.device() == "gpu" and p.sam_device() == "auto"
    probe.set_info(NO_CARD)
    assert p.device() == "cpu" and p.get_params().sam_device == "cpu"
    assert p.device_preference() == "gpu"                         # kept for a GPU PC
    assert not popups


def test_ai_component_missing_disables_both_and_uses_boundary(modes, qtbot):
    p, probe = _panel(modes, qtbot)
    probe.set_info(NO_TORCH)
    assert not any(c.isEnabled() for c in p.device_cards.values())
    assert "reinstall" in p.device_cards["cpu"].toolTip().lower()
    assert p.mode() == "boundary"


def test_old_saved_mode_names_still_load(modes, qtbot):
    from ui.app_state import params_from_dict
    p, probe = _panel(modes, qtbot, pref="cpu")
    probe.set_info(GPU)
    for old in ({"detection_mode": "sam_astm"}, {"detection_mode": "auto"},
                {"detection_mode": "sam_astm", "sam_device": "cuda"}, {}):
        p.set_params(params_from_dict(old))
        assert p.mode() == "sam_astm"
        assert p.get_params().sam_device == "cpu"                 # the PC's choice wins


def test_preference_persists_in_settings(modes, qtbot):
    from ui.app_state import AppState
    st = AppState()
    assert st.ai_device_preference == ""
    st.set_ai_device_preference("gpu")
    assert AppState().ai_device_preference == "gpu"
    st.ui_state["ai_device"] = "cuda"                             # odd saved value
    assert st.ai_device_preference == "gpu"


def test_probe_runs_in_background_and_updates_the_entry(modes, qtbot, monkeypatch):
    from ui.ai_probe import device_probe
    threads = []

    def slow(refresh=False):
        threads.append(threading.get_ident())
        time.sleep(0.4)                                           # "importing torch"
        return GPU
    monkeypatch.setattr(aid, "ai_devices", slow)
    probe = device_probe()
    t0 = time.perf_counter()
    assert probe.start()
    assert time.perf_counter() - t0 < 0.2                         # GUI thread not blocked
    assert not probe.start()                                      # only once at a time
    with qtbot.waitSignal(probe.ready, timeout=5000):
        pass
    assert threads and threads[0] != threading.get_ident()
    assert probe.info() is GPU


def test_device_choice_reaches_the_detection_worker(modes, qtbot, monkeypatch, tmp_path):
    from data.models import AppSettings
    from data.settings import save_settings
    from tests.ui_shell_helpers import make_session
    from ui.ai_probe import device_probe
    from ui.app_shell import AppShell
    from ui.app_state import AppState
    root = tmp_path / "ws"
    save_settings(AppSettings(workspace_root=str(root), operator="Tester", theme="dark"))
    shell = AppShell(AppState(), probe_device=False)
    qtbot.addWidget(shell)
    shell.open_session(make_session(root, 1), prefer="analyze")
    qtbot.waitUntil(lambda: shell.state.session is not None
                    and not shell.state.is_loading(), timeout=15000)
    a = shell.analyze
    device_probe().set_info(GPU)
    jobs = []
    monkeypatch.setattr(a, "check_setup", lambda imgs: True)
    monkeypatch.setattr(a.queue, "start", lambda js: jobs.extend(js))
    a.params.choose_device("gpu")
    a.analyze_all()
    assert jobs and all(j.params.detection_mode == "sam_astm" and j.params.sam_device == "gpu"
                        for j in jobs)
    assert shell.state.ai_device_preference == "gpu"
    assert shell.state.session.params.get("sam_device") == "gpu"
    shell.close()


def test_fallback_note_shown_where_analysis_messages_are(modes, qtbot, monkeypatch):
    from ui.app_state import AppState
    from ui.pages.analyze_page import AnalyzePage
    toasts = []
    fake_toasts = SimpleNamespace(show_toast=lambda *a, **k: toasts.append(a))
    st = AppState()
    page = AnalyzePage(st, toasts=fake_toasts)
    qtbot.addWidget(page)
    monkeypatch.setattr(st, "set_result", lambda uid, raw: None)
    page._device_notes = []
    page._batch_uids, page._batch_total = [1], 1
    raw = SimpleNamespace(ai_device_note=aid.NOTE_OOM_FALLBACK, ai_device="cpu")
    page._on_job_finished(1, raw)
    assert aid.NOTE_OOM_FALLBACK in page.run_sub.text()
    page.run_sub.setText("1 image · 10 grains · saved automatically")
    page._report_device_notes()
    assert "finished on the CPU" in page.run_sub.text()
    assert any("processed on the CPU" in t[0] and aid.NOTE_OOM_FALLBACK in t[1] for t in toasts)
