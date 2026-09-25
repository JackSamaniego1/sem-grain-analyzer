@echo off
:: ============================================================
:: SEM Grain Analyzer - Windows Build Script
:: Double-click this file to build the installer.
:: Requires: Python 3.10+ installed (python.org)
:: ============================================================

title SEM Grain Analyzer - Build

echo ============================================================
echo  SEM Grain Analyzer - Windows Installer Builder
echo ============================================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.10 or later from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python found.
python --version

:: Create a virtual environment
echo.
echo [1/7] Creating isolated Python environment...
if exist build_env rmdir /s /q build_env
python -m venv build_env
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)
echo [OK] Environment created.

:: Activate and install dependencies
echo.
echo [2/7] Installing dependencies (this may take a few minutes)...
call build_env\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install pyinstaller PySide6 opencv-python scikit-image scipy numpy openpyxl xlsxwriter python-pptx qtawesome Pillow segment-anything
if errorlevel 1 (
    echo ERROR: Failed to install packages. Check your internet connection.
    pause
    exit /b 1
)
:: CPU-only torch wheel keeps the installer smaller than the default CUDA
:: build; this app never needs a GPU at runtime (SAM inference on a single
:: SEM image is fine on CPU).
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo ERROR: Failed to install torch (CPU wheel). Check your internet connection.
    pause
    exit /b 1
)
echo [OK] Packages installed.

:: Download SAM model checkpoint
echo.
echo [3/7] Downloading SAM model checkpoint (~375MB)...
if not exist models mkdir models
if not exist models\sam_vit_b_01ec64.pth (
    echo Downloading sam_vit_b_01ec64.pth...
    powershell -Command "Invoke-WebRequest -Uri 'https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth' -OutFile 'models\sam_vit_b_01ec64.pth'"
    if errorlevel 1 (
        echo ERROR: Failed to download SAM model. Check your internet connection.
        pause
        exit /b 1
    )
    echo [OK] SAM model downloaded.
) else (
    echo [OK] SAM model already exists, skipping download.
)
if not exist models\sam_vit_b_01ec64.pth (
    echo ERROR: models\sam_vit_b_01ec64.pth is missing after the download step.
    echo The installer must not ship without the bundled SAM checkpoint.
    pause
    exit /b 1
)

:: Generate icon (drawn in-app by ui.design.branding — no download, no PIL
:: placeholder; same artwork the app itself uses as its window/taskbar icon)
echo.
echo [4/7] Creating application icon...
python -m ui.design.branding resources\icon.ico
if errorlevel 1 (
    echo ERROR: Failed to generate resources\icon.ico.
    pause
    exit /b 1
)
echo [OK] Icon created: resources\icon.ico

:: Run PyInstaller
echo.
echo [5/7] Building executable (this takes 5-10 minutes)...
pyinstaller grain_analyzer.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)
echo [OK] Executable built.

:: Create installer with NSIS if available, otherwise zip
echo.
echo [6/7] Packaging...

:: Check for NSIS
where makensis >nul 2>&1
if not errorlevel 1 (
    echo NSIS found - building installer...
    python create_nsis_script.py
    makensis installer.nsi
    echo [OK] Installer created: GrainAnalyzer_Setup.exe
) else (
    echo NSIS not found - creating zip package instead...
    echo (Optional: Install NSIS from https://nsis.sourceforge.io for a proper installer)
    powershell -Command "Compress-Archive -Path 'dist\GrainAnalyzer' -DestinationPath 'GrainAnalyzer_Windows.zip' -Force"
    echo [OK] Package created: GrainAnalyzer_Windows.zip
)

echo.
echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo.
echo The application is in: dist\GrainAnalyzer\
echo Executable: dist\GrainAnalyzer\GrainAnalyzer.exe
if exist GrainAnalyzer_Windows.zip echo Zip package: GrainAnalyzer_Windows.zip
if exist GrainAnalyzer_Setup.exe echo Installer: GrainAnalyzer_Setup.exe
echo.
pause
