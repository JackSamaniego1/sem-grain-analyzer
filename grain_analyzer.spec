# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Grain Analyzer v2.3
# Build: pyinstaller grain_analyzer.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os

block_cipher = None

# Collect torch dynamic libs and data
torch_datas = collect_data_files('torch', include_py_files=True)
torch_hidden = collect_submodules('torch')
tv_hidden = collect_submodules('torchvision')
sam_hidden = collect_submodules('segment_anything')

# SAM model checkpoint
sam_model = [('models/sam_vit_b_01ec64.pth', 'models')] \
    if os.path.isfile('models/sam_vit_b_01ec64.pth') else []

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        *collect_data_files('skimage'),
        *collect_data_files('scipy'),
        *collect_data_files('cv2'),
        *torch_datas,
        *sam_model,
    ],
    hiddenimports=[
        'skimage.filters._gaussian','skimage.filters.rank',
        'skimage.segmentation._watershed','skimage.feature.peak',
        'skimage.measure._regionprops','skimage.morphology.binary',
        'scipy.ndimage','scipy.ndimage._morphology',
        'scipy.special._ufuncs','scipy._lib.messagestream',
        'cv2','openpyxl','openpyxl.chart','openpyxl.styles',
        'PyQt6.QtCore','PyQt6.QtGui','PyQt6.QtWidgets',
        'core.grain_detector','core.scale_bar',
        'ui.main_window','ui.image_canvas','ui.settings_panel',
        'ui.results_panel','ui.calibration_dialog','ui.theme',
        'ui.scan_area_dialog','ui.analysis_progress_dialog',
        'utils.excel_export',
        'segment_anything',
        *torch_hidden,
        *tv_hidden,
        *sam_hidden,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['napari','matplotlib','IPython','tkinter','_tkinter',
              'wx','PySide2','PySide6','PyQt5','pandas'],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='GrainAnalyzer',
    debug=False, strip=False, upx=True,
    console=False,
    icon='resources/icon.ico',
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, name='GrainAnalyzer',
)

import sys
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GrainAnalyzer.app',
        icon='resources/icon.icns',
        bundle_identifier='com.jacksamaniego.grainanalyzer',
        info_plist={'NSHighResolutionCapable': True, 'CFBundleShortVersionString': '2.3'},
    )
