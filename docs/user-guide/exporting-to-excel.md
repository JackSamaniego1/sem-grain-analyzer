# Exporting to Excel

The Grain Analyzer exports professional Excel reports with images, charts, statistics, and per-grain data.

## Export Options

### Export All Images

- **Menu**: File > Export All to Excel (Ctrl+E)
- **Button**: "Export to Excel" in the left panel
- Generates a single `.xlsx` file covering all analyzed images

### Export Current Image

- **Menu**: File > Export Current to Excel (Ctrl+Shift+E)
- Generates a report for only the currently selected image

## Report Structure

Sheets appear in this order: **Overview**, **Lot Summary**, **Summary
Charts**, one sheet per image, **Methods**, then the raw per-grain data
sheets last.

### Overview Sheet

- **Report header** — title, operator, date, image count, and the
  Job / Part / Lot the report covers (all of them, e.g. "Lot: L-1, L-2",
  when several lots are included)
- **Per-image table** — one row per image with its own Job, Part and Lot,
  grain count, mean/median area, mean diameter and ASTM G
- **Combined (all images)** row at the bottom

### Lot Summary Sheet

One block per lot, stacked down the sheet:

- **Grain-size distribution chart** for all images in that lot combined,
  titled with the lot name
- **Stats beside it** — lot, number of images, total grains, mean ASTM G,
  mean diameter and mean area (in calibrated units)

If two parts both have a lot with the same name, they get separate blocks
titled "Part · Lot". Turn the sheet off in the report editor's outline if
you don't need it.

### Summary Charts Sheet

- **Grain Area Distribution** and **Grain Diameter Distribution** —
  histograms with normal-fit curve for all images combined

### Per-Image Summary Sheets

For each image, a summary sheet includes:

- **Original image** — High-resolution snapshot of the SEM image
- **Grain overlay** — Annotated image showing detected grains
- **Grain Area Distribution chart** — Bar chart with normal fit
- **Grain Diameter Distribution chart** — Bar chart with normal fit
- **Summary statistics table** — Grain count, mean area, diameter, circularity, coverage, etc.

### Per-Image Data Sheets

For each image, a data sheet provides the complete grain table:

| Column | Description |
|--------|-------------|
| ID | Grain number |
| Area | In calibrated units or px² |
| Diameter | Equivalent circular diameter |
| Major Axis | Major axis length |
| Minor Axis | Minor axis length |
| Perimeter | Grain boundary length |
| Circularity | Shape factor (0-1) |
| Aspect Ratio | Major/minor axis |
| Eccentricity | Shape elongation |
| Cx, Cy | Centroid coordinates |

The data table includes:

- Auto-filter headers for sorting and filtering
- Alternating row colors for readability
- Professional styling with dark blue headers

## Image Quality

Exported images are saved at **800px width** with low PNG compression for high-quality reproduction in reports and publications. Both the original SEM image and the grain overlay are included side by side.

## Histogram Charts

Both the area and diameter distribution charts include:

- **Blue bars** (area) or **green bars** (diameter) — Grain count per bin
- **Pink curve** — Normal distribution fit
- **Data table** — Bin ranges, counts, and normal fit values

The number of histogram bins matches your current setting in the results panel.
