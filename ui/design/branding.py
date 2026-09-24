"""
App icon and branding (UI-08).

The application icon is *drawn* here with QPainter — a rounded instrument
tile showing a grain-boundary network with one measured grain highlighted in
the accent blue and a small SEM-style scale bar.  Nothing is downloaded and no
image file is needed at runtime, so the window/taskbar icon works in a source
checkout, a frozen build, and offscreen tests alike (HARD CONSTRAINT #1).

``write_ico(path)`` exports the same artwork as a multi-size Windows ``.ico``
so the build can embed it in ``GrainAnalyzer.exe`` / the installer::

    .venv\\Scripts\\python -m ui.design.branding resources\\icon.ico

Company branding is pending user decision D-11: set ``ORGANIZATION_NAME``
(and nothing else) when it is decided; the About dialog picks it up.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QIcon, QImage, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF,
)

# ---------------------------------------------------------------------------
# D-11 (pending): the single place to set the lab / company name.  Empty =
# no organisation shown anywhere.  No logo file is bundled until decided.
ORGANIZATION_NAME: str = ""

APP_TAGLINE = "SEM grain detection · measurement · reporting"
APP_DESCRIPTION = ("Detects and measures grains on scanning-electron-microscope images, "
                   "computes ASTM E112 grain size and size distributions, and exports "
                   "lab reports to Excel and PowerPoint.")
# Windows groups taskbar buttons by this id; without it a source run shows
# the python.exe icon instead of ours.
APP_USER_MODEL_ID = "GrainAnalyzer.SEM.Desktop.3"

ICON_SIZES: Tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

# Palette fixed on purpose: the icon must look identical in light and dark
# themes and on the taskbar (it is not a themed glyph).
_TILE_TOP = "#1C2B40"
_TILE_BOTTOM = "#0D1117"
_TILE_EDGE = "#34455C"
_BOUNDARY = "#8FA6C1"
_GRAIN_TOP = "#3D8AEB"
_GRAIN_BOTTOM = "#1F5FB8"
_GRAIN_EDGE = "#EAF2FC"

# Grain artwork in unit coordinates of the tile's inner area.
_GRAIN = ((0.40, 0.27), (0.63, 0.29), (0.75, 0.49), (0.63, 0.70), (0.38, 0.69), (0.27, 0.48))
_BOUNDARIES: Tuple[Tuple[Tuple[float, float], ...], ...] = (
    ((0.40, 0.27), (0.31, -0.05)),
    ((0.63, 0.29), (0.82, 0.17), (1.05, 0.24)),
    ((0.82, 0.17), (0.74, -0.05)),
    ((0.75, 0.49), (1.05, 0.55)),
    ((0.63, 0.70), (0.73, 0.86), (1.05, 0.80)),
    ((0.73, 0.86), (0.66, 1.05)),
    ((0.38, 0.69), (0.29, 1.05)),
    ((0.27, 0.48), (0.11, 0.39), (-0.05, 0.52)),
    ((0.11, 0.39), (0.12, -0.05)),
)


def paint_app_icon(p: QPainter, rect: QRectF) -> None:
    """Paint the app icon into ``rect`` (square).  Simplifies below 24 px."""
    s = min(rect.width(), rect.height())
    x0 = rect.x() + (rect.width() - s) / 2
    y0 = rect.y() + (rect.height() - s) / 2
    small = s < 24
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    # tile
    margin = 0.0 if small else s * 0.03
    tile = QRectF(x0 + margin, y0 + margin, s - 2 * margin, s - 2 * margin)
    radius = tile.width() * (0.18 if small else 0.22)
    g = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    g.setColorAt(0.0, QColor(_TILE_TOP))
    g.setColorAt(1.0, QColor(_TILE_BOTTOM))
    path = QPainterPath()
    path.addRoundedRect(tile, radius, radius)
    p.fillPath(path, QBrush(g))
    if not small:
        p.setPen(QPen(QColor(_TILE_EDGE), max(1.0, s / 128)))
        p.setBrush(Qt.NoBrush)
        inset = max(0.5, s / 256)
        p.drawRoundedRect(tile.adjusted(inset, inset, -inset, -inset), radius, radius)
    p.setClipPath(path)

    pad = tile.width() * (0.10 if small else 0.12)
    inner = tile.adjusted(pad, pad, -pad, -pad)

    def pt(u: float, v: float) -> QPointF:
        return QPointF(inner.x() + u * inner.width(), inner.y() + v * inner.height())

    # grain-boundary network (dropped at favicon sizes where it turns to mush)
    if not small:
        pen = QPen(QColor(_BOUNDARY), max(1.0, s * 0.028))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        c = QColor(_BOUNDARY)
        c.setAlphaF(0.75)
        pen.setColor(c)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for line in _BOUNDARIES:
            p.drawPolyline(QPolygonF([pt(u, v) for u, v in line]))

    # the measured grain
    poly = QPolygonF([pt(u, v) for u, v in _GRAIN])
    if small:  # enlarge so it stays legible at 16 px
        c = poly.boundingRect().center()
        poly = QPolygonF([c + (q - c) * 1.45 for q in poly])
    gg = QLinearGradient(poly.boundingRect().topLeft(), poly.boundingRect().bottomRight())
    gg.setColorAt(0.0, QColor(_GRAIN_TOP))
    gg.setColorAt(1.0, QColor(_GRAIN_BOTTOM))
    pen = QPen(QColor(_GRAIN_EDGE), max(1.0, s * (0.07 if small else 0.034)))
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(gg))
    p.drawPolygon(poly)

    # SEM-style scale bar, bottom-left
    if s >= 40:
        h = max(2.0, s * 0.035)
        bar = QRectF(pt(0.0, 0.93).x(), pt(0.0, 0.93).y() - h / 2, inner.width() * 0.22, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_GRAIN_EDGE))
        p.drawRoundedRect(bar, h / 2, h / 2)
    p.restore()


def app_icon_image(size: int) -> QImage:
    """The icon rendered to a transparent ARGB image of ``size`` px."""
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    paint_app_icon(p, QRectF(0, 0, size, size))
    p.end()
    return img


def app_icon_pixmap(size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """Crisp pixmap for ``size`` logical px at the given device-pixel ratio."""
    dpr = max(1.0, float(device_pixel_ratio))
    pm = QPixmap.fromImage(app_icon_image(int(round(size * dpr))))
    pm.setDevicePixelRatio(dpr)
    return pm


_icon: Optional[QIcon] = None


def app_icon() -> QIcon:
    """Multi-resolution QIcon (every size drawn natively, never scaled)."""
    global _icon
    if _icon is None:
        ic = QIcon()
        for n in ICON_SIZES:
            ic.addPixmap(QPixmap.fromImage(app_icon_image(n)))
        _icon = ic
    return _icon


def _set_windows_app_id() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        return True
    except Exception:
        return False


def install_app_icon(app: Optional[QGuiApplication] = None) -> QIcon:
    """Set the application-wide window icon (every window, dialog and the
    taskbar button).  Idempotent."""
    app = app or QGuiApplication.instance()
    ic = app_icon()
    if app is not None:
        _set_windows_app_id()
        app.setWindowIcon(ic)
    return ic


def ico_bytes(sizes: Sequence[int] = (16, 24, 32, 48, 64, 128, 256)) -> bytes:
    """A Windows .ico containing PNG-compressed entries for ``sizes``."""
    pngs: List[bytes] = []
    for n in sizes:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        app_icon_image(n).save(buf, "PNG")
        buf.close()
        pngs.append(bytes(ba))
    out = [struct.pack("<HHH", 0, 1, len(sizes))]
    offset = 6 + 16 * len(sizes)
    for n, data in zip(sizes, pngs):
        d = 0 if n >= 256 else n
        out.append(struct.pack("<BBBBHHII", d, d, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    out.extend(pngs)
    return b"".join(out)


def write_ico(path, sizes: Iterable[int] = (16, 24, 32, 48, 64, 128, 256)) -> Path:
    """Write the icon as a multi-size .ico (used by the build)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico_bytes(tuple(sizes)))
    return path


def _main(argv: Sequence[str]) -> int:
    from PySide6.QtGui import QGuiApplication as _App
    _app = _App.instance() or _App(["branding"])  # noqa: F841 (QPainter needs an app)
    target = argv[0] if argv else "resources/icon.ico"
    print(f"Wrote {write_ico(target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
