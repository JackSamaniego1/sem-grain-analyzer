# Viewing Results

After analysis, the right panel displays comprehensive results organized into tabs.

## Statistics Cards

At the top of the results panel, summary cards show:

- **Grains Detected** — Total count
- **Grain Coverage** — Percentage of the analyzed area covered by grains (requires calibration)
- **Mean Area** — Average grain area in calibrated units (um² or nm²) or pixels
- **Mean Diameter** — Average equivalent diameter
- **Mean Circularity** — Average shape factor (0-1, where 1 = perfect circle)
- **Mean Aspect Ratio** — Average major/minor axis ratio (1 = equiaxed)

## Grain Area Distribution

A histogram showing the distribution of grain areas with:

- **Blue bars** — Frequency count per bin
- **Pink curve** — Normal distribution fit overlay
- **X-axis** — Grain area in calibrated units
- **Y-axis** — Number of grains

Use the **Bins** spinner to adjust the number of histogram bins (3-100). Bins use whole-number increments starting from 0 (e.g., 0-10, 10-20, 20-30).

## Grain Diameter Distribution

Same layout as the area distribution, but showing equivalent grain diameters:

- **Blue bars** — Frequency count per diameter bin
- **Pink curve** — Normal distribution fit
- **X-axis** — Equivalent grain diameter in calibrated units

## Grain Table

A sortable table showing per-grain measurements:

| Column | Description |
|--------|-------------|
| ID | Grain identification number |
| Area | Grain area (um², nm², or px²) |
| Diameter | Equivalent circular diameter |
| Circularity | 4pi x area / perimeter² (0-1) |
| Aspect Ratio | Major axis / minor axis |

Click column headers to sort. Select a row to highlight the corresponding grain on the image.

## Statistics Tab

A detailed text summary showing:

- Grain count
- Calibration info (px/um)
- Coverage percentage
- Area statistics: mean, std dev, median, min, max
- Diameter statistics: mean, std dev
- Shape statistics: circularity, aspect ratio

## Smart Units

The application automatically selects the best unit to avoid tiny decimals:

- If the pixel size is very small (< 50 nm/px): displays in **nm** and **nm²**
- Otherwise: displays in **um** and **um²**
- Without calibration: displays in **px** and **px²**
