"""
Generates an NSIS installer script for Windows.

A release has TWO assets (both fully offline, no network anywhere):
  GrainAnalyzer_Setup.exe        the standard installer (CPU)
  GrainAnalyzer_GPU_Pack.exe     OPTIONAL NVIDIA GPU option (separate file)
Keep both on the same flash drive, in the same folder.

GPU option in this installer (decision D-32):
  * Interactive: a page after the folder page offers a checkbox. The pack is
    auto-detected next to this installer ($EXEDIR) or chosen with Browse.
    Ticked by default only if the file is found AND an NVIDIA driver
    (nvcuda.dll / nvml.dll in System32) is present.
  * Silent (GrainAnalyzer_Setup.exe /S): no page. The pack is applied only if
    it sits in $EXEDIR AND /GPU=1 is on the command line, e.g.
        GrainAnalyzer_Setup.exe /S /GPU=1
  * Silent exit codes (SetErrorLevel; 0 = everything requested succeeded).
    The app is ALWAYS installed; only the exit code differs:
        10 = /GPU=1 given but GrainAnalyzer_GPU_Pack.exe missing/invalid
        11 = the GPU pack installer ran but failed (app uses the CPU)
  * The pack is run silently AFTER the app and its registry keys are
    installed. If it fails, the app stays installed and uses the CPU.
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
!include "nsDialogs.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "resources\icon.ico"
!define MUI_UNICON "resources\icon.ico"
!define MUI_HEADERIMAGE
!define MUI_BGCOLOR "1A2B4A"
!define MUI_TEXTCOLOR "FFFFFF"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
Page custom GpuPageCreate GpuPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------------------
; Optional GPU option (separate file GrainAnalyzer_GPU_Pack.exe). Offline only.
; ---------------------------------------------------------------------------
Var GpuDlg
Var GpuCheck
Var GpuStatusLbl
Var GpuPackPath
Var GpuPackAuto
Var GpuHasNvidia
Var GpuApply
Var GpuDir
Var GpuSilentWant

!insertmacro GetParameters
!insertmacro GetOptions
!insertmacro GetSize
!insertmacro GetFileName
!insertmacro GetParent

; In: $0 = path. Out: $1 = "" when the file is plausible, else a reason.
; Checks: exists, file name is exactly GrainAnalyzer_GPU_Pack.exe, size at
; least ~50 MB, and it starts with "MZ". The pack itself also refuses to
; install if its version differs from the installed app.
Function GpuCheckFile
  StrCpy $1 "missing"
  IfFileExists "$0" 0 gcf_end
  StrCpy $1 "name"
  ${GetFileName} "$0" $2
  StrCmp $2 "GrainAnalyzer_GPU_Pack.exe" 0 gcf_end
  StrCpy $1 "size"
  ${GetParent} "$0" $3
  ${GetSize} "$3" "/M=GrainAnalyzer_GPU_Pack.exe /S=0K /G=0" $4 $5 $6
  IntCmp $4 50000 gcf_sizeok gcf_end gcf_sizeok
gcf_sizeok:
  StrCpy $1 "format"
  ClearErrors
  FileOpen $7 "$0" r
  IfErrors gcf_end
  FileRead $7 $8 2
  FileClose $7
  StrCmp $8 "MZ" 0 gcf_end
  StrCpy $1 ""
gcf_end:
FunctionEnd

Function .onInit
  StrCpy $GpuApply "0"
  StrCpy $GpuPackPath ""
  StrCpy $GpuPackAuto "0"
  StrCpy $GpuHasNvidia "0"
  StrCpy $GpuSilentWant "0"

  ; Folder of this installer without a trailing backslash ($EXEDIR is "D:\"
  ; at a drive root, which would otherwise give "D:\\GrainAnalyzer_...").
  StrCpy $GpuDir "$EXEDIR"
  StrCpy $R2 "$GpuDir" 1 -1
  StrCmp $R2 "\" 0 +2
    StrCpy $GpuDir "$GpuDir" -1

  ; NVIDIA driver present? nvcuda.dll (CUDA driver) or nvml.dll are installed
  ; into System32 by the NVIDIA display driver. Looked up with WOW64 file
  ; redirection off because this installer is a 32-bit program.
  ${DisableX64FSRedirection}
  IfFileExists "$WINDIR\System32\nvcuda.dll" gpu_nv_yes
  IfFileExists "$WINDIR\System32\nvml.dll" gpu_nv_yes
  Goto gpu_nv_done
gpu_nv_yes:
  StrCpy $GpuHasNvidia "1"
gpu_nv_done:
  ${EnableX64FSRedirection}

  ; GPU option next to this installer?
  StrCpy $0 "$GpuDir\GrainAnalyzer_GPU_Pack.exe"
  Call GpuCheckFile
  StrCmp $1 "" 0 gpu_init_nofile
  StrCpy $GpuPackPath "$0"
  StrCpy $GpuPackAuto "1"
  StrCmp $GpuHasNvidia "1" 0 gpu_init_nofile
  StrCpy $GpuApply "1"
gpu_init_nofile:

  ; Silent install: no page; apply only when /GPU=1 was given AND the pack
  ; was found next to the installer.
  IfSilent 0 gpu_init_done
  StrCpy $GpuApply "0"
  ${GetParameters} $R0
  ${GetOptions} $R0 "/GPU=" $R1
  StrCmp $R1 "1" 0 gpu_init_done
  StrCpy $GpuSilentWant "1"
  StrCmp $GpuPackAuto "1" 0 gpu_init_done
  StrCpy $GpuApply "1"
gpu_init_done:
FunctionEnd

Function GpuPageCreate
  !insertmacro MUI_HEADER_TEXT "GPU option" "Optional: much faster AI-assisted detection on NVIDIA graphics cards"
  nsDialogs::Create 1018
  Pop $GpuDlg
  StrCmp $GpuDlg "error" 0 +2
    Abort

  ${NSD_CreateLabel} 0 0 100% 26u "The GPU option lets this program use an NVIDIA graphics card for AI-assisted detection. It is a separate file, GrainAnalyzer_GPU_Pack.exe, supplied with this installer."
  Pop $0

  ${NSD_CreateLabel} 0 30u 100% 26u ""
  Pop $GpuStatusLbl
  Call GpuUpdateStatus

  StrCmp $GpuPackAuto "1" gpu_page_nobrowse
  ${NSD_CreateButton} 0 58u 50u 12u "Browse..."
  Pop $0
  ${NSD_OnClick} $0 GpuBrowse
gpu_page_nobrowse:

  ${NSD_CreateCheckbox} 0 78u 100% 12u "Install the NVIDIA GPU option (much faster AI-assisted detection)"
  Pop $GpuCheck
  StrCmp $GpuApply "1" 0 +2
    ${NSD_Check} $GpuCheck
  ${NSD_OnClick} $GpuCheck GpuCheckClick
  StrCmp $GpuPackPath "" 0 +2
    EnableWindow $GpuCheck 0

  StrCmp $GpuHasNvidia "1" gpu_page_nvnote
  ${NSD_CreateLabel} 0 94u 100% 12u "No NVIDIA graphics card was found on this PC. The app will use the CPU."
  Pop $0
gpu_page_nvnote:
  nsDialogs::Show
FunctionEnd

Function GpuUpdateStatus
  StrCmp $GpuPackPath "" gus_none
  StrCmp $GpuPackAuto "1" 0 gus_chosen
  ${NSD_SetText} $GpuStatusLbl "GPU option found next to this installer:$\r$\n$GpuPackPath"
  Return
gus_chosen:
  ${NSD_SetText} $GpuStatusLbl "GPU option file selected:$\r$\n$GpuPackPath"
  Return
gus_none:
  ${NSD_SetText} $GpuStatusLbl "GPU option file not found next to this installer."
FunctionEnd

; Keep the tick in $GpuApply as it changes, so Back then Next restores it
; (nsDialogs does not call the Leave function when going Back).
Function GpuCheckClick
  ${NSD_GetState} $GpuCheck $GpuApply
FunctionEnd

Function GpuBrowse
  nsDialogs::SelectFileDialog open "$GpuDir\GrainAnalyzer_GPU_Pack.exe" "GrainAnalyzer_GPU_Pack.exe|GrainAnalyzer_GPU_Pack.exe|Programs (*.exe)|*.exe"
  Pop $0
  StrCmp $0 "" gb_end
  Call GpuCheckFile
  StrCmp $1 "" gb_ok
  MessageBox MB_OK|MB_ICONEXCLAMATION "That is not the GPU option file. Please choose GrainAnalyzer_GPU_Pack.exe, the separate file supplied with this installer."
  Goto gb_end
gb_ok:
  StrCpy $GpuPackPath "$0"
  StrCpy $GpuPackAuto "0"
  Call GpuUpdateStatus
  EnableWindow $GpuCheck 1
  ${NSD_Check} $GpuCheck
  StrCpy $GpuApply "1"
gb_end:
FunctionEnd

Function GpuPageLeave
  ${NSD_GetState} $GpuCheck $GpuApply
  StrCmp $GpuApply "1" 0 gpl_end
  StrCmp $GpuPackPath "" 0 gpl_end
  MessageBox MB_OK|MB_ICONEXCLAMATION "Please choose the GPU option file with Browse, or untick the box."
  Abort
gpl_end:
FunctionEnd

Section "Main Application" SecMain
  ; GPU pack (optional second installer) present from an earlier install?
  ; Re-installing overwrites its CPU-named files with CPU ones and leaves a
  ; mixed folder until the pack is run again (nothing is deleted here).
  ReadRegStr $R0 HKLM "${INSTALL_REG_KEY}" "GpuPack"

  SetOutPath "$INSTDIR"
  File /r "dist\${APP_DIR}\*.*"
  File "THIRD_PARTY_LICENSES.txt"
  DeleteRegValue HKLM "${INSTALL_REG_KEY}" "GpuPack"
  StrCmp $R0 "" nogpupack
  ; (not shown when the pack is being applied again in this same run)
  StrCmp $GpuApply "1" nogpupack
  MessageBox MB_OK|MB_ICONINFORMATION "The earlier GPU pack was replaced by this standard install. To use the NVIDIA GPU again, run GrainAnalyzer_GPU_Pack.exe once more after this setup finishes." /SD IDOK
nogpupack:

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

  ; Optional GPU option: only a file the user picked (or that sits next to
  ; this installer with /GPU=1 in silent mode). The app and its registry keys
  ; are already in place, which the pack's same-version check needs.
  StrCmp $GpuApply "1" gpu_run_go
  ; /GPU=1 requested silently but no usable pack next to the installer.
  StrCmp $GpuSilentWant "1" 0 gpu_run_skip
  SetErrorLevel 10
  Goto gpu_run_skip
gpu_run_go:
  StrCmp $GpuPackPath "" gpu_run_skip
  StrCpy $0 "$GpuPackPath"
  Call GpuCheckFile
  StrCmp $1 "" 0 gpu_run_failed
  DetailPrint "Installing the GPU option (this can take several minutes)..."
  ExecWait '"$GpuPackPath" /S' $0
  StrCmp $0 "0" gpu_run_skip
gpu_run_failed:
  MessageBox MB_OK|MB_ICONEXCLAMATION "The GPU option could not be installed. The app is installed and will use the CPU. You can run GrainAnalyzer_GPU_Pack.exe later." /SD IDOK
  IfSilent 0 gpu_run_skip
  SetErrorLevel 11
gpu_run_skip:
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

if __name__ == "__main__":
    with open("installer.nsi", "w") as f:
        f.write(nsis_content)
    print("installer.nsi created.")
