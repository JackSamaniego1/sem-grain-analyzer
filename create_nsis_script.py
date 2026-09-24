"""
Generates an NSIS installer script for Windows.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from version import __version__, APP_NAME as _APP_NAME, APP_PUBLISHER as _APP_PUBLISHER

nsis_content = r"""
; Grain Analyzer NSIS Installer Script
; Generated automatically by create_nsis_script.py

!define APP_NAME "__APP_NAME__"
!define APP_VERSION "__APP_VERSION__"
!define APP_PUBLISHER "__APP_PUBLISHER__"
!define APP_EXE "GrainAnalyzer.exe"
!define APP_DIR "GrainAnalyzer"
!define INSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\GrainAnalyzer"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "GrainAnalyzer_Setup.exe"
InstallDir "$PROGRAMFILES64\${APP_DIR}"
InstallDirRegKey HKLM "${INSTALL_REG_KEY}" "InstallLocation"
RequestExecutionLevel admin

; Modern UI
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "resources\icon.ico"
!define MUI_UNICON "resources\icon.ico"
!define MUI_HEADERIMAGE
!define MUI_BGCOLOR "1A2B4A"
!define MUI_TEXTCOLOR "FFFFFF"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Main Application" SecMain
  SetOutPath "$INSTDIR"
  File /r "dist\${APP_DIR}\*.*"
  File "THIRD_PARTY_LICENSES.txt"

  ; Write registry for Add/Remove Programs
  WriteRegStr HKLM "${INSTALL_REG_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${INSTALL_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${INSTALL_REG_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${INSTALL_REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${INSTALL_REG_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKLM "${INSTALL_REG_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${INSTALL_REG_KEY}" "NoRepair" 1

  ; Create shortcuts
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk" "$INSTDIR\uninstall.exe"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  ; Offline guarantee (decision D-14): OS-level block of all network traffic
  ; for the app executable. Defense-in-depth on top of core/offline_guard.py,
  ; which cannot see sockets opened by native (C/C++) libraries.
  ; Non-fatal if the firewall is centrally managed.
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="Grain Analyzer - block outbound"'
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="Grain Analyzer - block inbound"'
  nsExec::ExecToLog 'netsh advfirewall firewall add rule name="Grain Analyzer - block outbound" dir=out action=block program="$INSTDIR\${APP_EXE}" enable=yes profile=any'
  nsExec::ExecToLog 'netsh advfirewall firewall add rule name="Grain Analyzer - block inbound" dir=in action=block program="$INSTDIR\${APP_EXE}" enable=yes profile=any'

  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="Grain Analyzer - block outbound"'
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name="Grain Analyzer - block inbound"'
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  DeleteRegKey HKLM "${INSTALL_REG_KEY}"
SectionEnd
"""

nsis_content = (nsis_content
                 .replace("__APP_NAME__", _APP_NAME)
                 .replace("__APP_VERSION__", __version__)
                 .replace("__APP_PUBLISHER__", _APP_PUBLISHER))

with open("installer.nsi", "w") as f:
    f.write(nsis_content)
print("installer.nsi created.")
