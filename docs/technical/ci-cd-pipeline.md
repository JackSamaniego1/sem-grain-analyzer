# CI/CD Pipeline

## GitHub Actions Workflow

The project uses GitHub Actions for automated builds. The workflow is defined in `.github/workflows/build.yml`.

## Trigger

The workflow runs on:

- **Push to `main` branch** — Automatic build on every commit
- **Manual trigger** — `workflow_dispatch` for on-demand builds

## Build Matrix

Two parallel jobs run simultaneously:

### Windows Build (`windows-latest`)

1. **Checkout** — Clone the repository
2. **Setup Python 3.11** — Install Python runtime
3. **Install dependencies** — All pip packages including PyTorch
4. **Generate icon** — Create `resources/icon.ico` programmatically (256x256 RGBA, blue microscope theme with crosshairs)
5. **PyInstaller build** — `pyinstaller grain_analyzer.spec --clean --noconfirm`
6. **Install NSIS** — Package manager for the installer
7. **Generate NSIS script** — Run `create_nsis_script.py`
8. **Build installer** — `makensis.exe installer.nsi`
9. **Upload artifact** — `GrainAnalyzer_Setup.exe` (30-day retention)

### macOS Build (`macos-latest`)

1. **Checkout** — Clone the repository
2. **Setup Python 3.11** — Install Python runtime
3. **Install dependencies** — All pip packages
4. **Generate icon** — Create `resources/icon.icns` (512x512 RGBA)
5. **PyInstaller build** — Build the `.app` bundle
6. **Create DMG** — `hdiutil create` packages the app into a disk image
7. **Upload artifact** — `GrainAnalyzer.dmg` (30-day retention)

## NSIS Installer Configuration

The Windows installer is built with NSIS (Nullsoft Scriptable Install System):

- **Install location**: `C:\Program Files\GrainAnalyzer\`
- **Registry entries**: Add/Remove Programs integration
- **Shortcuts**: Desktop and Start Menu
- **Uninstaller**: Full cleanup of files, shortcuts, and registry
- **License**: Shows `LICENSE.txt` during installation
- **UI**: Modern UI 2 theme with custom colors

## Artifacts

Build artifacts are available for download from the GitHub Actions page for 30 days after each build.

## Notes

- The CI/CD pipeline should be updated to include the SAM model download step and the new torch/torchvision dependencies
- Artifact storage quotas may limit the number of retained builds due to the large file sizes (~1.5-2 GB per platform)
- Consider using GitHub Releases instead of artifact storage for distribution
