# Scale Bar Calibration

## Why Calibrate?

Without calibration, all measurements are in **pixel units** (px, px²). Calibrating with the scale bar converts measurements to real-world units (um, nm), which is essential for meaningful grain size analysis.

Calibration applies to **all loaded images** — set it once and it is used across your entire session.

## How to Calibrate

1. Click **"Set Scale Bar (Click 2 Points)"** in the left panel, or use **Analysis > Set Scale Bar** (Ctrl+K)
2. A calibration dialog opens showing the current image

### Step 1: Place Two Points

- **Zoom in** on the scale bar using the scroll wheel (the cursor stays centered)
- **Click the left end** of the scale bar — a red crosshair (P1) appears
- **Click the right end** of the scale bar — a green crosshair (P2) appears

A yellow dashed line connects the two points, and the pixel distance is displayed.

### Step 2: Enter the Real-World Length

- In the **"Scale bar length"** field, enter the known length of the scale bar
- Select the correct **unit** from the dropdown: um (micrometers), nm (nanometers), or mm (millimeters)
- The dialog shows the calculated **px/um** value in real time

### Step 3: Apply

- Click **"Apply to ALL Images"** to set the calibration
- The left panel updates to show the calibration: `px/um` and `nm/px` values

## Tips

- Zoom in as much as possible on the scale bar for maximum accuracy
- Place points precisely at the ends of the bar, not on the text labels
- If you make a mistake, click **Reset** to clear the points and start over
- The calibration status shows a green checkmark when active, or a warning when uncalibrated

## Auto Scale Bar Detection

The application includes experimental automatic scale bar detection that:

1. Crops the bottom 22% of the image where SEM scale bars typically appear
2. Looks for a bright horizontal bar (>180 brightness, >20px wide, <10px tall)
3. Attempts OCR on nearby text to read the scale value (requires pytesseract)

This feature is used internally and may not work on all SEM images. Manual calibration is recommended for best accuracy.
