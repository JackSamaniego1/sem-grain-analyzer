# Editing Grains

After analysis, you can manually remove false detections by selecting and deleting individual grains.

## Selecting a Grain

1. Switch to **Grain Overlay** view using the view mode buttons at the top of the image canvas
2. **Left-click** on any detected grain in the overlay

When a grain is selected:

- A **red circle** highlights the selected grain
- A label appears: **"Grain {ID} selected — press Delete to remove"**

## Deleting a Grain

With a grain selected, press the **Delete** key on your keyboard.

The grain is immediately removed from:

- The overlay visualization
- The label image
- The grain list and statistics
- Any subsequent Excel exports

## Tips

- You must be in **Grain Overlay** view to select grains
- Only one grain can be selected at a time
- After deleting grains, the statistics in the right panel update automatically
- Deleted grains cannot be recovered — if you need them back, reanalyze the image
- Use **Reanalyze Current Image** (Ctrl+F5) after adjusting parameters instead of manually deleting many grains

## Common False Positives to Remove

- Debris or contamination particles
- Scale bar or text artifacts (if scan area was not set)
- Edge artifacts near image boundaries
- Pores or voids misidentified as grains
- Twin boundaries within a single grain counted as separate grains
