# Architecture Overview

## Project Structure

```
sem-grain-analyzer/
├── main.py                    # Application entry point, splash screen
├── core/
│   ├── grain_detector.py      # Detection engine (3 modes), measurement, overlay
│   └── scale_bar.py           # Auto scale bar detection (OCR-based)
├── ui/
│   ├── main_window.py         # Main window, menus, tabs, analysis threading
│   ├── settings_panel.py      # Left panel controls and parameter widgets
│   ├── results_panel.py       # Right panel statistics, histograms, grain table
│   ├── image_canvas.py        # Zoomable/pannable image display with grain selection
│   ├── calibration_dialog.py  # 2-point scale bar calibration dialog
│   ├── scan_area_dialog.py    # Rectangle-based scan area selection dialog
│   ├── analysis_progress_dialog.py  # Multi-image progress tracker
│   └── theme.py               # Dark theme stylesheet (Fusion-based)
├── utils/
│   └── excel_export.py        # Excel report generation with charts and images
├── models/
│   └── sam_vit_b_01ec64.pth   # SAM model checkpoint (not in git, ~375MB)
├── grain_analyzer.spec        # PyInstaller build specification
├── BUILD_WINDOWS.bat          # Windows build script
├── BUILD_MAC.sh               # macOS build script
├── create_nsis_script.py      # NSIS installer script generator
└── requirements.txt           # Python dependencies
```

## Component Diagram

```
┌─────────────────────────────────────────────────────┐
│                    MainWindow                        │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ Settings  │  │  ImageCanvas  │  │ ResultsPanel  │ │
│  │  Panel    │  │  (per tab)    │  │               │ │
│  │           │  │               │  │ - Stats cards │ │
│  │ - Params  │  │ - Zoom/Pan    │  │ - Area hist   │ │
│  │ - Buttons │  │ - Grain sel.  │  │ - Diam hist   │ │
│  │ - Mode    │  │ - View modes  │  │ - Grain table │ │
│  └─────┬─────┘  └───────┬──────┘  └───────────────┘ │
│        │                │                             │
│        ▼                ▼                             │
│  ┌──────────────────────────────┐                    │
│  │      AnalysisWorker          │  (QThread)         │
│  │  - Crop to scan area         │                    │
│  │  - Call GrainDetector        │                    │
│  │  - Remove border grains      │                    │
│  │  - Remap coordinates         │                    │
│  └──────────────┬───────────────┘                    │
└─────────────────┼───────────────────────────────────┘
                  ▼
┌─────────────────────────────────┐
│        GrainDetector            │
│  ┌───────────┐ ┌─────────────┐ │
│  │ SAM+ASTM  │ │ Threshold   │ │
│  │ Pipeline  │ │ Pipeline    │ │
│  └───────────┘ └─────────────┘ │
│  ┌───────────┐ ┌─────────────┐ │
│  │ Boundary  │ │ Measurement │ │
│  │ Pipeline  │ │ + Overlay   │ │
│  └───────────┘ └─────────────┘ │
└─────────────────────────────────┘
```

## Threading Model

Analysis runs on a background **QThread** to keep the UI responsive:

1. `MainWindow` creates an `AnalysisWorker` and moves it to a new `QThread`
2. The worker emits `progress(pct, msg)` signals that update the UI
3. On completion, `finished(AnalysisResult)` delivers results back to the main thread
4. For multi-image batches, images are processed **sequentially** (one thread at a time)
5. A cancel flag stops the queue between images

## Data Flow

```
User clicks "Analyze"
  → AnalysisWorker.run()
    → Crop to scan area (if set)
    → GrainDetector.analyze()
      → Preprocess (grayscale, auto-crop)
      → Selected pipeline (SAM / threshold / boundary)
      → _measure_grains() → List[GrainResult]
      → _compute_statistics() → AnalysisResult
      → _draw_overlay() → overlay image
    → Remove border-touching grains
    → Remap coordinates to full image
  → finished signal → MainWindow._on_done()
    → Update tab, results panel, status bar
```

## Key Data Structures

### DetectionParams

Configuration dataclass passed from the UI to the detector:

```python
@dataclass
class DetectionParams:
    blur_sigma: float = 1.5
    threshold_offset: float = -0.1
    min_grain_size_px: int = 50
    max_grain_size_px: int = 0
    watershed_min_dist: int = 5
    dark_grains: bool = False
    use_watershed: bool = True
    edge_sensitivity: float = 1.5
    use_adaptive: bool = True
    use_clahe: bool = True
    clahe_clip_limit: float = 2.0
    detection_mode: str = "auto"
    # ... additional fields
```

### GrainResult

Per-grain measurements:

```python
@dataclass
class GrainResult:
    grain_id, area_px, area_um2, perimeter_px, perimeter_um,
    equivalent_diameter_px, equivalent_diameter_um,
    major_axis_um, minor_axis_um, aspect_ratio,
    circularity, eccentricity, centroid_x, centroid_y, bbox
```

### AnalysisResult

Complete analysis output:

```python
@dataclass
class AnalysisResult:
    grains: List[GrainResult]
    grain_count: int
    label_image: np.ndarray      # Integer label map
    overlay_image: np.ndarray    # BGR overlay visualization
    binary_image: np.ndarray     # Binary segmentation mask
    px_per_um: float
    has_calibration: bool
    # Statistical summaries...
```
