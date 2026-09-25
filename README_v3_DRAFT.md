# SEM Grain Analyzer (v3)

A desktop tool for measuring grain size from Scanning Electron Microscope (SEM) images. It finds grain boundaries, measures each grain, computes ASTM E112 grain size, and produces Excel and PowerPoint reports you can hand to a customer or file for a spec record.

**Runs entirely on your PC. No internet connection is used or required, ever.**

---

## Fully offline and private

This app never talks to the network. There is no update checker, no telemetry, no crash reporting, no cloud sync, and no web fonts or web links anywhere in the interface — everything it needs (the AI detection model, fonts, icons, templates) is bundled inside the installer. Nothing you load, measure, or export leaves the machine it runs on. This is a hard requirement of the tool, not a setting you can turn off, so it's safe to run on an offline lab PC or one that will never touch the corporate network.

## Installing

1. On a computer with internet access, go to the project's **GitHub Releases** page.
2. Download **`GrainAnalyzer_Setup.exe`**.
3. Copy the installer onto a USB flash drive.
4. Plug the drive into the lab PC (no internet needed there) and run the installer. Everything required is bundled — no separate downloads, no Python, nothing else to install.

If the app ever reports a missing asset (icon, model file, template, etc.) after installing, do **not** look for it online — that means the install is damaged. Uninstall and reinstall from a fresh copy of `GrainAnalyzer_Setup.exe`. The app will never show you a download link for a missing piece.

## Workflow

The app is organized into five pages, in the order you'll normally use them:

1. **Projects** — your workspace: Company/Project or Project/Sample/Lot folders. Create, rename, and organize projects and lots here. Deleted items go to a **Trash** folder inside the workspace and can be restored (an "Undo" option appears right after you delete or move something).
2. **Wizard** ("New session") — starts a new lot: pick or confirm the folder, load your SEM images, and set up calibration and scan area before analysis.
3. **Analyze** — run detection on one image or all images in the lot. Press **F5** to analyze all, **Ctrl+F5** to re-analyze the current image.
4. **Review** — inspect and correct the results image by image (see Grain Editing below).
5. **Reports** — build and export the Excel workbook and/or PowerPoint deck for the lot.

A **guided tour** (11 steps) walks a first-time user through the app; it never starts on its own during automated testing, and you can dismiss or replay it from the Help menu.

## Scale bar calibration

Before analysis, you calibrate pixels-to-real-units by measuring the SEM image's on-screen scale bar in one of three modes:

- **Rectangle** — drag a box over the scale bar; only its width is used as the bar length. Resize with 8 handles, nudge with arrow keys (Shift = 10 px steps). Edges snap to the detected bar ends.
- **Level** (default) — click both ends of the bar; the line locks horizontal, ends snap to the bar within a few pixels, and a magnified loupe follows the cursor for precision.
- **Free** — the original two-click method with a tilt readout, for bars that aren't perfectly horizontal.

You can switch modes freely; your measurement carries over. Calibration is remembered per image/session. Scan-area selection lets you exclude the SEM info/legend bar from analysis; grains touching that border are automatically discarded.

## Grain editing (Review page)

If detection makes a mistake, correct it directly on the image:

| Key | Action |
|---|---|
| **L** | Lasso-select grains (hold Ctrl to add to the selection) |
| **M** | Merge the selected touching grains into one |
| **C** | Cut a grain into two with a drawn line |
| **V** | Switch back to the select tool |
| Delete | Remove a selected grain |

All edits are undoable and are saved with the session, so re-opening a lot later keeps your corrections. Press the **?** button (or Help ▸ Keyboard shortcuts) for the full, current shortcut list.

## ASTM E112 grain size (G)

Every analyzed image reports the mean ASTM E112 grain size number (G), alongside area, equivalent circular diameter (ECD), and other per-grain statistics used for the standard.

## Spec limits & calibration check (both optional)

- **Spec limits** — from a project's or sample's right-click menu ("Spec limits (optional)…") you can define a spec (name, revision, decision rule, and metric rules such as mean G or %RA) that results are checked against. A pass/fail verdict badge then appears on the lot's result card. Off by default; lots with no spec defined show no verdict, exactly as before.
- **Calibration check** — an optional instrument-verification workflow (reachable once turned on in Settings ▸ Calibration verification) that measures a certified reference standard and records a pass/fail check with tolerance. A small status chip ("Cal ✔ 2 d ago" / "Cal due" / "Cal failed") shows on relevant screens once a check has been recorded; it stays hidden if the feature is off or nothing's been checked yet.

## Exporting reports

From the Reports page, export **Excel** (workbook with charts, per-image overview, and per-grain data), **PowerPoint** (a formatted deck), or **both** at once. Files are written to the session's `exports` folder by default, or choose "Save as…" to pick a different location.

## Troubleshooting

- **"Missing asset" / missing model or icon file at startup** — the installation is incomplete or damaged. Reinstall using `GrainAnalyzer_Setup.exe`. Never follow a download link, since the app doesn't provide one.
- **Something looks wrong after an update** — check Help ▸ About for the installed version, and confirm you're using the latest `GrainAnalyzer_Setup.exe` from Releases.
- **Network/firewall prompts** — the app should never ask for network access; if something does, it is not this application's normal behavior.

---

## For developers

- Run the test suite: `.venv\Scripts\python -m pytest tests -q -p no:cacheprovider`
- Launch the GUI: `.venv\Scripts\python main.py`
- Build the Windows installer: `BUILD_WINDOWS.bat` (PyInstaller + NSIS)
- Stack: PySide6 (LGPL), Python 3.11. Project and contribution notes live in `CLAUDE.md` and `handoff/`.
