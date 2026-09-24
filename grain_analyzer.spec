# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Grain Analyzer v3
# Build: pyinstaller grain_analyzer.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(SPEC)))
from version import __version__

block_cipher = None

# Collect torch dynamic libs and data
torch_datas = collect_data_files('torch', include_py_files=True)
torch_hidden = collect_submodules('torch')
tv_hidden = collect_submodules('torchvision')
sam_hidden = collect_submodules('segment_anything')

# SAM model checkpoint
sam_model = [('models/sam_vit_b_01ec64.pth', 'models')] \
    if os.path.isfile('models/sam_vit_b_01ec64.pth') else []

# Third-party licence notices bundled into the installed app so the
# offline install carries its own attribution (HARD CONSTRAINT: no
# network access, so we can't link out to licence pages at runtime).
license_datas = [(p, '.') for p in ('LICENSE.txt', 'THIRD_PARTY_LICENSES.txt')
                  if os.path.isfile(p)]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        *collect_data_files('skimage'),
        *collect_data_files('scipy'),
        *collect_data_files('cv2'),
        *collect_data_files('qtawesome'),
        *collect_data_files('pptx'),
        *torch_datas,
        *sam_model,
        *license_datas,
    ],
    hiddenimports=[
        'skimage.filters._gaussian','skimage.filters.rank',
        'skimage.segmentation._watershed','skimage.feature.peak',
        'skimage.measure._regionprops','skimage.morphology.binary',
        'scipy.ndimage','scipy.ndimage._morphology',
        'scipy.special._ufuncs','scipy._lib.messagestream',
        'cv2','openpyxl','openpyxl.chart','openpyxl.styles',
        'xlsxwriter','pptx','qtawesome',
        'PySide6.QtCore','PySide6.QtGui','PySide6.QtWidgets',
        'core.grain_detector','core.scale_bar','core.offline_guard',
        'core.infobar','core.sem_metadata',
        'ui.app_shell','ui.calibration_dialog','ui.scan_area_dialog',
        'reports.excel_renderer','reports.pptx_renderer',
        'segment_anything',
        *torch_hidden,
        *tv_hidden,
        *sam_hidden,
    ],
    hookspath=[],
    runtime_hooks=[],
    # PyQt5/PyQt6/PySide2 are excluded: this app is PySide6-only (LGPL, no
    # licence to buy — HARD CONSTRAINT). QtNetwork/QtWebEngine are excluded
    # because nothing in this codebase imports them and bundling them would
    # be a large, pointless attack surface for an app that must never touch
    # the network (HARD CONSTRAINT: offline & private).
    excludes=['napari','matplotlib','IPython','tkinter','_tkinter',
              'wx','PySide2','PyQt5','PyQt6','pandas',
              'PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick','PySide6.QtNetwork',
              'PySide6.QtNetworkAuth','PySide6.QtPositioning',
              'PySide6.QtLocation','PySide6.QtBluetooth',
              'PySide6.QtNfc','PySide6.QtSerialPort'],
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

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GrainAnalyzer.app',
        icon='resources/icon.icns',
        bundle_identifier='com.jacksamaniego.grainanalyzer',
        info_plist={'NSHighResolutionCapable': True, 'CFBundleShortVersionString': __version__},
    )
