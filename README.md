# SEM Grain Analyzer

Automatic grain detection and measurement tool for Scanning Electron Microscope (SEM) images.

## Download & Install

1. Go to the [Releases](https://github.com/JackSamaniego1/sem-grain-analyzer/releases) page
2. Download **GrainAnalyzer_Setup.exe** (Windows) or **GrainAnalyzer.dmg** (macOS)
3. Run the installer — everything is included, no additional setup needed

The installer bundles all dependencies including the AI model. No Python installation required.

## Features

- **Three detection modes** — AI-assisted (SAM + ASTM E112), threshold-based, and boundary-first
- **Batch processing** — Analyze multiple images with a progress tracker
- **Scale bar calibration** — Click two points on the scale bar for real-world units
- **Scan area selection** — Exclude SEM info bars; border-touching grains auto-discarded
- **Interactive editing** — Click a grain and press Delete to remove false detections
- **Histograms** — Grain area and diameter distributions with adjustable bins
- **Excel export** — Reports with high-res images, charts, statistics, and per-grain data

## Quick Start

1. **Open images** — Click "Open SEM Images" or Ctrl+O (select multiple with Ctrl+click)
2. **Calibrate** — Click "Set Scale Bar", zoom into the scale bar, click both ends, enter the length
3. **Set scan area** — Click "Set Scan Area" and draw a rectangle to exclude the SEM legend bar (optional)
4. **Analyze** — Click "Analyze ALL Images" or press F5
5. **Review** — Switch to overlay view to inspect results, delete any false detections
6. **Export** — Click "Export to Excel" or Ctrl+E

## Detection Modes

| Mode | Best For | Speed |
|------|----------|-------|
| **AI-assisted (SAM + ASTM E112)** | Complex grain structures, highest accuracy | Slow (30-90s CPU) |
| **Threshold-based** | Bright grains on dark background (or vice versa) | Fast |
| **Boundary-first** | Dense mosaic grains with dark groove boundaries | Fast |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Open images |
| F5 | Analyze all images |
| Ctrl+F5 | Reanalyze current image |
| Ctrl+E | Export all to Excel |
| Ctrl+K | Set scale bar |
| Ctrl+R | Set scan area |
| Delete | Remove selected grain |
| Scroll wheel | Zoom |
| Alt+drag | Pan |

## Documentation

Full documentation is available in the [docs](docs/) folder, covering the user guide and technical details.

## Building from Source

If you prefer to run from source instead of using the installer:

```bash
git clone https://github.com/JackSamaniego1/sem-grain-analyzer.git
cd sem-grain-analyzer
pip install -r requirements.txt
python main.py
```

For the AI-assisted mode, download the SAM checkpoint (~375MB):

```bash
mkdir models
curl -L -o models/sam_vit_b_01ec64.pth https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
```

To build the installer yourself, double-click `BUILD_WINDOWS.bat` (Windows) or run `bash BUILD_MAC.sh` (macOS). Requires Python 3.10+.
