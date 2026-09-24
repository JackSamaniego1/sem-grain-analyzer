"""
Demo workspace + offscreen screenshots of the v3 shell.

    python -m ui.demo --capture scratch/ui     # builds a temp workspace, renders PNGs

Everything is written into a temporary directory (workspace AND settings —
``LOCALAPPDATA`` is redirected) which is deleted afterwards; the user's real
settings and Documents folder are never touched.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List

import cv2
import numpy as np


# ======================================================================
# Synthetic SEM-like micrographs
# ======================================================================

def synthetic_sem(h: int = 600, w: int = 800, n_grains: int = 140, seed: int = 1,
                  info_bar: bool = True, void: bool = False) -> np.ndarray:
    """Voronoi grain mosaic with grooves, shading, noise and an SEM info bar."""
    from scipy.spatial import cKDTree
    rng = np.random.default_rng(seed)
    bar = 56 if info_bar else 0
    fh = h - bar
    pts = np.stack([rng.uniform(0, fh, n_grains), rng.uniform(0, w, n_grains)], 1)
    yy, xx = np.mgrid[0:fh, 0:w]
    _, lab = cKDTree(pts).query(np.stack([yy.ravel(), xx.ravel()], 1))
    lab = lab.reshape(fh, w)
    fills = rng.uniform(105, 205, n_grains)
    img = fills[lab].astype(np.float32)
    # in-grain texture (orientation contrast)
    tex = cv2.GaussianBlur(rng.normal(0, 1, (fh, w)).astype(np.float32), (0, 0), 6) * 9
    img += tex
    edge = np.zeros((fh, w), bool)
    edge[:, :-1] |= lab[:, :-1] != lab[:, 1:]
    edge[:-1, :] |= lab[:-1, :] != lab[1:, :]
    edge = cv2.dilate(edge.astype(np.uint8), np.ones((2, 2), np.uint8)).astype(bool)
    img[edge] = 38
    img = cv2.GaussianBlur(img, (0, 0), 0.8)
    shade = np.linspace(-12, 10, w)[None, :] + np.linspace(8, -8, fh)[:, None]
    img += shade + rng.normal(0, 5, (fh, w))
    if void:
        cy, cx = int(fh * 0.35), int(w * 0.7)
        cv2.ellipse(img, (cx, cy), (70, 48), 20, 0, 360, 0, -1)
    img = np.clip(img, 0, 255).astype(np.uint8)
    full = np.zeros((h, w), np.uint8)
    full[:fh] = img
    if info_bar:
        full[fh:] = 6
        f = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(full, "SE2   15.00 kV   WD 8.6 mm   Mag 500 x", (14, fh + 34), f, 0.55, 225, 1,
                    cv2.LINE_AA)
        cv2.line(full, (w - 190, fh + 30), (w - 40, fh + 30), 235, 3)
        cv2.putText(full, "20 um", (w - 140, fh + 22), f, 0.5, 235, 1, cv2.LINE_AA)
    return cv2.cvtColor(full, cv2.COLOR_GRAY2BGR)


def build_demo_workspace(root: Path, img_dir: Path, analyse: bool = True) -> dict:
    """Two projects, samples, lots and sessions — some analysed."""
    from core.grain_detector import DetectionParams
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace
    from ui.app_state import params_to_dict
    from ui.filtering import default_options, filter_image, options_to_dict
    from ui.workers import analyze_image

    ws = Workspace(root)
    cat = Catalog(root)
    img_dir.mkdir(parents=True, exist_ok=True)
    out = {}

    def images(prefix: str, n: int, seed0: int, void_at: int = -1) -> List[str]:
        paths = []
        for i in range(n):
            p = img_dir / f"{prefix}_{i + 1:02d}.png"
            cv2.imwrite(str(p), synthetic_sem(seed=seed0 + i, n_grains=110 + 25 * i,
                                              void=(i == void_at)))
            paths.append(str(p))
        return paths

    params = DetectionParams(detection_mode="boundary")
    ppu = 2.35  # px/µm (20 µm bar ≈ 47 px … demo only)
    scan = (0, 0, 800, 544)

    def session(lot: Path, label: str, operator: str, paths: List[str], n_analyse: int,
                notes: str = "") -> Path:
        entries = []
        pf_images = {}
        opts = default_options(scan)
        opts.exclude_low_contrast = True
        opts.exclude_touching_invalid = True
        for i, p in enumerate(paths):
            bgr = cv2.imread(p)
            res = None
            if analyse and i < n_analyse:
                raw = analyze_image(bgr, ppu, params, scan, draw_overlay=False)
                f = filter_image(raw, bgr, opts, frozenset(), params)
                res = f["result"]
                res.label_image = raw.label_image
                pf_images[Path(p).name] = {"excluded": {str(k): v for k, v in f["excluded"].items()},
                                           "manual": [], "options": None}
            entries.append(ImageEntry(source_path=p, result=res, px_per_um=ppu,
                                      scan_rect=scan))
        dp = params_to_dict(params)
        dp["post_filters"] = {"options": options_to_dict(opts), "images": pf_images}
        ref = save_session(lot, {"operator": operator, "instrument": "Zeiss Sigma 300",
                                 "magnification": "500×", "accelerating_voltage_kv": 15.0,
                                 "working_distance_mm": 8.6, "px_per_um": ppu,
                                 "scan_rect": list(scan), "detection_params": dp,
                                 "detector_mode": "boundary", "notes": notes},
                           entries, label=label, catalog=cat)
        return ref.path

    p1 = ws.create_project("Alloy 718 qualification", customer="Turbine components programme",
                           description="Grain size acceptance for forged discs")
    s1 = ws.create_sample(p1, "S-014", material="Inconel 718", alloy_grade="AMS 5662",
                          heat_treatment="Solution + aged")
    l1 = ws.create_lot(p1, s1, "2026-0917-B", supplier="Special Metals",
                       received_date="2026-09-17", notes="Forging lot, rim location")
    out["session_main"] = session(l1, "Transverse section 500×", "A. Ramirez",
                                  images("s014_t", 3, 10, void_at=1), 3,
                                  "Etched, Kalling's No. 2")
    out["session_partial"] = session(l1, "Longitudinal section 500×", "J. Samaniego",
                                     images("s014_l", 3, 20), 1)
    l1b = ws.create_lot(p1, s1, "2026-0921-A", supplier="Special Metals",
                        received_date="2026-09-21")
    session(l1b, "Surface 1000×", "A. Ramirez", images("s014_s", 2, 30), 0)
    s2 = ws.create_sample(p1, "S-015", material="Inconel 718", alloy_grade="AMS 5663",
                          heat_treatment="Direct aged")
    ws.create_lot(p1, s2, "2026-0930-C", supplier="ATI")
    p2 = ws.create_project("Ti-6Al-4V AM study", customer="Additive manufacturing R&D",
                           description="Prior-β grain size after HIP")
    s3 = ws.create_sample(p2, "AM-B3", material="Ti-6Al-4V", alloy_grade="Grade 5",
                          heat_treatment="HIP 920 °C")
    l3 = ws.create_lot(p2, s3, "B3-L1", supplier="In-house build 3")
    session(l3, "As-built vs HIP", "K. Chen", images("am_b3", 2, 40), 2)
    out["lot_main"] = l1
    out["project"] = p1
    out["images"] = images("new", 3, 60)
    return out


# ======================================================================
# Capture
# ======================================================================

def _pump(app, ms: int) -> None:
    from PySide6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        app.processEvents()


def _wait(app, cond, ms: int = 30000) -> bool:
    from PySide6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        app.processEvents()
        if cond():
            return True
    return False


def capture(out_dir: str) -> List[str]:
    tmp = Path(tempfile.mkdtemp(prefix="grain_demo_"))
    os.environ["LOCALAPPDATA"] = str(tmp / "appdata")
    saved: List[str] = []
    try:
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest

        app = QApplication.instance() or QApplication(sys.argv[:1])
        from data.models import AppSettings
        from data.settings import save_settings
        from ui.app_shell import AppShell
        from ui.app_state import AppState, NodeRef
        from ui.design.theme import apply_theme
        from ui.dialogs.new_session_wizard import NewSessionWizard

        root = tmp / "Projects"
        demo = build_demo_workspace(root, tmp / "src")
        save_settings(AppSettings(workspace_root=str(root), operator="J. Samaniego", theme="dark"))
        os.makedirs(out_dir, exist_ok=True)

        apply_theme(app, "dark")
        state = AppState()
        win = AppShell(state, probe_device=False)
        win.resize(1600, 960)
        win.show()
        _pump(app, 500)
        win.chip_device.set_text("CPU")

        # ---- Projects (dark + light)
        win.go("projects")
        state.set_node(NodeRef("lot", demo["lot_main"]))
        _wait(app, lambda: not win.projects.is_loading() and win.projects.cards())
        _pump(app, 900)
        cards = win.projects.cards()
        if cards:
            win.projects._card_clicked(cards[0])
        _pump(app, 400)
        p = os.path.join(out_dir, "app_projects_dark.png")
        win.grab().save(p)
        saved.append(p)
        win.set_theme("light")
        _pump(app, 700)
        p = os.path.join(out_dir, "app_projects_light.png")
        win.grab().save(p)
        saved.append(p)
        win.set_theme("dark")
        _pump(app, 500)

        # ---- Wizard
        wiz = NewSessionWizard(state, prefill={"project_path": demo["project"]}, parent=win)
        wiz.fill(project="Alloy 718 qualification", sample="S-014", lot="2026-0917-B",
                 label_text="Transverse section 1000×", operator="J. Samaniego",
                 instrument="Zeiss Sigma 300", magnification="1000×", kv=15.0, wd=8.6)
        wiz.add_images(demo["images"])
        wiz.show()
        wiz._go(3)
        _pump(app, 500)
        wiz._go(4)
        _pump(app, 700)
        p = os.path.join(out_dir, "app_wizard.png")
        wiz.grab().save(p)
        saved.append(p)
        wiz.close()

        # ---- Analyze (with results)
        win.open_session(demo["session_main"], prefer="analyze")
        _wait(app, lambda: state.session is not None)
        _pump(app, 900)
        imgs = state.images()
        state.set_current_image(imgs[1].uid)
        _pump(app, 300)
        win.analyze.params.sec_mode.set_expanded(True, animate=False)
        win.analyze.canvas.set_view("overlay")
        for c in (win.analyze.st_images, win.analyze.st_grains, win.analyze.st_diam,
                  win.analyze.st_g):
            c.count_up()
        _pump(app, 1200)
        p = os.path.join(out_dir, "app_analyze.png")
        win.grab().save(p)
        saved.append(p)

        # ---- Review with filters + manual removal + selection + hover
        win.go("review")
        _pump(app, 600)
        im = state.current_image()
        opts = state.filter_options()
        opts.exclude_border = True
        opts.exclude_low_contrast = True
        opts.exclude_touching_invalid = True
        state.set_filter_options(opts)
        _wait(app, lambda: not state.is_filtering(), 20000)
        _pump(app, 1500)
        _wait(app, lambda: not state.is_filtering(), 20000)
        im = state.current_image()
        kept = [g for g in im.result.grains][:40]
        if len(kept) > 6:
            state.delete_grains(im.uid, [kept[3].grain_id, kept[9].grain_id])
            _wait(app, lambda: not state.is_filtering(), 20000)
            _pump(app, 1400)
            _wait(app, lambda: not state.is_filtering(), 20000)
        im = state.current_image()
        cv_ = win.review.canvas
        if im.result.grains:
            g = sorted(im.result.grains, key=lambda g: -g.area_px)[2]
            cv_.select([g.grain_id])
            win.review._on_canvas_selection([g.grain_id])
            win.review.tabs.setCurrentIndex(0)
            ex = [gid for gid in im.excluded if gid in {x.grain_id for x in im.raw.grains}]
            if ex:
                gg = {x.grain_id: x for x in im.raw.grains}[ex[0]]
                s, off = cv_._scale, cv_._offset
                pos = QPointF(off.x() + gg.centroid_x * s, off.y() + gg.centroid_y * s)
                QTest.mouseMove(cv_, pos.toPoint())
                cv_._mouse = pos
                cv_._hover_id = gg.grain_id
                cv_._hover_path = cv_._contour_path(cv_._labels, gg)
        for c in (win.review.c_count, win.review.c_area, win.review.c_diam, win.review.c_cov,
                  win.review.c_inv, win.review.c_g):
            c.count_up()
        _pump(app, 1300)
        p = os.path.join(out_dir, "app_review.png")
        win.grab().save(p)
        saved.append(p)
        for key in ("settings", "reports"):
            win.go(key)
            _pump(app, 500)
            p = os.path.join(out_dir, f"app_{key}.png")
            win.grab().save(p)
            saved.append(p)
        state.flush()
        win.close()
        _pump(app, 200)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return saved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", metavar="DIR", required=True)
    args = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from core import offline_guard
    offline_guard.install()
    for p in capture(args.capture):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
