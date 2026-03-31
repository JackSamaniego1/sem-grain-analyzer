# Getting Started

## Installation

### From Installer (Recommended)

1. Download the latest `GrainAnalyzer_Setup.exe` (Windows) or `GrainAnalyzer.dmg` (macOS) from the [Releases](https://github.com/JackSamaniego1/sem-grain-analyzer/releases) page
2. Run the installer and follow the on-screen instructions
3. Launch **Grain Analyzer** from the Start Menu or Desktop shortcut

The installer includes everything needed, including the AI model for the SAM detection mode. No additional downloads or configuration required.

### From Source

If you prefer to run from source:

```bash
git clone https://github.com/JackSamaniego1/sem-grain-analyzer.git
cd sem-grain-analyzer
pip install -r requirements.txt
python main.py
```

For the AI-assisted detection mode, you also need the SAM checkpoint:

```bash
mkdir models
curl -L -o models/sam_vit_b_01ec64.pth https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
```

## First Launch

When you open the application, you will see:

- **Left panel** — Settings and controls
- **Center** — Image canvas (empty until you load images)
- **Right panel** — Analysis results (hidden until you run analysis)

## Recommended Workflow

The typical procedure for analyzing SEM grain images:

1. **Open images** — Load one or more SEM micrographs
2. **Calibrate** — Set the scale bar so measurements are in real units (um or nm)
3. **Set scan area** — Exclude the SEM info bar at the bottom of the image (optional)
4. **Choose detection mode** — AI-assisted is the default and most accurate
5. **Analyze** — Click "Analyze ALL Images" and wait for results
6. **Review** — Switch to overlay view, inspect detected grains
7. **Edit** — Delete any false positives by clicking on them and pressing Delete
8. **Export** — Save results to an Excel report

Each of these steps is covered in detail in the following sections.
