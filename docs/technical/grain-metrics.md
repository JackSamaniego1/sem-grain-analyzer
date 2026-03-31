# Grain Metrics

## Per-Grain Measurements

Each detected grain is measured using `skimage.measure.regionprops`. The following properties are computed:

### Area

- **area_px** — Number of pixels in the grain region
- **area_um2** — `area_px / (px_per_um)²` (requires calibration)

### Perimeter

- **perimeter_px** — Length of the grain boundary in pixels (using `region.perimeter`)
- **perimeter_um** — `perimeter_px / px_per_um`

### Equivalent Diameter

The diameter of a circle with the same area as the grain:

```
equivalent_diameter = sqrt(4 * area / pi)
```

- **equivalent_diameter_px** — In pixels
- **equivalent_diameter_um** — In calibrated units

### Major and Minor Axis

Lengths of the major and minor axes of the best-fit ellipse:

- **major_axis_um** — `region.axis_major_length / px_per_um`
- **minor_axis_um** — `region.axis_minor_length / px_per_um`

### Aspect Ratio

Ratio of major to minor axis length:

```
aspect_ratio = major_axis / minor_axis
```

- **1.0** = perfectly equiaxed (circular)
- **> 1.0** = elongated grain

### Circularity

How close the grain shape is to a perfect circle:

```
circularity = 4 * pi * area / perimeter²
```

- **1.0** = perfect circle
- **< 1.0** = irregular or elongated shape
- Clamped to maximum of 1.0

### Eccentricity

Eccentricity of the best-fit ellipse (from `region.eccentricity`):

- **0.0** = perfect circle
- **1.0** = line (degenerate ellipse)

### Centroid

Center of mass of the grain region:

- **centroid_x** — X coordinate (column)
- **centroid_y** — Y coordinate (row)

Note: When a scan area is set, centroids are remapped to full-image coordinates.

### Bounding Box

Axis-aligned bounding box: `(min_row, min_col, max_row, max_col)`

## Aggregate Statistics

Computed across all grains in an image:

| Statistic | Field | Description |
|-----------|-------|-------------|
| Mean area | `mean_area_um2` | Average grain area |
| Std dev area | `std_area_um2` | Standard deviation of areas |
| Median area | `median_area_um2` | Median grain area |
| Min area | `min_area_um2` | Smallest grain |
| Max area | `max_area_um2` | Largest grain |
| Mean diameter | `mean_diameter_um` | Average equivalent diameter |
| Std dev diameter | `std_diameter_um` | Standard deviation of diameters |
| Mean circularity | `mean_circularity` | Average circularity (0-1) |
| Mean aspect ratio | `mean_aspect_ratio` | Average elongation |
| Total analyzed area | `total_analyzed_area_um2` | Full image area in calibrated units |
| Grain coverage | `grain_coverage_pct` | Sum of grain areas / total area x 100% |

## Unit Auto-Scaling

The application automatically selects display units to avoid tiny decimal values:

```python
um_per_px = 1.0 / px_per_um
nm_per_px = um_per_px * 1000.0

if nm_per_px < 50:
    # Very small pixels → use nanometers
    area_unit = "nm²"   (multiply um² by 1e6)
    diam_unit = "nm"    (multiply um by 1000)
else:
    # Normal → use micrometers
    area_unit = "µm²"
    diam_unit = "µm"
```

## Size Filtering

Grains are filtered by two user-configurable parameters:

- **min_grain_size_px** (default: 50 px²) — Regions smaller than this are discarded as noise
- **max_grain_size_px** (default: 0 = unlimited) — Regions larger than this are discarded as background

The boundary-first pipeline enforces a minimum of 20 px² regardless of the user setting.
