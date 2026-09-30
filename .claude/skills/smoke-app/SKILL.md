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
   .venv\Scripts\python -c "import main; from PySide6.QtWidgets import QApplication; main.configure_runtime(); a=QApplication([]); from ui.design.theme import apply_theme; apply_theme(a,'dark'); from ui.app_shell import AppShell; w=AppShell(); w.resize(1440,880); w.show(); [a.processEvents() for _ in range(20)]; w.grab().save('scratch/qa/smoke.png'); print('SMOKE OK'); w.close(); a.processEvents()"
   ```
2. If it prints `SMOKE OK`, Read `scratch/qa/smoke.png` and describe what is visible (layout regions, obvious rendering defects).
3. If it throws, report the traceback's last 5 lines and the file:line of the failure.
4. For a real interactive check when a display is available: `.venv\Scripts\python main.py` (splash for 2 s, then main window).
