"""UPDATE 4 item 5: apply a Resolution Profile to images (GUI thread, no widgets).

A profile carries a scale (nm per pixel) and a scan area defined on an image
of a given size.  Applying it to an image:

* ``check_fit`` first -- a different image size or a scan area outside the
  image changes NOTHING on that image (the caller shows the message);
* goes through the analysis lock (``AppState._guard``) like every scale /
  scan-area change, so images being analysed are never touched;
* sets the image's own scale + scan area, stores the profile snapshot on the
  image (saved in the manifest) and puts the image back to "Not analysed";
* is ONE step on the undo stack (:class:`ProfileCommand`).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from data.resolution_profiles import ResolutionProfile, check_fit
from ui.app_state import _SnapshotCommand

__all__ = ["ProfileCommand", "apply_profile", "remove_profile", "profile_of",
           "PROFILE_SOURCE"]

PROFILE_SOURCE = "profile"


def profile_of(im) -> Optional[dict]:
    """The snapshot of the profile applied to ``im`` (None = none)."""
    p = getattr(im, "profile", None)
    return p if isinstance(p, dict) and p.get("name") else None


def _snap(im) -> tuple:
    return (im.uid, im.px_override, im.scale_source, im.bar_um,
            tuple(im.scan_rect) if im.scan_rect else None, im.scan_source,
            dict(im.profile) if im.profile else None, im.status)


def _write(state, snap: Sequence[tuple]) -> None:
    doc = state.session
    if doc is None or not snap:
        return
    changed = []
    for item in snap:
        im = doc.image(item[0])
        if im is None:
            continue
        (_u, im.px_override, im.scale_source, im.bar_um, im.scan_rect,
         im.scan_source, prof, status) = item
        im.profile = dict(prof) if prof else None
        if im.status not in ("queued", "running"):
            im.status, im.progress, im.message = status, 0, ""
        changed.append(im.uid)
    state._meta_dirty = True
    state.calibration_changed.emit()
    state.setup_changed.emit()
    for uid in changed:
        state.image_updated.emit(uid)
    state.schedule_save()


class ProfileCommand(_SnapshotCommand):
    """Apply / remove a profile on several images (one undo step)."""

    def _apply(self, snap) -> None:
        _write(self.state, snap)


def apply_profile(state, profile: ResolutionProfile,
                  uids: Sequence) -> Tuple[List, List[str]]:
    """Apply ``profile`` to the images ``uids``.  Returns (applied uids,
    messages for the images left unchanged).  Nothing at all changes when
    the analysis lock refuses (its own message explains why)."""
    doc = state.session
    if doc is None or profile is None:
        return [], []
    ok, notes = [], []
    for uid in uids:
        im = doc.image(uid)
        if im is None:
            continue
        if im.loading or not im.readable or not im.shape:
            notes.append(f"{im.display_name}: the image is not loaded, so the profile "
                         "was not applied.")
            continue
        h, w = im.shape[:2]
        fit = check_fit(profile, w, h)
        if not fit.ok:
            notes.append(fit.message if len(uids) == 1 else f"{im.display_name}: {fit.message}")
            continue
        ok.append((im, fit))
    if not ok:
        return [], notes
    if not state._guard("Applying a resolution profile", [im.uid for im, _f in ok],
                        resync="calibration"):
        return [], notes
    before = [_snap(im) for im, _f in ok]
    snap = profile.snapshot()
    for im, fit in ok:
        h, w = im.shape[:2]
        im.px_override = float(fit.px_per_um)
        im.scale_source = PROFILE_SOURCE
        im.scan_rect = tuple(int(v) for v in fit.scan_rect) if fit.scan_rect \
            else (0, 0, int(w), int(h))
        im.scan_source = PROFILE_SOURCE
        im.profile = dict(snap)
        if im.status not in ("queued", "running"):
            im.status, im.progress, im.message = "pending", 0, ""   # "Not analysed"
    after = [_snap(im) for im, _f in ok]
    state.undo_stack.push(ProfileCommand(state, before, after,
                                         f"Apply profile '{profile.name}'"))
    _write(state, after)
    return [im.uid for im, _f in ok], notes


def remove_profile(state, uids: Sequence) -> List:
    """Stop showing a profile on the images (their scale and scan area stay
    as they are).  Undoable."""
    doc = state.session
    if doc is None:
        return []
    ims = [im for im in (doc.image(u) for u in uids) if im is not None and im.profile]
    if not ims or not state._guard("Removing a resolution profile",
                                   [im.uid for im in ims], resync="calibration"):
        return []
    before = [_snap(im) for im in ims]
    for im in ims:
        im.profile = None
        if im.scale_source == PROFILE_SOURCE:
            im.scale_source = "manual"
        if im.scan_source == PROFILE_SOURCE:
            im.scan_source = "manual"
    after = [_snap(im) for im in ims]
    state.undo_stack.push(ProfileCommand(state, before, after, "Remove profile"))
    _write(state, after)
    return [im.uid for im in ims]
