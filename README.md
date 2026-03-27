# SEM Grain Analyzer

Automatic grain detection and measurement tool for Scanning Electron Microscope (SEM) images.

## Features
- Load SEM images (TIFF, PNG, JPG, BMP)
- Automatic scale bar detection
- Pixel-to-micron calibration
- Automatic grain detection using Otsu threshold + Watershed segmentation
- Grain overlay visualization
- Excel export with full statistics

## Building the Installer

### Windows
1. Install Python 3.10+ from https://python.org (check "Add Python to PATH")
2. Double-click `BUILD_WINDOWS.bat`
3. Wait ~5 minutes. Done.

Output: `dist/SEMGrainAnalyzer/SEMGrainAnalyzer.exe`

Optional: Install NSIS (https://nsis.sourceforge.io) before building to get a proper
`SEMGrainAnalyzer_Setup.exe` installer instead of a zip.

### macOS
1. Install Python 3.10+: `brew install python` or from https://python.org
2. Run: `bash BUILD_MAC.sh`
3. App bundle appears in `dist/SEMGrainAnalyzer.app`

## Usage

1. **Open Image** — Load your SEM image file
2. **Set Calibration** — Enter scale bar pixel width and real-world length (µm)
3. **Adjust Parameters** — Tune detection settings if needed
4. **Analyze** — Click "Analyze Grains" (F5)
5. **Export** — Save Excel report with statistics

## Detection Parameters

| Parameter | Description |
|---|---|
| Blur (σ) | Gaussian blur to reduce noise. Higher = smoother |
| Threshold offset | Adjust Otsu auto-threshold. Negative = detect more |
| Min grain area | Ignore regions smaller than this (removes debris) |
| Max grain area | Ignore regions larger than this (0 = no limit) |
| Watershed separation | Distance between grain centers for watershed splitting |
| Dark grains | Check if grains are darker than background |
| Use watershed | Separate touching/overlapping grains |
