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


# ---- GPU option page in the main installer (decision D-32 follow-up) --------

@pytest.fixture(scope="module")
def main_nsi():
    return _load("create_nsis_script").nsis_content


def test_importing_main_script_does_not_write_installer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _load("create_nsis_script")
    assert not (tmp_path / "installer.nsi").exists()


def test_main_defines_well_formed(main_nsi):
    for line in main_nsi.splitlines():
        if line.startswith("!define "):
            assert re.match(r'^!define \w+( ".*")?$', line), line


def test_main_installer_has_gpu_page_before_install_page(main_nsi):
    assert "Page custom GpuPageCreate GpuPageLeave" in main_nsi
    assert (main_nsi.index("MUI_PAGE_DIRECTORY")
            < main_nsi.index("Page custom GpuPageCreate")
            < main_nsi.index("MUI_PAGE_INSTFILES"))
    assert "Install the NVIDIA GPU option (much faster AI-assisted detection)" in main_nsi
    assert "GPU option file not found next to this installer." in main_nsi
    assert "GPU option found next to this installer" in main_nsi
    assert "No NVIDIA graphics card was found on this PC. The app will use the CPU." in main_nsi
    assert "nsDialogs::SelectFileDialog" in main_nsi
    assert "It is a separate file, GrainAnalyzer_GPU_Pack.exe, supplied with this installer." in main_nsi


def test_main_installer_nvidia_detection_and_silent_switch(main_nsi):
    assert "nvcuda.dll" in main_nsi and "nvml.dll" in main_nsi
    assert '"/GPU="' in main_nsi and "IfSilent" in main_nsi
    assert r'$GpuDir\GrainAnalyzer_GPU_Pack.exe' in main_nsi
    assert r'$EXEDIR\GrainAnalyzer_GPU_Pack.exe' not in main_nsi
    assert "/GPU=1" in _base_nsi_text()  # documented in the script header


def test_pack_runs_after_registry_keys_and_reports_failure(main_nsi):
    sect = main_nsi.split('Section "Main Application"')[1].split("SectionEnd")[0]
    assert sect.index('"UninstallString"') < sect.index("ExecWait")
    assert "ExecWait '\"$GpuPackPath\" /S' $0" in sect
    assert "The GPU option could not be installed. The app is installed and will use the CPU." in sect
    assert "You can run GrainAnalyzer_GPU_Pack.exe later." in sect
    # the "pack replaced" notice is suppressed when re-applying in this run
    assert "StrCmp $GpuApply \"1\" nogpupack" in sect


def test_pack_file_sanity_checks_present(main_nsi):
    for needle in ('"GrainAnalyzer_GPU_Pack.exe" 0 gcf_end', "IntCmp $4 50000", 'StrCmp $8 "MZ"'):
        assert needle in main_nsi


def test_pack_installer_exit_codes_and_sd_defaults(pack_mod):
    text = pack_mod.generate("x", [])
    for code in ("SetErrorLevel 3", "SetErrorLevel 4", "SetErrorLevel 5"):
        assert code in text
    # every Abort is preceded by an explicit non-zero error level
    lines = [l.strip() for l in text.splitlines()]
    for i, l in enumerate(lines):
        if l == "Abort" and "MUI_PAGE" not in lines[i - 2]:
            assert any(x.startswith("SetErrorLevel") for x in lines[max(0, i - 3):i]), lines[i - 3:i]
    for l in lines:
        if l.startswith("MessageBox"):
            assert "/SD " in l, l


def test_no_network_strings_in_either_script(main_nsi, pack_mod):
    texts = [main_nsi, pack_mod.generate("x", [])]
    texts += [open(os.path.join(ROOT, f), encoding="utf-8").read()
              for f in ("create_nsis_script.py", "create_gpu_pack_nsis.py")]
    for t in texts:
        low = t.lower()
        for bad in ("http", "www.", "inetc", "nsisdl", "ftp:", "inetload"):
            assert bad not in low, bad


def test_exedir_trailing_backslash_stripped_and_error_levels(main_nsi):
    assert 'StrCpy $GpuDir "$GpuDir" -1' in main_nsi
    assert "SetErrorLevel 10" in main_nsi and "SetErrorLevel 11" in main_nsi
    assert "GpuCheckClick" in main_nsi  # tick survives Back -> Next


def test_makensis_compiles_both_scripts(main_nsi, pack_mod):
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which("makensis")
    if not exe:
        for c in (r"C:\Program Files (x86)\NSIS\makensis.exe", r"C:\Program Files\NSIS\makensis.exe"):
            if os.path.isfile(c):
                exe = c
    if not exe:
        pytest.skip("makensis not installed")
    work = tempfile.mkdtemp(prefix="nsis_test_")
    try:
        for d in ("dist\\GrainAnalyzer", "dist_gpu_pack", "resources"):
            os.makedirs(os.path.join(work, d))
        for rel in ("dist\\GrainAnalyzer\\GrainAnalyzer.exe", "dist_gpu_pack\\stub.dll",
                    "THIRD_PARTY_LICENSES.txt", "LICENSE.txt"):
            with open(os.path.join(work, rel), "wb") as f:
                f.write(b"stub")
        shutil.copy(os.path.join(ROOT, "resources", "icon.ico"), os.path.join(work, "resources", "icon.ico"))
        scripts = {"main.nsi": main_nsi, "pack.nsi": pack_mod.generate("dist_gpu_pack", [])}
        for name, text in scripts.items():
            with open(os.path.join(work, name), "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run([exe, "/V1", name], cwd=work, capture_output=True, text=True, timeout=300)
            assert r.returncode == 0, (name, r.stdout, r.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)
