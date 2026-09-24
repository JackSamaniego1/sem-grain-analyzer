# Changelog

All notable changes to Grain Analyzer are documented here.

## [Unreleased] — 3.0.0

### Changed
- **Migrated PyQt6 → PySide6.** PyQt6 is GPL/commercial; PySide6 is LGPL v3
  and free to distribute with no licence purchase. `pyqtSignal`/`pyqtSlot`
  renamed to `Signal`/`Slot` throughout `main.py` and `ui/*.py`. No PyQt6
  package remains installed in `.venv`.
- `version.py` is now the single source of truth for the app version
  (`__version__ = "3.0.0-dev"`) and app name. `main.py`, `ui/main_window.py`,
  `ui/settings_panel.py`, `grain_analyzer.spec` (macOS bundle plist), and
  `create_nsis_script.py` all read from it instead of hard-coding "2.3".
- Build scripts (`BUILD_WINDOWS.bat`, `BUILD_MAC.sh`) and CI
  (`.github/workflows/build.yml`) now install the **CPU-only torch wheel**
  (`--index-url https://download.pytorch.org/whl/cpu`) to keep the
  installer smaller; a GPU is never required at runtime.
- Fixed `BUILD_WINDOWS.bat` referring to a stale `dist\SEMGrainAnalyzer` /
  `SEMGrainAnalyzer.exe` path; the spec has always built `GrainAnalyzer`.
- Build scripts and CI now fail loudly if `models/sam_vit_b_01ec64.pth` is
  missing after the download step, instead of silently shipping a broken
  installer.

### Added
- `core/offline_guard.py` — installed as the very first thing in `main.py`,
  before any PySide6/torch/cv2 import. Sets offline env vars
  (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `TORCH_HOME`, `NO_PROXY`) and
  monkeypatches the `socket` module so any attempt to reach a non-loopback
  address raises `OfflineViolation` and is logged locally. This is the
  enforcement mechanism behind HARD CONSTRAINT #1 in `CLAUDE.md`: the app
  must never touch the network and no data may leave the machine.
- `tests/test_offline.py` — AST-scans first-party source for forbidden
  network-capable imports/attributes/URL literals, and runtime-tests that
  the guard blocks real socket calls while leaving loopback IPC (used by
  Qt internals) working. Also asserts zero network attempts while running
  a full detection + Excel export.
- New dependencies: `xlsxwriter`, `python-pptx`, `qtawesome` (added to
  `requirements.txt`, build scripts, CI, and `grain_analyzer.spec`
  hiddenimports/datas) in preparation for the upgraded report renderers
  and icon set later in the v3 plan.
- `THIRD_PARTY_LICENSES.txt` — full licence + upstream URL listing for
  every runtime dependency, including the LGPL v3 dynamic-linking notice
  for PySide6/Qt. Bundled into the PyInstaller build and installed by
  NSIS.

### Removed
- Stray `.github/workflows/build.ymlresources/` directory (leftover from
  a past mistake).
- PyQt6, PyQt6-Qt6, PyQt6-sip uninstalled from `.venv`.

### Known issues
- `core/grain_detector.py` (~line 684) still contains one `https://` URL
  in an error message pointing the user to the SAM checkpoint download
  page. It is being removed by the detection-engineer agent in a
  concurrent task (DET-01/DET-03); `tests/test_offline.py` allowlists
  exactly that file with a `# TODO remove once DET-01 lands` comment so
  the offline test suite is green either way.
