# Loading Images

## Supported Formats

The Grain Analyzer supports all common image formats:

- **TIFF** (.tif, .tiff) — recommended for SEM images
- **PNG** (.png)
- **JPEG** (.jpg, .jpeg)
- **BMP** (.bmp)

## Opening Images

There are two ways to open images:

1. Click the **"Open SEM Images..."** button in the left panel
2. Use the menu: **File > Open Images** or press **Ctrl+O**

You can select multiple images at once by holding **Ctrl** (or **Cmd** on macOS) while clicking files in the file dialog.

## Image Tabs

Each loaded image appears as a tab in the center of the application. You can:

- **Click a tab** to switch between images
- **Close a tab** by clicking the X on the tab (if available)

After analysis, each tab title updates to show the grain count, for example: `sample_001.tif (142g)`.

## Image Navigation

Once an image is loaded:

| Action | Control |
|--------|---------|
| **Zoom in/out** | Scroll wheel |
| **Pan** | Alt + left-click drag, or middle-click drag |

The image automatically fits to the canvas when first loaded. Zooming centers on the mouse cursor position, with a range from 0.05x to 20x magnification.

## View Modes

After running analysis, each image tab has three view modes accessible via buttons at the top of the canvas:

- **Original** — The raw SEM image as loaded
- **Grain Overlay** — Detected grains highlighted with colored outlines and ID numbers
- **Binary Mask** — The segmented binary image showing grain vs. background regions

Switch between these to inspect detection quality from different perspectives.
