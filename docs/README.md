# SEM Grain Analyzer

**SEM Grain Analyzer** is a desktop application for automated grain detection, measurement, and statistical analysis of Scanning Electron Microscope (SEM) images.

## Key Features

- **Three detection modes** — AI-assisted (SAM + ASTM E112), threshold-based, and boundary-first
- **Batch processing** — Analyze multiple images in one session with a progress tracker
- **Scale bar calibration** — Click two points on the scale bar to convert pixels to real-world units
- **Scan area selection** — Exclude SEM legend bars and other non-grain regions
- **Interactive grain editing** — Click to select grains, press Delete to remove false detections
- **Comprehensive statistics** — Area, diameter, circularity, aspect ratio, eccentricity, and more
- **Histogram visualization** — Grain area and diameter distributions with adjustable bin counts
- **Excel export** — Professional reports with images, histograms, statistics, and per-grain data

## Typical Workflow

1. Open one or more SEM images
2. Set the scale bar calibration (optional but recommended)
3. Define a scan area to exclude legends (optional)
4. Select a detection mode
5. Click **Analyze ALL Images**
6. Review results, delete false detections
7. Export to Excel

## System Requirements

- **OS**: Windows 10/11, macOS 10.15+
- **Python**: 3.10+ (for running from source)
- **RAM**: 4 GB minimum, 8 GB recommended (16 GB for AI-assisted mode)
- **Disk**: ~2 GB for full installation with SAM model
- **GPU**: Optional — CUDA-capable GPU accelerates AI-assisted mode

## Quick Install

Download the latest installer from the [GitHub Releases](https://github.com/JackSamaniego1/sem-grain-analyzer/releases) page, or build from source using `BUILD_WINDOWS.bat` (Windows) or `BUILD_MAC.sh` (macOS).
