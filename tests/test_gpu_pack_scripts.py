"""Packaging-script tests for the optional GPU pack (build tooling only)."""
import importlib.util
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, ROOT)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(ROOT)
    return mod


@pytest.fixture(scope="module")
def pack_mod():
    return _load("create_gpu_pack_nsis")


@pytest.fixture(scope="module")
def make_mod():
    return _load("make_gpu_pack")


def _base_nsi_text():
    return open(os.path.join(ROOT, "create_nsis_script.py"), encoding="utf-8").read()


def _defines(text):
    return {m.group(1): m.group(2) for m in
            re.finditer(r'^!define (\w+) "(.*)"$', text, re.M)}


def test_every_define_line_has_space_between_name_and_value(pack_mod):
    text = pack_mod.generate("dist_gpu_pack", [])
    for line in text.splitlines():
        if line.startswith("!define "):
            assert re.match(r'^!define \w+( ".*")?$', line), line


def test_registry_names_match_base_installer(pack_mod):
    pack = pack_mod.generate("x", [])
    base = _base_nsi_text()
    assert _defines(pack)["INSTALL_REG_KEY"] == _defines(base)["INSTALL_REG_KEY"]
    assert _defines(pack)["APP_EXE"] == _defines(base)["APP_EXE"]
    for name in ("InstallLocation", "DisplayVersion"):
        assert f'"{name}"' in pack
        assert f'"{name}"' in base


def test_pack_refuses_running_app_before_copying(pack_mod):
    text = pack_mod.generate("x", ["a\\b.dll"])
    assert "Close SEM Grain Analyzer, then run this again." in text
    assert text.index("Close SEM Grain Analyzer") < text.index("Section \"GPU Pack\"")


def test_removed_files_become_delete_and_rmdir_lines(pack_mod):
    text = pack_mod.generate("x", ["_internal/torch-2/METADATA", "_internal/torch-2/RECORD"])
    assert 'Delete "$INSTDIR\\_internal\\torch-2\\METADATA"' in text
    assert text.index("Delete ") < text.index("RMDir ") < text.index("File /r")
    # deepest directory first
    assert text.index('RMDir "$INSTDIR\\_internal\\torch-2"') < text.index('RMDir "$INSTDIR\\_internal"')


def test_base_installer_reports_gpu_pack_replacement_without_deleting():
    base = _base_nsi_text()
    assert '"GpuPack"' in base
    assert 'DeleteRegValue HKLM "${INSTALL_REG_KEY}" "GpuPack"' in base
    assert "RMDir /r \"$INSTDIR\\" not in base.split('Section "Main Application"')[1].split("SectionEnd")[0]


def _write(root, rel, data):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def test_make_pack_added_changed_removed_identical(make_mod, tmp_path):
    cpu, gpu, out = (str(tmp_path / n) for n in ("cpu", "gpu", "out"))
    _write(cpu, "same.dll", b"same")
    _write(gpu, "same.dll", b"same")
    _write(cpu, "sub/changed.dll", b"cpu-version")
    _write(gpu, "sub/changed.dll", b"gpu-version!")
    _write(cpu, "torch-1+cpu.dist-info/METADATA", b"m")
    _write(gpu, "new/cudnn.dll", b"n")
    n, total, removed = make_mod.build_pack(cpu, gpu, out)
    assert n == 2 and removed == 1
    assert os.path.isfile(os.path.join(out, "sub", "changed.dll"))
    assert os.path.isfile(os.path.join(out, "new", "cudnn.dll"))
    assert not os.path.exists(os.path.join(out, "same.dll"))
    lst = open(make_mod.removed_list_path(out), encoding="utf-8").read().split()
    assert lst == [os.path.join("torch-1+cpu.dist-info", "METADATA")]


def test_long_path_prefix_on_windows(make_mod):
    p = make_mod._lp(os.getcwd())
    if sys.platform == "win32":
        assert p.startswith("\\\\?\\")
    else:
        assert p == os.getcwd()
