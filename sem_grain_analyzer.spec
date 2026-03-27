# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SEM Grain Analyzer
# Build: pyinstaller sem_grain_analyzer.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        *collect_data_files('skimage'),
        *collect_data_files('scipy'),
        *collect_data_files('cv2'),
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
        'utils.excel_export',
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
    name='SEMGrainAnalyzer',
    debug=False, strip=False, upx=True,
    console=False,
    icon='resources/icon.ico',
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, name='SEMGrainAnalyzer',
)

import sys
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='SEMGrainAnalyzer.app',
        icon='resources/icon.icns',
        bundle_identifier='com.semtools.grainanalyzer',
        info_plist={'NSHighResolutionCapable': True, 'CFBundleShortVersionString': '1.0.0'},
    )
