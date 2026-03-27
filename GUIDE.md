# SEM Grain Analyzer — Complete Guide
## For users with no coding knowledge

---

## What this app does

**SEM Grain Analyzer** is a desktop application that:

1. Loads an SEM image (TIFF, PNG, JPEG, BMP)
2. Detects the scale bar automatically and converts pixels → real-world units (µm)
3. Automatically finds every grain in the image using computer vision
4. Counts grains and measures each one's area, diameter, perimeter, shape, and more
5. Exports a formatted Excel report with statistics, charts, and per-grain data

---

## How to get the installer (two options)

---

### OPTION A — Let GitHub build it for you (RECOMMENDED — no Python needed)

This is the easiest method. GitHub's free cloud servers build the Windows `.exe`
and macOS `.dmg` for you automatically. You just download the finished file.

**Steps:**

1. Create a free account at https://github.com (click "Sign up")

2. Create a new repository:
   - Click the green **"New"** button on your GitHub home page
   - Name it anything, e.g. `sem-grain-analyzer`
   - Leave all other options as default
   - Click **"Create repository"**

3. Upload the project files:
   - On your new repository page, click **"uploading an existing file"**
   - Drag the entire `sem_grain_analyzer` folder contents into the upload area
   - Keep the folder structure exactly as it is
   - Scroll down and click **"Commit changes"**

4. Wait for the build (~5–8 minutes):
   - Click the **"Actions"** tab at the top of your repository
   - You will see a workflow called **"Build Installers"** running
   - A green checkmark means it finished successfully

5. Download your installer:
   - Click on the completed workflow run
   - Scroll to the bottom — you will see **"Artifacts"**:
     - `SEMGrainAnalyzer-Windows-Setup` → Windows installer `.exe`
     - `SEMGrainAnalyzer-macOS-DMG` → macOS disk image `.dmg`
   - Click to download

6. Install and run:
   - **Windows:** Double-click `SEMGrainAnalyzer_Setup.exe`, click through the wizard.
     The app appears in your Start Menu and on your Desktop.
   - **macOS:** Open the `.dmg`, drag `SEMGrainAnalyzer.app` to your Applications folder.

---

### OPTION B — Build it yourself on Windows (one-time setup)

You only need to do this once. After that, you have the installer on your machine.

#### Step 1 — Install Python (one time only)

1. Go to https://www.python.org/downloads/
2. Click the big yellow **"Download Python 3.11.x"** button
3. Run the installer
4. **IMPORTANT:** On the first screen, check the box that says
   **"Add Python to PATH"** before clicking Install
5. Click **"Install Now"**
6. When finished, click **"Close"**

#### Step 2 — Run the build script

1. Unzip the `sem_grain_analyzer` folder somewhere on your computer
   (e.g. your Desktop or Documents)
2. Open the folder
3. Double-click **`BUILD_WINDOWS.bat`**
4. A black window will appear showing progress — this is normal
5. It will download the required packages and build the application
6. This takes **3–8 minutes** depending on your internet speed
7. When it says **"BUILD COMPLETE!"**, press any key to close

#### Step 3 — Find your installer

After the build completes, look in the same folder for:
- **`SEMGrainAnalyzer_Windows.zip`** — contains the app (always created)
- **`SEMGrainAnalyzer_Setup.exe`** — proper installer wizard (only if NSIS is installed)

To get the proper installer wizard, optionally install NSIS first:
1. Go to https://nsis.sourceforge.io/Download
2. Download and install NSIS (it's free)
3. Then run `BUILD_WINDOWS.bat` again

#### Distributing to others

Send the zip or the `.exe` installer to anyone. **They do not need Python.**
They just run the installer and the app appears in their Start Menu.

---

## Using the application

### 1. Open an image
Click **"📂 Open SEM Image..."** in the left panel.
Supported formats: `.tif`, `.tiff`, `.png`, `.jpg`, `.jpeg`, `.bmp`

### 2. Set the scale bar calibration
Click **"📏 Set Scale Bar..."**

The app will try to detect the scale bar automatically. You will then enter:
- **Scale bar length in pixels** — how many pixels wide the scale bar is
  (the app attempts to fill this in automatically)
- **Scale bar label in µm** — the number printed next to the scale bar on your image
  (e.g., if it says "1 µm", enter `1.0`)

Use the **Quick Presets** buttons to quickly set the µm value.

If you are unsure of the pixel length, open your image in any image viewer
(e.g. Windows Photos, Preview on Mac), zoom in, and count the pixels across the bar.

### 3. Adjust detection parameters (optional)
The defaults work for most SEM images. Only adjust if results are wrong:

| Setting | What it does | When to change |
|---|---|---|
| **Blur (σ)** | Smooths the image before detection | Increase if getting many tiny false grains |
| **Threshold offset** | Adjusts how bright a region must be to count as a grain | Negative = detect more; Positive = detect less |
| **Min grain area** | Ignores anything smaller than this | Increase to remove noise/debris |
| **Max grain area** | Ignores anything larger than this | Set if background regions are being detected |
| **Watershed separation** | Controls splitting of touching grains | Increase if joined grains aren't being separated |
| **Grains are dark** | Inverts detection | Check if your grains are darker than background |
| **Use watershed** | Separates touching grains | Uncheck only if grains are completely separated |

### 4. Run analysis
Click **"🔬 Analyze Grains"** (or press **F5**).

The progress bar will show each stage. This usually takes 1–10 seconds.

### 5. Review results

Three view buttons let you switch between:
- **Original** — your unmodified image
- **Grain Overlay** — grains highlighted with colored regions and ID numbers
- **Binary Mask** — the black-and-white detection mask

The right panel shows:
- Grain count and area coverage
- Mean area, mean diameter, circularity, aspect ratio
- A scrollable table of every individual grain
- Full statistics text

### 6. Export to Excel
Click **"📊 Export to Excel"**, choose where to save, click Save.

The Excel file has three sheets:
- **Summary** — all statistics in a formatted report
- **Grain Data** — every grain with all measurements (filterable/sortable)
- **Distribution** — area histogram with chart

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Grains merging into one big blob | Increase **Watershed separation** |
| Too many tiny spots detected | Increase **Min grain area** |
| Background being detected as grains | Increase **Threshold offset**, or check **Grains are dark** |
| Grains missed (under-detection) | Decrease **Threshold offset** |
| Grain outlines look jagged | Increase **Blur (σ)** |
| Scale bar not auto-detected | Enter pixel length manually; bar must be white and horizontal |
| BUILD_WINDOWS.bat fails | Check internet connection; make sure Python PATH was checked during install |
| App won't start on Windows | Right-click → "Run as administrator" once |

---

## Important notes

- You must **build on the same OS you want to run on**. A Windows build only runs
  on Windows; a macOS build only runs on macOS.
- The GitHub Actions method (Option A) builds both automatically.
- The built application is completely self-contained — no Python, no dependencies
  needed on the target computer.

---

## Algorithm (for reference)

The detection pipeline:
1. **Grayscale conversion** — converts color image to intensity
2. **Gaussian blur** — reduces high-frequency noise
3. **Otsu's thresholding** — automatically finds the optimal brightness threshold
   to separate grains from background
4. **Morphological closing** — fills small holes inside grain regions
5. **Morphological opening** — removes tiny noise particles outside grains
6. **Distance transform** — measures how far each pixel is from the nearest background
7. **Watershed segmentation** — flood-fills from grain centers to separate touching grains
8. **Region properties** — measures area, perimeter, axes, eccentricity, circularity
   for every detected grain
