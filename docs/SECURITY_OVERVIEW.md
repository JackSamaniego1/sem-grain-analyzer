# Grain Analyzer — Security & Data-Handling Overview

**Version covered:** 3.0.0 (Windows) · **Source:** https://github.com/JackSamaniego1/sem-grain-analyzer

Grain Analyzer is a standalone desktop program that measures grain size in SEM images. It runs fully offline: it doesn't use the network, has no server side and no user accounts, and doesn't send data anywhere. This document explains how that is enforced and how to check it.

---

## 1. Summary

| Question | Answer |
|---|---|
| Does it connect to the internet? | **No.** It is blocked in two independent places (see §2): inside the program and in the Windows Firewall. |
| Does it send telemetry, crash reports or usage data? | **No.** There is no telemetry, analytics, crash upload, update check or licence check. |
| Does it download anything at runtime (models, fonts, updates)? | **No.** Every asset, including the AI model file, is bundled in the installer. If a file is missing, the app tells the user to reinstall. It never offers a download link. |
| Where is data stored? | Only on the local machine, in folders listed in §3. |
| Does it need a cloud account, licence server or login? | **No.** |
| Does it run in the background or at startup? | **No.** It runs only while the user has it open. It installs no service, scheduled task or startup entry. |
| Does it need admin rights to run? | **No.** Admin rights are needed only to install it (see §5). |

---

## 2. How "no network" is enforced

### Layer 1: inside the program (`core/offline_guard.py`)
- The guard is the first thing the program starts (`main.py`, line 17), before any library that could reach the network is loaded.
- It replaces Python's low-level network functions (`socket.connect`, `connect_ex`, `sendto`, `sendmsg`, `create_connection`, `getaddrinfo`). Any attempt to reach a non-local address is refused with an `OfflineViolation` error and logged locally.
- Only loopback (`127.0.0.0/8`, `::1`) is allowed, because the UI toolkit uses it for communication within the program itself. Loopback never leaves the computer.
- It sets environment variables that stop the machine-learning libraries (PyTorch, Hugging Face) from trying to download anything. It also points their cache folder at a local directory.
- Log of blocked attempts: `%LOCALAPPDATA%\GrainAnalyzer\logs\offline_guard.log`. In normal use this file stays empty.

### Layer 2: Windows Firewall (added by the installer)
The installer adds two Windows Firewall rules that block **all inbound and all outbound** traffic for `GrainAnalyzer.exe`, on every network profile:
```
Grain Analyzer - block outbound   (dir=out, action=block)
Grain Analyzer - block inbound    (dir=in,  action=block)
```
This backs up Layer 1: the operating system blocks traffic even from native (C/C++) library code that the Python guard can't see. The uninstaller removes both rules.

> On PCs where Group Policy controls the firewall, local rules may be ignored. IT can apply the same two rules centrally if required.

### Layer 3: the source code itself
- The app's own code (the `core`, `data`, `reports` and `ui` folders) doesn't use any networking library: no `socket`, `urllib`, `http`, `requests`, QtNetwork, QtWebEngine or `webbrowser`.
- Documents open only through Windows' local file associations: Excel opens `.xlsx`, and Explorer shows a folder.

### Automated verification
`tests/test_offline.py` is part of the test suite (839 tests in total, all passing for 3.0.0). It checks the following:
- The app's own source code contains no networking code.
- Connecting to remote hosts over TCP, UDP or DNS lookup is blocked. Loopback still works.
- The installer script contains the firewall block rules.
- **A full analysis and export of Excel and PowerPoint makes no network attempts at all.**

---

## 3. What data is stored, and where

All data stays on the local disk, in plain, open file formats:

| Data | Location | Format |
|---|---|---|
| Projects, images, results, reports | `Documents\GrainAnalyzer\Projects\` by default. The user can change this folder in Settings, e.g. to a network share they already use. | Folders with images, JSON, SQLite index, `.xlsx`, `.pptx` |
| App settings | `%LOCALAPPDATA%\GrainAnalyzer\settings.json` | JSON |
| Offline-guard log | `%LOCALAPPDATA%\GrainAnalyzer\logs\` | Text |
| Temporary files | Windows temp folder, deleted after use | — |

- Nothing is stored in the Windows registry apart from the standard Add/Remove Programs entry.
- The app is not encrypted and has no access control. Data gets whatever protection the folder it sits in already has, the same as any Excel or image file the user saves there.
- **Uninstalling** removes the program files, shortcuts, the registry entry and the firewall rules. It **doesn't** delete the user's projects or reports.

---

## 4. Third-party components

- **UI:** PySide6 / Qt (LGPL v3).
- **Image analysis:** OpenCV, scikit-image, SciPy, NumPy (BSD/Apache).
- **AI model:** PyTorch and Meta's Segment Anything (SAM), both Apache 2.0.
- **Reports:** xlsxwriter and python-pptx (BSD/MIT).

All of these are permissive open-source licences with no fees and no licence server. The full list with licence texts ships in `THIRD_PARTY_LICENSES.txt`.

**About the AI model file:** SAM runs **locally on the PC's CPU or GPU**. Images are never sent to an AI service. The model checkpoint (`sam_vit_b_01ec64.pth`) is Meta's official public release, bundled at build time and loaded only from the install folder.

---

## 5. Installation and build provenance

- **How the installer is built:** GitHub Actions compiles the public source code at the tagged release (`v3.0.0`) and attaches `GrainAnalyzer_Setup.exe` to the GitHub Release. The build uses PyInstaller for the program and NSIS for the installer.
- **Installer scope:**
  - Installs to `C:\Program Files\GrainAnalyzer`.
  - Creates Start-menu and desktop shortcuts.
  - Adds the Add/Remove Programs entry.
  - Adds the two firewall rules.
  - Nothing else: no drivers, no services, no browser extensions, no startup items.
- **Admin rights** are requested only because it installs to Program Files and adds the firewall rules. The app itself runs as a normal user.
- **Air-gapped install:** the installer is fully self-contained (~670 MB). You can copy it to the target PC on removable media, and the PC never needs internet access.

### Known limitations
- **The installer is not code-signed.** Windows SmartScreen shows "Windows protected your PC", so the user has to choose **More info › Run anyway**. To check that the file came from the official release, compare its SHA-256 hash with the file on the GitHub Release page:
  `Get-FileHash .\GrainAnalyzer_Setup.exe -Algorithm SHA256`
- **The Python guard (Layer 1) can't see native code.** The firewall rules (Layer 2) cover that case. If Group Policy disables local firewall rules, IT should deploy the rules centrally.
- **Upgrading over an older version** replaces the program in place. Uninstall the old version first for a clean install; the data folders are not affected.

---

## 6. How IT can check this independently

1. **Firewall rules:** after install, run
   `netsh advfirewall firewall show rule name="Grain Analyzer - block outbound"`
2. **Network activity:** run the app while watching it with Resource Monitor (Network tab) or `netstat -b`. It shows no connections to outside addresses.
3. **Guard log:** `%LOCALAPPDATA%\GrainAnalyzer\logs\offline_guard.log` records any blocked attempt.
4. **Source review:** all source code is public. The key files are `core/offline_guard.py`, `tests/test_offline.py` and `create_nsis_script.py` (the installer).
5. **Offline test:** install and run it on a PC with networking disabled. Everything works, because nothing depends on the network.
