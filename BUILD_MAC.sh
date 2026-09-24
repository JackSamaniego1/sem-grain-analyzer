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

echo "[1/5] Creating virtual environment..."
rm -rf build_env
python3 -m venv build_env
source build_env/bin/activate

echo "[2/5] Installing packages..."
pip install --upgrade pip -q
pip install pyinstaller PySide6 opencv-python scikit-image scipy numpy openpyxl xlsxwriter python-pptx qtawesome Pillow segment-anything
# CPU-only torch wheel keeps the installer smaller; this app never needs a
# GPU at runtime.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

echo "[3/5] Downloading SAM model checkpoint (~375MB)..."
mkdir -p models
if [ ! -f models/sam_vit_b_01ec64.pth ]; then
    curl -L -o models/sam_vit_b_01ec64.pth https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
    echo "SAM model downloaded."
else
    echo "SAM model already exists, skipping."
fi
if [ ! -f models/sam_vit_b_01ec64.pth ]; then
    echo "ERROR: models/sam_vit_b_01ec64.pth is missing after the download step."
    exit 1
fi

echo "[4/5] Creating icon..."
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

echo "[5/5] Building application..."
pyinstaller grain_analyzer.spec --clean --noconfirm

echo ""
echo "============================================================"
echo " BUILD COMPLETE!"
echo "============================================================"
echo "App bundle: dist/GrainAnalyzer.app"
echo ""
echo "To create a distributable DMG:"
echo "  hdiutil create -volname 'Grain Analyzer' -srcfolder dist/GrainAnalyzer.app -ov -format UDZO GrainAnalyzer.dmg"
