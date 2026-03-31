# Image Enhancement

## CLAHE (Contrast Limited Adaptive Histogram Equalization)

### Purpose

SEM images often have uneven illumination or low contrast between grains and boundaries. CLAHE boosts local contrast so that subtle boundary grooves become more visible to the detection algorithms.

### How It Works

1. The image is divided into an **8x8 grid** of tiles
2. Each tile gets its own histogram equalization
3. A **clip limit** prevents over-amplification of noise:
   - Default: 2.0
   - Range: 0.5 - 8.0
   - Higher = more aggressive contrast boost
4. Bilinear interpolation blends tile boundaries for smooth results

### Implementation

```python
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
enhanced = clahe.apply(gray_image)
```

### When to Adjust

- **Increase to 3-4**: For very flat, low-contrast images where boundaries are barely visible
- **Decrease to 1.0**: For already high-contrast images where CLAHE introduces noise
- **Disable entirely**: If the image has good native contrast

CLAHE is used by both the **threshold-based** and **boundary-first** modes. The **AI-assisted (SAM)** mode does not use CLAHE — SAM handles contrast internally.

## Gaussian Blur

### Purpose

Reduces high-frequency noise before thresholding. Without blur, noise pixels can create false grain detections.

### Configuration

- **Sigma** (default: 1.5): Controls the blur radius
  - 0.0 = no blur
  - 1.0-2.0 = light smoothing (recommended)
  - 3.0+ = heavy smoothing (may blur small grains)

### Used In

- Threshold-based pipeline: applied before Otsu thresholding
- Boundary-first pipeline: uses bilateral filtering instead (preserves edges better)

## Bilateral Filtering

### Purpose

Used in the boundary-first pipeline as an alternative to Gaussian blur. Bilateral filtering smooths flat regions while **preserving edges**, which is critical for boundary detection.

### Adaptive Parameters

The boundary pipeline adapts bilateral filter strength based on image texture:

| Texture Level | Light Filter | Medium Filter | Heavy Filter |
|---------------|-------------|---------------|--------------|
| High texture (>3.5) | d=5, sC=30, sS=5 | d=9, sC=40, sS=9 | d=15, sC=50, sS=15 |
| Low texture | d=9, sC=40, sS=9 | d=15, sC=50, sS=15 | d=25, sC=60, sS=25 |

Where `d` = kernel diameter, `sC` = sigma color, `sS` = sigma space.

Three filtered versions (light, medium, heavy) are used for multi-scale boundary signal extraction.

## Auto-Crop

### Purpose

Many SEM images have bright white borders or info bars that would confuse detection. The auto-crop removes these automatically.

### Algorithm

1. Compute mean intensity per column and per row
2. Find the longest contiguous run of "dark" pixels (< 240 brightness) in each direction
3. If this dark region is smaller than 85% of the full image, crop to it
4. Add a 2-pixel margin to avoid cutting into the image content

This runs before any detection mode and is separate from the user's manual scan area selection.
