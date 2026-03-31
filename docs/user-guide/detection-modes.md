# Detection Modes

The Grain Analyzer offers three detection modes, each suited to different types of SEM images.

## AI-Assisted (SAM + ASTM E112)

**Default mode. Most accurate for complex grain structures.**

This mode uses Meta's **Segment Anything Model (SAM)** for instance segmentation, refined with **ASTM E112** linear intercept analysis to validate and correct the results.

### How It Works

1. **SAM segmentation** — A dense grid of point prompts (32x32 on CPU, 64x64 on GPU) is fed to the SAM model, which generates candidate masks for every grain-like region in the image
2. **Mask filtering** — Candidates are filtered by size, predicted quality (IoU > 0.75), and overlap (< 50% with existing grains). Background regions (> 40% of image) are discarded
3. **ASTM E112 refinement** — 20 horizontal + 20 vertical test lines are drawn across the image. Boundary crossings are counted to calculate the mean intercept length. Any grain larger than 4x the expected area is re-split using watershed segmentation

### When to Use

- Complex grain structures where classical methods struggle
- Images where multiple grains are being merged into one
- When you want the highest accuracy and don't mind longer processing time

### Performance

- **CPU**: 30-90 seconds per image (auto-downscales images > 1024px)
- **GPU (CUDA)**: 5-15 seconds per image
- Progress is reported as SAM processes each batch of point prompts

### Tuning

This mode has no user-adjustable parameters. The Image Enhancement and Detection Parameters panels are hidden when SAM mode is selected, since SAM handles everything internally.

---

## Threshold-Based

**Best for images with distinct bright grains on a dark background (or vice versa).**

This mode uses classical Otsu and adaptive thresholding with optional watershed segmentation.

### How It Works

1. **CLAHE enhancement** — Optional contrast boost to improve grain/background separation
2. **Gaussian blur** — Reduces noise before thresholding
3. **Otsu thresholding** — Automatically finds the optimal intensity cutoff
4. **Adaptive thresholding** — Optional local neighborhood comparison for uneven illumination
5. **Morphological cleanup** — Close and open operations to fill holes and remove noise
6. **Watershed segmentation** — Separates touching grains using distance transform

### When to Use

- Grains are clearly brighter (or darker) than the background
- Good contrast between grains and substrate
- Sparse grains that don't fill the entire field of view

### Tuning Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Blur (sigma) | 1.5 | Gaussian smoothing to suppress noise |
| Threshold offset | -0.1 | Shifts the Otsu threshold up or down |
| Min area | 50 px² | Discard regions smaller than this |
| Max area | 0 (unlimited) | Discard regions larger than this |
| Watershed dist | 5 px | Minimum distance between grain centers |
| Dark grains | Off | Check if grains are darker than background |
| Use watershed | On | Split merged/touching grains |
| Adaptive threshold | On | Use local vs. global thresholding |

---

## Boundary-First

**Best for dense mosaic grain images separated by thin dark grooves.**

This mode uses multi-signal boundary detection with a two-pass architecture: contrast for initial segmentation, then texture orientation to split remaining merged grains.

### How It Works

**Pass 1 — Contrast Boundaries:**

The detector measures the image's texture characteristics to route between two sub-modes:

- **Groove mode** (strong morphological gradient): Uses multi-scale black top-hat transforms at 3 kernel sizes (9, 15, 21px) to extract dark groove lines between grains. The boundary map is 80% black top-hat, 10% DoG, 10% gradient.

- **Mosaic mode** (subtle boundaries): Combines 5 boundary signals — Difference of Gaussians (DoG), Laplacian of Gaussian (LoG), dark valley detection, multi-scale step change, and gradient magnitude. Weights adapt to image texture.

In both sub-modes, grain centers are found as local maxima of the "interior" signal (inverse of boundaries), then marker-controlled watershed grows grains from those seeds.

**Pass 2 — Texture Orientation Split (mosaic mode only):**

Regions larger than 2x the median grain area are suspected of containing merged grains. The structure tensor is computed on the raw image to detect where crystallographic hatching/stripe direction changes. A blended landscape (70% contrast + 30% orientation) drives a second watershed to split these oversized regions.

### When to Use

- Dense grain mosaics where grains fill the entire field of view
- Grains separated by thin dark grooves or subtle contrast changes
- SEM images of polished/etched metallographic samples

### Tuning Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Edge sensitivity | 1.5 | Higher detects more boundaries (may over-segment) |
| Threshold offset | -0.1 | Negative = more boundaries, positive = fewer |
| CLAHE strength | 2.0 | Contrast boost intensity (0.5-8.0) |
| Min area | 50 px² | Discard small regions |
| Max area | 0 (unlimited) | Discard oversized regions |
| Watershed dist | 5 px | Minimum distance between grain centers |
