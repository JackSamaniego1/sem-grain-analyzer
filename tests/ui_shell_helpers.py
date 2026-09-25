"""Shared helpers for the v3 shell tests (tmp workspace, synthetic sessions).

Every test redirects LOCALAPPDATA and the workspace root into ``tmp_path`` —
nothing is ever written to the real settings folder or Documents.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import cv2
import numpy as np

from tests.conftest import make_mosaic


def mosaic_png(path: Path, seed: int = 7, black: bool = False, size: int = 224,
               n_grains: int = 36) -> str:
    gray, _ = make_mosaic(h=size, w=size, n_grains=n_grains, seed=seed)
    if black:
        gray = gray.copy()
        gray[60:130, 80:170] = 0          # a void / masked region
    cv2.imwrite(str(path), np.repeat(gray[:, :, None], 3, axis=2))
    return str(path)


def make_session(root: Path, n: int = 2, label: str = "Session A", project: str = "Proj",
                 sample: str = "S-1", lot: str = "L-1", black: bool = False,
                 src_dir: Path = None) -> Path:
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace

    ws = Workspace(root)
    try:
        pp = ws.resolve_project(project)
    except FileNotFoundError:
        pp = ws.create_project(project)
    try:
        sp = ws.resolve_sample(pp, sample)
    except FileNotFoundError:
        sp = ws.create_sample(pp, sample, material="Steel")
    try:
        lp = ws.resolve_lot(pp, sp, lot)
    except FileNotFoundError:
        lp = ws.create_lot(pp, sp, lot)
    src_dir = src_dir or (root.parent / "src")
    src_dir.mkdir(parents=True, exist_ok=True)
    paths: List[str] = [mosaic_png(src_dir / f"{label.replace(' ', '_')}_{i}.png", seed=7 + i,
                                   black=black)
                        for i in range(n)]
    ref = save_session(lp, {"operator": "Tester"}, [ImageEntry(source_path=p) for p in paths],
                       label=label, catalog=Catalog(root))
    return Path(ref.path)


def confirm_setup(shell, qtbot, px_per_um: float = 2.0, timeout: int = 60000) -> None:
    """UX-02 gate: give an uncalibrated session a scale (as the operator
    would with Set scale bar) and click "Auto-find scan area & scale bar
    (all images)", then wait until every image is ready for analysis."""
    st = shell.state
    qtbot.waitUntil(lambda: not st.is_loading(), timeout=timeout)
    if px_per_um and all(st.px_for(im) <= 0 for im in st.images()):
        st.set_calibration(px_per_um)
    shell.analyze.setup_tile.btn_auto.click()
    qtbot.waitUntil(lambda: not st.is_setting_up(), timeout=timeout)
    assert all(st.setup_ready(im) for im in st.images()), \
        [st.setup_issues(im) for im in st.images()]
