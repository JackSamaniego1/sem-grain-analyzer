# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Grain Analyzer v3
# Build: pyinstaller grain_analyzer.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(SPEC)))
from version import __version__

block_cipher = None

# GPU edition switch (see the CUDA block below). UPX must never touch CUDA
# DLLs (slow, and can corrupt their fat binaries).
_IS_CUDA = os.environ.get('GA_TORCH_FLAVOR', 'cpu').lower() == 'cuda'
_USE_UPX = not _IS_CUDA

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

# Batch 4 (D-39): the guided tour's 3 synthetic sample images
# (tools/make_tutorial_images.py); located via core.resources at run time.
tutorial_datas = [('assets/tutorial', 'assets/tutorial')] \
    if os.path.isdir('assets/tutorial') else []

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[
        # onnxruntime native DLLs for the offline info-bar OCR (RapidOCR)
        *collect_dynamic_libs('onnxruntime'),
    ],
    datas=[
        # RapidOCR config.yaml + bundled ONNX models (det/rec/cls)
        *collect_data_files('rapidocr_onnxruntime'),
        *collect_data_files('skimage'),
        *collect_data_files('scipy'),
        *collect_data_files('cv2'),
        *collect_data_files('qtawesome'),
        *collect_data_files('pptx'),
        *torch_datas,
        *sam_model,
        *license_datas,
        *tutorial_datas,
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
        'core.info_bar_ocr','rapidocr_onnxruntime','onnxruntime',
        'shapely','shapely.geometry','pyclipper','yaml','six',
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
    # tqdm (MPL-2.0) is declared by rapidocr but never imported; keep it out.
    excludes=['tqdm','napari','matplotlib','IPython','tkinter','_tkinter',
              'wx','PySide2','PyQt5','PyQt6','pandas',
              'PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick','PySide6.QtNetwork',
              'PySide6.QtNetworkAuth','PySide6.QtPositioning',
              'PySide6.QtLocation','PySide6.QtBluetooth',
              'PySide6.QtNfc','PySide6.QtSerialPort'],
    cipher=block_cipher,
)

# ---------------------------------------------------------------------------
# GPU (CUDA) edition support -- opt-in, the default build is unchanged.
#   set GA_TORCH_FLAVOR=cuda   (only when the build venv has a +cuXXX torch)
# Removes torch files that inference never needs. Verified on torch 2.14.0+cu126
# (RTX 4080 SUPER, SAM vit_b AutomaticMaskGenerator + conv/convT/SDPA/inv):
# these can go (~0.5 GB); everything else in torch/lib is a hard import of
# torch_cuda.dll / torch_cpu.dll or lazily loaded by cuDNN and MUST stay
# (cudnn_heuristic, cudnn_engines_runtime_compiled, cudnn_graph/ops/cnn,
# cublas/cublasLt, cusparse, cusolver, cufft, curand, nvrtc, nvJitLink, cupti,
# cudart). Do not trim further without re-running a GPU inference test.
# ---------------------------------------------------------------------------
if _IS_CUDA:
    _TRIM_DLLS = {
        'cusolvermg64_11.dll', 'cusolvermg64_12.dll',   # multi-GPU solver
        'cufftw64_11.dll', 'cufftw64_12.dll',           # FFTW-compat shim
        'nvperf_host.dll',                              # profiler backend
        'nvtoolsext64_1.dll',                           # NVTX profiling
        'cudnn_adv64_9.dll',                            # cuDNN RNN/attention, unused
        'libiompstubs5md.dll',
    }

    def _keep_cuda(entry):
        dest = entry[0].replace('\\', '/').lower()
        base = dest.rsplit('/', 1)[-1]
        if base in _TRIM_DLLS or base.endswith('.alt.dll'):
            return False
        if dest.startswith('torch/') and (base.endswith('.lib') or
                                          dest.startswith('torch/include/')):
            return False
        return True

    a.binaries = [e for e in a.binaries if _keep_cuda(e)]
    a.datas = [e for e in a.datas if _keep_cuda(e)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='GrainAnalyzer',
    debug=False, strip=False, upx=_USE_UPX,
    console=False,
    icon='resources/icon.ico',
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=_USE_UPX, name='GrainAnalyzer',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GrainAnalyzer.app',
        icon='resources/icon.icns',
        bundle_identifier='com.jacksamaniego.grainanalyzer',
        info_plist={'NSHighResolutionCapable': True, 'CFBundleShortVersionString': __version__},
    )
