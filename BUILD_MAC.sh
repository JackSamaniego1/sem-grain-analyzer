#!/bin/bash
# ============================================================
# SEM Grain Analyzer - macOS Build Script
# Run in Terminal: bash BUILD_MAC.sh
# Requires: Python 3.10+ (brew install python or python.org)
# ============================================================

set -e
echo "============================================================"
echo " SEM Grain Analyzer - macOS Build"
echo "============================================================"

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found. Install from https://python.org"
    exit 1
fi
echo "[OK] $(python3 --version)"

echo "[1/4] Creating virtual environment..."
rm -rf build_env
python3 -m venv build_env
source build_env/bin/activate

echo "[2/4] Installing packages..."
pip install --upgrade pip -q
pip install pyinstaller PyQt6 opencv-python scikit-image scipy numpy openpyxl Pillow torch torchvision segment-anything

echo "[3/4] Creating icon..."
python3 -c "
try:
    from PIL import Image, ImageDraw
    import os
    img = Image.new('RGBA', (512,512), (26,43,74,255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([60,60,452,452], fill=(0,140,200,255))
    draw.ellipse([130,130,382,382], fill=(26,43,74,255))
    draw.rectangle([236,100,276,412], fill=(255,255,255,255))
    draw.rectangle([100,236,412,276], fill=(255,255,255,255))
    os.makedirs('resources', exist_ok=True)
    img.save('resources/icon.icns')
    img.save('resources/icon.ico', format='ICO')
    print('Icons created.')
except Exception as e:
    print(f'Icon skipped: {e}')
    import os; os.makedirs('resources', exist_ok=True)
    open('resources/icon.ico','wb').close()
    open('resources/icon.icns','wb').close()
"

echo "[4/4] Building application..."
pyinstaller grain_analyzer.spec --clean --noconfirm

echo ""
echo "============================================================"
echo " BUILD COMPLETE!"
echo "============================================================"
echo "App bundle: dist/SEMGrainAnalyzer.app"
echo ""
echo "To create a distributable DMG:"
echo "  hdiutil create -volname 'SEM Grain Analyzer' -srcfolder dist/SEMGrainAnalyzer.app -ov -format UDZO SEMGrainAnalyzer.dmg"
