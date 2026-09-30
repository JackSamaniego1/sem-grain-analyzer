"""Synthetic SEM info-bar renderers for the OCR tests (UPDATE 4 item 4).

Reproduces the two real layouts the lab uses:

* ``render_jeol``   — layout A: JEOL 1280x1024, black bar = bottom 64 px,
  white bold monospace text on two lines, small solid scale-bar rectangle
  with the label (``"100nm"``) directly to its right.
* ``render_thermo`` — layout B: Thermo Fisher / Phenom 1080x717, black bar
  = bottom 42 px, long thin scale line with end ticks and the label
  (``"15 µm"``) centred below it, then label/value column pairs.

Each returns ``(bgr_image, info)`` where ``info`` holds the drawn scale-bar
rectangle (full-frame ``(x, y, w, h)``) and the bar height.  Fonts come from
the Windows font folder when present, else Pillow's built-in scalable font,
so the fixtures render on any CI runner.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _font(names, size):
    for n in names:
        p = os.path.join(_FONT_DIR, n)
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # very old Pillow
        return ImageFont.load_default()


def _micrograph(w, h, seed=3):
    """Grain-like gray texture (deterministic)."""
    rng = np.random.default_rng(seed)
    small = rng.integers(60, 200, size=(h // 24 + 2, w // 24 + 2)).astype(np.uint8)
    img = np.array(Image.fromarray(small).resize((w, h), Image.NEAREST))
    noise = rng.normal(0, 8, size=(h, w))
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def _to_bgr(pil):
    rgb = np.asarray(pil.convert("RGB"))
    return np.ascontiguousarray(rgb[:, :, ::-1])


def render_jeol(scale_text="100nm", bar_px=30, bar_side="left",
                mag_text="X 30,000", kv_text="7.0kV", wd_text="WD 9.7mm",
                det_text="LEI", date_text="9/14/2026", time_text="13:42:09",
                width=1280, height=1024, bar_h=64, extra_line1=None):
    """Layout A.  ``bar_side="left"`` draws the bar left of the label (the
    real JEOL layout); ``"right"`` puts the label first."""
    img = Image.new("L", (width, height), 0)
    img.paste(Image.fromarray(_micrograph(width, height - bar_h)), (0, 0))
    d = ImageDraw.Draw(img)
    f = _font(["consolab.ttf", "courbd.ttf", "lucon.ttf"], 22)
    y0 = height - bar_h
    y1, y2 = y0 + 6, y0 + 36                 # text lines
    # line 1
    lbl_w = int(d.textlength(scale_text, font=f))
    if bar_side == "left":
        bx = 680
        lx = bx + bar_px + 8
    else:
        lx = 640
        bx = lx + lbl_w + 10
    by = y1 + 8
    d.rectangle([bx, by, bx + bar_px - 1, by + 7], fill=255)
    d.text((lx, y1), scale_text, font=f, fill=255)
    right = max(bx + bar_px, lx + lbl_w)
    d.text((right + 14, y1), "JEOL", font=f, fill=255)
    d.text((1110, y1), date_text, font=f, fill=255)
    if extra_line1:
        d.text((40, y1), extra_line1, font=f, fill=255)
    # line 2
    x = 520
    for t in (mag_text, kv_text, det_text, "SEM", wd_text):
        if t:
            d.text((x, y2), t, font=f, fill=255)
            x += int(d.textlength(t, font=f)) + 26
    d.text((1150, y2), time_text, font=f, fill=255)
    return _to_bgr(img), {"bar_rect": (bx, by, bar_px, 8), "bar_h": bar_h}


def render_thermo(scale_text="15 µm", bar_x0=8, bar_x1=322,
                  mag_value="10 000 ×", fw_value="51.8 µm", hv_value="15 kV",
                  det_value="BSD Full", wd_value="8.947 mm",
                  vac_value="0.10 Pa", width=1080, height=717, bar_h=42,
                  extra_pairs=None):
    """Layout B.  The label is centred under a thin ticked line."""
    img = Image.new("L", (width, height), 0)
    img.paste(Image.fromarray(_micrograph(width, height - bar_h, seed=5)), (0, 0))
    d = ImageDraw.Draw(img)
    fl = _font(["arialbi.ttf", "segoeuiz.ttf"], 13)
    fv = _font(["arialbd.ttf", "segoeuib.ttf"], 14)
    y0 = height - bar_h
    ly = y0 + 9
    d.rectangle([bar_x0, ly, bar_x1, ly + 1], fill=255)
    for tx in (bar_x0, bar_x1):
        d.rectangle([tx, ly - 4, tx + 1, ly + 5], fill=255)
    tw = d.textlength(scale_text, font=fv)
    d.text(((bar_x0 + bar_x1) / 2 - tw / 2, y0 + 20), scale_text, font=fv,
           fill=255)
    pairs = [("Mag.", mag_value), ("FW", fw_value), ("HV", hv_value),
             ("Int.", "Image"), ("Det.", det_value), ("WD", wd_value),
             ("Vac.", vac_value)]
    if extra_pairs:
        pairs += list(extra_pairs)
    x = max(bar_x1, 322) + 28
    for lab, val in pairs:
        if val is None:
            continue
        d.text((x, y0 + 3), lab, font=fl, fill=255)
        d.text((x, y0 + 21), val, font=fv, fill=255)
        x += int(max(d.textlength(lab, font=fl), d.textlength(val, font=fv))) + 22
    d.text((width - 8 - d.textlength("2025-04-21", font=fv), y0 + 3),
           "2025-04-21", font=fv, fill=255)
    t = "stainless steel - image"
    d.text((width - 8 - d.textlength(t, font=fv), y0 + 21), t, font=fv, fill=255)
    return _to_bgr(img), {"bar_rect": (bar_x0, ly - 4, bar_x1 - bar_x0 + 2, 10),
                          "bar_h": bar_h}
