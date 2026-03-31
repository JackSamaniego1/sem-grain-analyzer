# Building from Source

## Prerequisites

- **Python 3.10+** — Download from [python.org](https://python.org)
- **Internet connection** — For downloading dependencies and the SAM model

## Windows Build

### Automated Build

Double-click `BUILD_WINDOWS.bat` or run it from a command prompt. The script performs 7 steps automatically:

1. **Create virtual environment** — Isolated Python environment in `build_env/`
2. **Install dependencies** — All Python packages including PyTorch and SAM
3. **Download SAM model** — Fetches `sam_vit_b_01ec64.pth` (~375MB) into `models/`
4. **Create icon** — Generates the application icon programmatically
5. **Build executable** — Runs PyInstaller to create the bundled application
6. **Package** — Creates an NSIS installer (if NSIS is installed) or a ZIP file
7. **Done** — Output in `dist/GrainAnalyzer/`

### Output

- **With NSIS**: `GrainAnalyzer_Setup.exe` installer
- **Without NSIS**: `SEMGrainAnalyzer_Windows.zip` portable package

### Optional: Install NSIS

For a proper Windows installer with Start Menu shortcuts and uninstaller:

1. Download NSIS from [nsis.sourceforge.io](https://nsis.sourceforge.io)
2. Install it (ensure `makensis` is on PATH)
3. Re-run the build script

## macOS Build

Run in Terminal:

```bash
bash BUILD_MAC.sh
```

The script:

1. Creates a virtual environment
2. Installs all dependencies
3. Downloads the SAM checkpoint
4. Creates the application icon
5. Builds the `.app` bundle via PyInstaller

To create a distributable DMG:

```bash
hdiutil create -volname 'SEM Grain Analyzer' \
  -srcfolder dist/SEMGrainAnalyzer.app \
  -ov -format UDZO SEMGrainAnalyzer.dmg
```

## PyInstaller Spec File

The `grain_analyzer.spec` file configures the build:

### Bundled Data

- `skimage`, `scipy`, `cv2` data files
- PyTorch data files and dynamic libraries
- SAM model checkpoint (`models/sam_vit_b_01ec64.pth`)

### Hidden Imports

PyInstaller cannot auto-detect all dynamic imports. The spec explicitly includes:

- All `torch` submodules
- All `torchvision` submodules
- All `segment_anything` submodules
- scikit-image internal modules
- scipy internal modules
- Application modules (core, ui, utils)

### Excluded Packages

To reduce bundle size, these unused packages are excluded:

- napari, matplotlib, IPython, tkinter
- PySide2, PySide6, PyQt5 (only PyQt6 is used)
- pandas

## Dependencies

Full dependency list (`requirements.txt`):

| Package | Version | Purpose |
|---------|---------|---------|
| PyQt6 | >= 6.6.0 | GUI framework |
| opencv-python | >= 4.8.0 | Image processing |
| scikit-image | >= 0.22.0 | Segmentation, measurement |
| scipy | >= 1.11.0 | Distance transforms, labeling |
| numpy | >= 1.24.0 | Array operations |
| openpyxl | >= 3.1.0 | Excel export |
| Pillow | >= 10.0.0 | Image I/O |
| torch | >= 2.0.0 | SAM model inference |
| torchvision | >= 0.15.0 | SAM dependency |
| segment-anything | >= 1.0 | SAM model |

## Installer Size

The final installer is approximately **1.5 - 2 GB** due to:

- PyTorch runtime (~500 MB)
- SAM checkpoint (~375 MB)
- OpenCV, scikit-image, scipy, PyQt6 (~400 MB)
- Python runtime and other libraries (~200 MB)

## Troubleshooting

### Build fails at PyInstaller step

- Ensure all dependencies installed correctly in the virtual environment
- Check for conflicting package versions
- Try `pip install --force-reinstall pyinstaller`

### SAM model download fails

- Check internet connection
- Manually download from: `https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth`
- Place in the `models/` directory

### Missing DLLs on Windows

- Install the latest [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
- Ensure Python was installed with the "Add to PATH" option
