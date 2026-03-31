# Detection Algorithms

This page describes the classical detection algorithms used in the Threshold-Based and Boundary-First modes. For the AI-assisted mode, see [SAM + ASTM E112 Pipeline](sam-astm-pipeline.md).

## Auto-Crop

Before any detection runs, the image is checked for bright border regions (e.g., SEM info bars):

1. Compute column and row intensity means
2. Find the longest contiguous "dark" run (< 240 brightness) in each axis
3. If the dark region is < 85% of the total image, crop to that region
4. A 2px margin is preserved around the crop

This is separate from the user's scan area — it handles bright borders automatically.

## Threshold-Based Pipeline

### Step-by-Step

1. **CLAHE Enhancement** (optional)
   - Contrast Limited Adaptive Histogram Equalization
   - 8x8 tile grid, configurable clip limit (default 2.0)

2. **Gaussian Blur**
   - Smoothing with configurable sigma (default 1.5)
   - Reduces noise before thresholding

3. **Otsu Thresholding**
   - Automatic optimal threshold calculation
   - User-adjustable offset shifts the threshold up/down
   - "Dark grains" checkbox inverts the comparison

4. **Adaptive Thresholding** (optional)
   - Block size: `max(11, image_size / 15)`, always odd
   - Combined with Otsu via bitwise OR for robustness
   - Handles uneven illumination across the image

5. **Morphological Cleanup**
   - Close operation: fills small holes within grains
   - Open operation: removes small noise outside grains
   - Elliptical structuring elements

6. **Small Object Removal**
   - Regions below `min_grain_size_px` are discarded

7. **Watershed Segmentation** (optional)
   - Distance transform of the binary mask
   - Peak local maxima find grain centers
   - Watershed grows regions from those seeds
   - Separates touching/overlapping grains

## Boundary-First Pipeline

### Architecture

The boundary-first mode uses a two-pass approach:

```
Pass 1: Contrast-based boundary detection → watershed
Pass 2: Texture orientation analysis → split oversized regions
```

### Texture Measurement

Before detection, the image's texture ratio is measured:

```
texture_ratio = fine_gradient_mean / coarse_gradient_mean
```

- Fine: Sobel on Gaussian(sigma=1.0)
- Coarse: Sobel on Gaussian(sigma=4.0)

This routes to **groove mode** (high morphological gradient) or **mosaic mode** (lower gradient).

### Groove Mode

For images with dark groove lines between grains:

1. **Bilateral filtering** — Light denoising preserving edges
2. **Multi-scale black top-hat** — Extracts dark grooves at 3 kernel sizes (9, 15, 21px)
3. **Boundary map** — 80% black top-hat + 10% DoG + 10% gradient
4. **Sensitivity boost** — Power transform: `boundary^(1/sensitivity)`
5. **Interior signal** — `1.0 - boundary`, smoothed with sigma=3.0
6. **Seed finding** — `peak_local_max` on interior signal
7. **Watershed** — Grows grains from seeds on boundary map

### Mosaic Mode

For images with subtle contrast or texture boundaries:

1. **Adaptive bilateral filtering** — Three levels (light, medium, heavy) based on texture ratio
2. **Five boundary signals**:
   - **DoG** — Difference of Gaussians (sigma 1.0 vs 5.0)
   - **LoG** — Negative Laplacian of Gaussian (sigma 3.0), clamped positive
   - **Dark valleys** — Multi-scale (11, 21, 41px) local minimum detection
   - **Step change** — Multi-scale Sobel on medium and heavy bilateral images
   - **Gradient** — Standard Sobel magnitude
3. **Black top-hat** — 15px elliptical kernel for groove detection
4. **Adaptive weighting**:
   - High texture: 15% DoG, 10% LoG, 10% dark, 20% step, 10% grad, 35% top-hat
   - Low texture: 15% DoG, 10% LoG, 15% dark, 40% step, 10% grad, 10% top-hat
5. **Sensitivity boost + watershed** — Same as groove mode but with sigma=8.0 smoothing

### Pass 2: Texture Orientation Split

Only runs in mosaic mode for regions larger than 2x the median grain area:

1. **Structure tensor** — Computes local orientation from image gradients
   - `Jxx, Jxy, Jyy` smoothed with sigma=20
   - Angle: `0.5 * arctan2(2*Jxy, Jxx - Jyy)`
   - Coherence: discriminant / trace
2. **Orientation change map** — Sobel on `cos(2*angle)` and `sin(2*angle)`, weighted by coherence
3. **Split landscape** — 70% contrast map + 30% orientation map
4. **Re-watershed** — Distance transform seeds within each oversized region
5. **Validation** — Only accept splits where all sub-regions meet minimum size

This detects boundaries between grains with similar brightness but different crystallographic orientation (visible as hatching/stripe direction changes in SEM images).
