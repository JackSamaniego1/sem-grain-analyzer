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
echo [1/5] Creating isolated Python environment...
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
echo [2/5] Installing dependencies (this may take a few minutes)...
call build_env\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install pyinstaller PyQt6 opencv-python scikit-image scipy numpy openpyxl Pillow
if errorlevel 1 (
    echo ERROR: Failed to install packages. Check your internet connection.
    pause
    exit /b 1
)
echo [OK] Packages installed.

:: Generate icon
echo.
echo [3/5] Creating application icon...
python -c "
import struct, zlib, base64
# Minimal valid .ico file (16x16, blue microscope-themed)
# We generate a simple colored icon programmatically
try:
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (256,256), (26, 43, 74, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([40,40,216,216], fill=(0,140,200,255))
    draw.ellipse([80,80,176,176], fill=(26,43,74,255))
    draw.rectangle([118,80,138,200], fill=(255,255,255,255))
    draw.rectangle([80,118,176,138], fill=(255,255,255,255))
    import os
    os.makedirs('resources', exist_ok=True)
    img.save('resources/icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])
    print('Icon created.')
except Exception as e:
    print(f'Icon skipped ({e}) - using default.')
    import os
    os.makedirs('resources', exist_ok=True)
    open('resources/icon.ico','wb').close()
"
echo [OK] Icon step done.

:: Run PyInstaller
echo.
echo [4/5] Building executable (this takes 2-5 minutes)...
pyinstaller sem_grain_analyzer.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)
echo [OK] Executable built.

:: Create installer with NSIS if available, otherwise zip
echo.
echo [5/5] Packaging...

:: Check for NSIS
where makensis >nul 2>&1
if not errorlevel 1 (
    echo NSIS found - building installer...
    python create_nsis_script.py
    makensis installer.nsi
    echo [OK] Installer created: SEMGrainAnalyzer_Setup.exe
) else (
    echo NSIS not found - creating zip package instead...
    echo (Optional: Install NSIS from https://nsis.sourceforge.io for a proper installer)
    powershell -Command "Compress-Archive -Path 'dist\SEMGrainAnalyzer' -DestinationPath 'SEMGrainAnalyzer_Windows.zip' -Force"
    echo [OK] Package created: SEMGrainAnalyzer_Windows.zip
)

echo.
echo ============================================================
echo  BUILD COMPLETE!
echo ============================================================
echo.
echo The application is in: dist\SEMGrainAnalyzer\
echo Executable: dist\SEMGrainAnalyzer\SEMGrainAnalyzer.exe
if exist SEMGrainAnalyzer_Windows.zip echo Zip package: SEMGrainAnalyzer_Windows.zip
if exist SEMGrainAnalyzer_Setup.exe echo Installer: SEMGrainAnalyzer_Setup.exe
echo.
pause
