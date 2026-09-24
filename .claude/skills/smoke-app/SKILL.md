---
name: smoke-app
description: Launch the Grain Analyzer headless, render the main window to a PNG, and confirm it starts without exceptions. Use after any UI change.
allowed-tools: PowerShell, Bash, Read, Write
---

# Headless smoke test of the GUI

1. Run (PowerShell):
   ```powershell
   New-Item -ItemType Directory -Force scratch\qa | Out-Null
   $env:QT_QPA_PLATFORM='offscreen'
   .venv\Scripts\python -c "from PyQt6.QtWidgets import QApplication; from ui.theme import apply_dark_theme; from ui.main_window import MainWindow; a=QApplication([]); apply_dark_theme(a); w=MainWindow(); w.resize(1440,880); w.show(); a.processEvents(); w.grab().save('scratch/qa/smoke.png'); print('SMOKE OK')"
   ```
2. If it prints `SMOKE OK`, Read `scratch/qa/smoke.png` and describe what is visible (layout regions, obvious rendering defects).
3. If it throws, report the traceback's last 5 lines and the file:line of the failure.
4. For a real interactive check when a display is available: `.venv\Scripts\python main.py` (splash for 2 s, then main window).
