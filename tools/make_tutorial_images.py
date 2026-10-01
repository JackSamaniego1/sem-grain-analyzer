"""
Generate the guided tour's sample images (batch 4, D-39).

    .venv\\Scripts\\python tools\\make_tutorial_images.py

Writes three synthetic SEM micrographs to ``assets/tutorial/``: a grey
polycrystal (the synthetic grain generator of ``ui/demo.py``) above a
JEOL-style black data bar -- a solid 10 µm scale bar with its label, then
magnification / kV / detector / WD on the second line -- so the app's
scan-area auto-detect, scale-bar finder and offline OCR all succeed on them.

Run once at development time; the PNGs are committed and bundled by
``grain_analyzer.spec``.  The installed app never generates or downloads
them.  ``--check`` re-runs the app's own detection on the written files.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "assets" / "tutorial"
WIDTH, HEIGHT = 1280, 960
BAR_H = 64                      # JEOL data bar: bottom 64 px
SCALE_UM = 10.0
PX_PER_UM = 16.0                # 10 µm bar = 160 px
#: (file name, seed, grain count, magnification text)
IMAGES = (
    ("tutorial_sample_1.png", 11, 210, "X 2,500"),
    ("tutorial_sample_2.png", 23, 240, "X 2,500"),
    ("tutorial_sample_3.png", 37, 270, "X 2,500"),
)

_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _font(size: int):
    for n in ("consolab.ttf", "courbd.ttf", "lucon.ttf", "DejaVuSansMono-Bold.ttf"):
        p = os.path.join(_FONT_DIR, n)
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render(seed: int, n_grains: int, mag_text: str) -> np.ndarray:
    """One 1280 x 960 greyscale micrograph with a JEOL-style data bar."""
    from ui.demo import synthetic_sem           # numpy / cv2 / scipy only
    fh = HEIGHT - BAR_H
    micro = synthetic_sem(h=fh, w=WIDTH, n_grains=n_grains, seed=seed, info_bar=False)[:, :, 0]
    img = Image.new("L", (WIDTH, HEIGHT), 0)
    img.paste(Image.fromarray(micro), (0, 0))
    d = ImageDraw.Draw(img)
    f = _font(22)
    y0 = fh
    y1, y2 = y0 + 6, y0 + 36
    bar_px = int(round(SCALE_UM * PX_PER_UM))
    bx, by = 640, y1 + 8
    d.rectangle([bx, by, bx + bar_px - 1, by + 8 - 1], fill=255)
    label = f"{SCALE_UM:g}\u00b5m"
    lx = bx + bar_px + 10
    d.text((lx, y1), label, font=f, fill=255)
    d.text((lx + int(d.textlength(label, font=f)) + 16, y1), "JEOL", font=f, fill=255)
    d.text((1110, y1), "9/30/2026", font=f, fill=255)
    x = 470
    for t in (mag_text, "15.0kV", "SEI", "SEM", "WD 10.0mm"):
        d.text((x, y2), t, font=f, fill=255)
        x += int(d.textlength(t, font=f)) + 26
    d.text((1150, y2), "10:24:{:02d}".format(seed % 60), font=f, fill=255)
    return np.asarray(img)


def write_all(out_dir: Path = OUT_DIR) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, seed, n, mag in IMAGES:
        p = out_dir / name
        Image.fromarray(render(seed, n, mag)).save(p, optimize=True)
        paths.append(p)
    return paths


def check(paths) -> bool:
    """Run the app's own auto-find (scan area + scale bar + OCR) on ``paths``."""
    import cv2

    from ui.app_state import setup_probe
    ok = True
    for p in paths:
        bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
        out = setup_probe(bgr, str(p), want_meta=False, want_ocr=True)
        info = out.get("info") or {}
        ocr = out.get("ocr") or {}
        px = out["bar_px"] / ocr["um"] if out["bar_px"] and ocr.get("um") else 0.0
        good = bool(info) and abs(out["bar_px"] - SCALE_UM * PX_PER_UM) <= 3 \
            and abs(float(ocr.get("um", 0)) - SCALE_UM) < 1e-6
        ok = ok and good
        print(f"{Path(p).name}: info bar={'yes' if info else 'NO'}  bar={out['bar_px']:.0f} px  "
              f"label={ocr.get('value')} {ocr.get('unit')}  -> {px:.3f} px/um  "
              f"{'OK' if good else 'FAILED'}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true", help="verify with the app's auto-find")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    paths = write_all(Path(a.out))
    total = sum(p.stat().st_size for p in paths)
    print(f"wrote {len(paths)} images, {total / 1e6:.2f} MB, to {a.out}")
    if a.check:
        return 0 if check(paths) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
