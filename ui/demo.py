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
    """The lab's own structure (HIER-01 default for a new workspace):
    Job # › Part Number › Lot, images stored directly in each lot.

    Job 24-117 "Acme Aerospace" › Part 7718-A "Turbine disk forging, Alloy
    718" › Lot L-44A (heat HT-90211) is the fully analysed showcase; other
    lots are partly analysed or empty.  No scan area is set — the SEM info
    bar is excluded automatically (DET-05)."""
    from core.grain_detector import DetectionParams
    from data.catalog import Catalog
    from data.models import ImageEntry
    from data.session_io import save_session
    from data.workspace import Workspace
    from ui.app_state import params_to_dict
    from ui.filtering import default_options, filter_image, options_to_dict
    from ui.workers import analyze_image

    ws = Workspace(root)
    assert ws.profile.images_location == "lot"
    cat = Catalog(root)
    img_dir.mkdir(parents=True, exist_ok=True)
    out = {}

    def images(prefix: str, n: int, seed0: int, void_at: int = -1) -> List[str]:
        paths = []
        for i in range(n):
            p = img_dir / f"{prefix}_{i + 1:04d}.tif"
            cv2.imwrite(str(p), synthetic_sem(seed=seed0 + i, n_grains=110 + 25 * i,
                                              void=(i == void_at)))
            paths.append(str(p))
        return paths

    params = DetectionParams(detection_mode="boundary")
    ppu = 2.35  # px/µm (20 µm bar ≈ 47 px … demo only)

    def record(lot: Path, operator: str, paths: List[str], n_analyse: int,
               notes: str = "") -> Path:
        entries = []
        opts = default_options(None)
        opts.exclude_low_contrast = True
        opts.exclude_touching_invalid = True
        for i, p in enumerate(paths):
            bgr = cv2.imread(p)
            res = None
            if analyse and i < n_analyse:
                raw = analyze_image(bgr, ppu, params, None, draw_overlay=False)
                f = filter_image(raw, bgr, opts, frozenset(), params)
                res = f["result"]
                res.label_image = raw.label_image     # every raw grain; filters re-derive
            entries.append(ImageEntry(source_path=p, result=res))
        ref = save_session(lot, {"operator": operator, "instrument": "Zeiss Sigma 300",
                                 "filters": options_to_dict(opts),
                                 "magnification": "500×", "accelerating_voltage_kv": 15.0,
                                 "working_distance_mm": 8.6, "px_per_um": ppu,
                                 "detection_params": params_to_dict(params),
                                 "detector_mode": "boundary", "notes": notes},
                           entries, in_place=True, catalog=cat)
        return ref.path

    j1 = ws.create_project("24-117", customer="Acme Aerospace", po_number="PO-5521")
    p1 = ws.create_sample(j1, "7718-A", part_description="Turbine disk forging",
                          material_alloy="Alloy 718", drawing_revision="C")
    l1 = ws.create_lot(j1, p1, "L-44A", heat_number="HT-90211", supplier="Special Metals",
                       received_date="2026-09-17", quantity="12")
    out["session_main"] = record(l1, "A. Ramirez", images("SEM", 3, 10, void_at=1), 3,
                                 "Etched, Kalling's No. 2 — rim location")
    l2 = ws.create_lot(j1, p1, "L-44B", heat_number="HT-90214", supplier="Special Metals",
                       received_date="2026-09-21", quantity="8")
    out["session_partial"] = record(l2, "J. Samaniego", images("SEM_B", 3, 20), 1)
    p2 = ws.create_sample(j1, "7718-C", part_description="Compressor ring",
                          material_alloy="Alloy 718", drawing_revision="A")
    ws.create_lot(j1, p2, "L-51", heat_number="HT-90302", supplier="ATI")
    j2 = ws.create_project("24-121", customer="Northwind Turbines", po_number="PO-7730")
    p3 = ws.create_sample(j2, "TI-5521", part_description="AM bracket",
                          material_alloy="Ti-6Al-4V", drawing_revision="B")
    l3 = ws.create_lot(j2, p3, "B3-L1", heat_number="AM-B3", supplier="In-house build 3")
    record(l3, "K. Chen", images("AM", 2, 40), 2)
    out["lot_main"] = l1
    out["project"] = j1
    out["images"] = images("NEW", 3, 60)
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
        wiz.fill(project="24-117", sample="7718-A", lot="L-44C", heat_number="HT-90388",
                 supplier="Special Metals", received_date="2026-09-24", quantity="6",
                 operator="J. Samaniego", instrument="Zeiss Sigma 300", magnification="1000×",
                 kv=15.0, wd=8.6)
        wiz.add_images(demo["images"])
        wiz.show()
        last = len(wiz.step_titles()) - 1
        wiz._go(last - 1)
        _pump(app, 700)
        p = os.path.join(out_dir, "app_wizard_lot.png")
        wiz.grab().save(p)
        saved.append(p)
        wiz._go(last)
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
        win.analyze.sec_scan.set_expanded(True, animate=False)
        cur = state.current_image()
        from core.scale_bar import find_scale_bar_line
        bar = find_scale_bar_line(cur.image_bgr)
        # as the Zeiss TIFF tag would report it: the printed 20 µm bar
        cur.cal_suggestion = ((bar["length_px"] if bar else 150) / 20.0, "Zeiss", "high")
        state.sem_metadata_ready.emit(cur.uid)
        win.analyze.canvas.set_view("overlay")
        for c in (win.analyze.st_images, win.analyze.st_grains, win.analyze.st_diam,
                  win.analyze.st_g):
            c.count_up()
        _pump(app, 1200)
        p = os.path.join(out_dir, "app_analyze.png")
        win.grab().save(p)
        saved.append(p)
        # side panel: metadata scale row + info-bar chip (DET-05 / INN-05)
        win.analyze.params.sec_mode.set_expanded(False, animate=False)
        _pump(app, 400)
        p = os.path.join(out_dir, "app_analyze_side.png")
        win.analyze.params.grab().save(p)
        saved.append(p)

        # ---- Calibration dialog: scale bar found, length from metadata
        from ui.calibration_dialog import CalibrationDialog, suggest_bar_length_um
        cal = CalibrationDialog(cur.image_bgr, parent=win)
        cal.prefill(bar, suggest_bar_length_um(bar["length_px"], cur.cal_suggestion[0])
                    if bar else None)
        cal.show()
        _pump(app, 600)
        p = os.path.join(out_dir, "app_calibration_prefill.png")
        cal.grab().save(p)
        saved.append(p)
        cal.close()

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
        win.go("settings")
        _pump(app, 500)
        p = os.path.join(out_dir, "app_settings.png")
        win.grab().save(p)
        saved.append(p)
        card = win.settings_page.naming
        sc = card.parentWidget()
        while sc is not None and not hasattr(sc, "ensureWidgetVisible"):
            sc = sc.parentWidget()
        if sc is not None:
            sc.ensureWidgetVisible(card, 0, 0)
        card.levels[2].table.selectRow(0)
        _pump(app, 600)
        p = os.path.join(out_dir, "app_settings_naming_page.png")
        win.grab().save(p)
        saved.append(p)
        p = os.path.join(out_dir, "app_settings_naming.png")
        card.grab().save(p)
        saved.append(p)
        # rename preview: the folder template now adds the customer to the job
        from ui.dialogs.rename_folders_dialog import RenameFoldersDialog
        prof = state.profile
        prof.levels[0].folder_template = "{id} - {customer}"
        state.set_profile(prof)
        dlg = RenameFoldersDialog(state, win)
        dlg.show()
        _wait(app, lambda: dlg.tree.topLevelItemCount() > 0, 10000)
        _pump(app, 600)
        p = os.path.join(out_dir, "app_rename_folders.png")
        dlg.grab().save(p)
        saved.append(p)
        dlg.close()
        prof.levels[0].folder_template = "{id}"
        state.set_profile(prof)
        _pump(app, 300)

        # ---- Reports: overview table, per-image slide with caption, light theme
        rp = win.reports
        win.go("reports")
        _pump(app, 400)
        rp.build_from_session()
        _wait(app, lambda: rp.model is not None and not rp.is_busy(), 60000)
        _pump(app, 600)
        rp.inspector.org.setText("Metallurgy Laboratory")
        rp.inspector.org.textEdited.emit("Metallurgy Laboratory")
        rp.select(("section", "overview_table"))
        _pump(app, 700)
        p = os.path.join(out_dir, "app_reports.png")
        win.grab().save(p)
        saved.append(p)
        img = sorted(rp.model.images, key=lambda i: i.order)[0]
        rp.select(("image", img.id))
        _pump(app, 500)
        prev = rp.current_preview()
        prev.caption.setText("Transverse section, rim location — Kalling's No. 2 etch, 500×")
        prev.notes.setPlainText("Uniform equiaxed grains; no duplex structure observed.")
        prev.caption.setFocus()
        _pump(app, 700)
        p = os.path.join(out_dir, "app_reports_image.png")
        win.grab().save(p)
        saved.append(p)
        win.set_theme("light")
        rp.select(("section", "overview_table"))
        _pump(app, 900)
        p = os.path.join(out_dir, "app_reports_light.png")
        win.grab().save(p)
        saved.append(p)
        win.set_theme("dark")
        _pump(app, 300)
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
