# SEM Grain Analyzer - Local Development Setup

## Project Overview
The SEM Grain Analyzer is a PyQt6-based desktop application for automatic grain detection and measurement in Scanning Electron Microscope (SEM) images. This document outlines the local development setup.

## Setup Status

### ✅ Completed
1. **Repository Cloned**: `https://github.com/JackSamaniego1/sem-grain-analyzer.git`
2. **Git Configured**: 
   - User: Saman Iego
   - Email: samaniegojack12@gmail.com
   - Remote: origin (GitHub)
3. **Python Environment**:
   - Python 3.11.9 detected
   - Virtual environment created: `.venv/`
   - Dependencies installing (in progress)
4. **.gitignore Updated**:
   - Added `.venv/`, `venv/`, `env/` for virtual environments
   - Added `.claude/` for Claude Code settings

### 📦 Dependencies Being Installed
Core dependencies include:
- **PyQt6** (6.6.0+) - Desktop UI framework
- **OpenCV** (4.8.0+) - Image processing
- **PyTorch** (2.0.0+) - Deep learning
- **Segment Anything** (1.0+) - AI-assisted grain detection
- **scikit-image** (0.22.0+) - Image analysis
- **openpyxl** (3.1.0+) - Excel export

### 🚀 Running the App Locally

Once dependencies are installed, run:
```bash
# Activate virtual environment
.venv\Scripts\Activate.ps1

# Run the application
python main.py
```

### 📁 Project Structure
```
.
├── main.py                 # Entry point
├── core/
│   ├── grain_detector.py  # Main detection algorithms
│   └── scale_bar.py       # Scale calibration
├── ui/
│   ├── main_window.py     # Main window
│   ├── image_canvas.py    # Image display
│   ├── settings_panel.py  # Detection settings
│   ├── results_panel.py   # Results visualization
│   ├── calibration_dialog.py    # Scale calibration UI
│   ├── scan_area_dialog.py      # Scan area selection
│   ├── analysis_progress_dialog.py # Progress tracking
│   └── theme.py           # Dark theme
├── utils/
│   └── excel_export.py    # Excel reporting
├── requirements.txt       # Python dependencies
└── BUILD_WINDOWS.bat      # Windows installer build script
```

### 🔧 Development Workflow

#### 1. Making Changes
- Edit Python files as needed
- Changes take effect on next run

#### 2. Testing Locally
```bash
# Run with your changes
.venv\Scripts\Activate.ps1
python main.py
```

#### 3. Committing Changes
```bash
# Stage changes
git add <file>

# Commit
git commit -m "Description of changes"

# Push to GitHub
git push origin main
```

#### 4. Building Installers
- **Windows**: `BUILD_WINDOWS.bat`
- **macOS**: `BUILD_MAC.sh`

### 📋 Key Features to Test
1. **Image Loading** - Open SEM images
2. **Scale Calibration** - Click scale bar endpoints
3. **Scan Area Selection** - Define analysis region
4. **Grain Detection** - Run AI/threshold/boundary detection
5. **Interactive Editing** - Select and delete grains
6. **Histograms** - View grain size distributions
7. **Excel Export** - Generate reports with images and data

### 🔑 Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| Ctrl+O | Open images |
| F5 | Analyze all images |
| Ctrl+E | Export to Excel |
| Ctrl+K | Set scale bar |
| Ctrl+R | Set scan area |
| Delete | Remove selected grain |

### ⚙️ AI Model Download
The AI-assisted detection mode requires downloading the SAM (Segment Anything Model):
- Size: ~375MB
- Location: `models/` directory
- Downloaded automatically on first AI-assisted use, or manually place checkpoint file

### 🐛 Troubleshooting

#### If dependencies fail to install:
1. Ensure Python 3.10+ is installed
2. Update pip: `.venv\Scripts\pip install --upgrade pip`
3. Clear pip cache: `.venv\Scripts\pip cache purge`
4. Retry: `.venv\Scripts\pip install -r requirements.txt`

#### If the app won't start:
1. Verify all dependencies: `.venv\Scripts\pip check`
2. Check Python version: `python --version`
3. Ensure PyQt6 is installed: `.venv\Scripts\pip show PyQt6`

### 📝 Next Steps
1. ✅ Wait for pip install to complete
2. ⏳ Test running the app: `python main.py`
3. ⏳ Test with sample SEM images
4. ⏳ Make any code improvements
5. ⏳ Commit and push changes to GitHub
6. ⏳ Create installer for distribution

---
**Last Updated**: September 23, 2026
**Repository**: https://github.com/JackSamaniego1/sem-grain-analyzer
