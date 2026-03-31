# Running Analysis

## Analyze All Images

Click the **"Analyze ALL Images"** button (or press **F5**) to run grain detection on every loaded image.

A progress dialog appears showing:

- **Overall progress bar** — how many images are complete
- **Per-image status** — waiting, analyzing, done (with grain count), or error
- **Current image progress** — percentage through the current detection pipeline
- **Estimated time remaining** — calculated from average processing time

### Cancelling

Click **Cancel** at any time to stop the analysis. The progress window closes immediately, and any images that were already completed retain their results.

## Reanalyze Current Image

After adjusting detection parameters, you can re-run analysis on just the currently selected image:

- Click **"Reanalyze Current Image"** (or press **Ctrl+F5**)
- This is much faster than re-analyzing everything
- Useful for fine-tuning parameters on a difficult image

## What Happens During Analysis

For each image, the analysis pipeline:

1. **Preprocesses** the image (grayscale conversion, auto-crop of bright borders)
2. **Applies scan area crop** if set, and records the offset
3. **Runs the selected detection mode** (SAM, threshold, or boundary)
4. **Measures grain properties** — area, perimeter, diameter, circularity, etc.
5. **Computes statistics** — mean, standard deviation, median, coverage
6. **Generates overlay** — colored grain outlines with ID labels
7. **Removes border grains** — any grain touching the scan area edge is discarded
8. **Maps coordinates** back to the full image

## After Analysis

Once analysis completes:

- Tab titles update to show grain counts (e.g., `sample.tif (142g)`)
- The **right panel** shows statistics, histograms, and grain table
- The **Image Enhancement** and **Detection Parameters** sections become visible (for threshold and boundary modes)
- The **Export to Excel** button is enabled
- The status bar shows the total grain count across all images
