# Scan Area Selection

## Purpose

Many SEM images include an information bar at the bottom with metadata, scale bars, and logos. The scan area tool lets you draw a rectangle to define the **region of interest** — only grains within this rectangle are analyzed.

Additionally, any grain that **touches the border** of the scan area is automatically discarded. This ensures that partially visible grains at the edges do not skew your measurements.

## How to Set a Scan Area

1. Click **"Set Scan Area (Draw Rectangle)"** in the left panel, or use **Analysis > Set Scan Area** (Ctrl+R)
2. A dialog opens showing the current image

### Drawing the Rectangle

- **Left-click and drag** to draw a rectangle over the area you want to analyze
- The area outside the rectangle is darkened with a semi-transparent overlay
- A green border outlines your selection
- The dimensions are shown: `Analysis area: {width} x {height} px`

The minimum selection size is 10x10 pixels.

### Navigation

- **Scroll wheel** to zoom in/out
- **Alt + left-click drag** or **middle-click drag** to pan

### Applying

- Click **Apply** to confirm the scan area
- Click **Reset** to clear the selection and use the full image
- The left panel shows the scan area status: `Scan area: {w}x{h}px at ({x},{y})`

## How It Works

When analysis runs:

1. The image is cropped to the scan area rectangle
2. Detection runs only on the cropped region
3. Grains touching any edge of the crop are removed from results
4. Grain coordinates are mapped back to full-image positions for display

## Tips

- Draw the rectangle to exclude the SEM info bar at the bottom
- You can also exclude any regions with artifacts or contamination
- The scan area applies to **all images** in the session
- Individual images can have per-image scan areas (set before analysis)
