# SAM + ASTM E112 Pipeline

The AI-assisted detection mode combines Meta's **Segment Anything Model (SAM)** with the **ASTM E112** linear intercept method for grain size validation and correction.

## Overview

```
Load SAM model → Downscale image (CPU) → Generate masks
  → Filter masks → Build label image → ASTM E112 intercept validation
  → Watershed split oversized regions → Final label image
```

## SAM Model

### Model Details

- **Architecture**: Vision Transformer (ViT-B)
- **Checkpoint**: `sam_vit_b_01ec64.pth` (~375 MB)
- **Input**: RGB image
- **Output**: Set of binary masks with quality scores

### Automatic Mask Generation

SAM is used in fully automatic mode via `SamAutomaticMaskGenerator`:

| Parameter | CPU Value | GPU Value | Purpose |
|-----------|-----------|-----------|---------|
| `points_per_side` | 32 | 64 | Grid density for point prompts |
| `pred_iou_thresh` | 0.80 | 0.80 | Minimum predicted IoU to keep mask |
| `stability_score_thresh` | 0.88 | 0.88 | Mask stability filter |
| `crop_n_layers` | 0 | 0 | No multi-crop (speed optimization) |
| `min_mask_region_area` | min_grain_size | min_grain_size | Minimum mask area in pixels |

With 32 points per side, SAM evaluates **1,024 point prompts** in batches of 64, generating candidate masks for each.

### CPU Optimization

For CPU inference, images larger than 1024px (longest dimension) are **downscaled** before SAM processing:

```python
scale = 1024 / max(height, width)
resized = cv2.resize(image, (new_w, new_h))
# Run SAM on smaller image...
# Upscale masks back with nearest-neighbor interpolation
```

This typically provides a 4-8x speedup with minimal quality loss.

### Progress Tracking

SAM's mask decoder `forward()` method is monkey-patched to count batch completions:

```
Progress: 10% → 50% (across all batches)
Each batch increments: 10 + 40 * (batch / total_batches)
```

This provides real-time progress updates instead of the UI appearing frozen.

## Mask Filtering

After SAM generates candidate masks, they are filtered:

1. **Sort by area** (descending) — smaller grains overwrite larger background regions
2. **Size filter** — Skip masks below `min_grain_size_px` or above `max_grain_size_px`
3. **Background filter** — Skip masks covering > 40% of the image
4. **Quality filter** — Skip masks with `predicted_iou < 0.75`
5. **Overlap filter** — Skip masks where > 50% overlaps an existing grain

Accepted masks are assigned sequential grain IDs in the label image.

## ASTM E112 Linear Intercept Method

### Theory

ASTM E112 defines the **Heyn linear intercept procedure** for measuring grain size:

1. Draw straight test lines across the microstructure
2. Count the number of grain boundary intersections
3. Calculate the **mean intercept length**: `l = total_line_length / total_intersections`
4. The mean intercept length relates to the expected grain area

### Implementation

The intercept analysis uses **40 test lines** (20 horizontal + 20 vertical), evenly spaced across the image:

```python
# Horizontal lines
for i in range(20):
    row = height * (i + 1) / 21
    line = labels[row, :]
    crossings = count where diff(line) != 0

# Vertical lines
for i in range(20):
    col = width * (i + 1) / 21
    line = labels[:, col]
    crossings = count where diff(line) != 0
```

The mean intercept length in pixels:

```
mean_intercept_px = total_line_length / total_intersections
```

Expected grain area (circular approximation):

```
expected_area = pi * (mean_intercept_px / 2)²
```

### Refinement

Any grain region larger than **4x the expected area** is flagged as likely containing merged grains. These oversized regions are re-split using:

1. Distance transform within the region mask
2. `peak_local_max` to find sub-grain centers (min distance = intercept_length / 3)
3. Watershed segmentation on the negative distance transform
4. Accept the split only if all sub-regions meet the minimum size threshold

This corrects cases where SAM merged adjacent grains into a single mask.

## Device Selection

The pipeline automatically selects the best available device:

```python
device = "cuda" if torch.cuda.is_available() else "cpu"
```

- **CUDA GPU**: Full-resolution processing, 64-point grid, 5-15 seconds
- **CPU**: Downscaled to 1024px, 32-point grid, 30-90 seconds

## Checkpoint Location

The SAM checkpoint is searched in these locations (in order):

1. `{MEIPASS}/models/sam_vit_b_01ec64.pth` (PyInstaller bundle)
2. `{project_dir}/models/sam_vit_b_01ec64.pth` (development)
3. `{project_dir}/sam_vit_b_01ec64.pth` (fallback)
4. `{core_dir}/sam_vit_b_01ec64.pth` (fallback)

If not found, a `FileNotFoundError` is raised with download instructions.
