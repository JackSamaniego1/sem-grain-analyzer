"""
Grain-edit wiring shared by the pages that host a :class:`GrainCanvas`
(UI-05 / INN-04).

The canvas only *collects* gestures (lasso loop, cut line, merge request);
this controller turns them into undoable ``AppState`` edits and tells the
user what happened with a non-blocking toast.  Lasso selection is handled
inside the canvas; deleting the selection keeps using the page's existing
"remove grains" path (manual exclusion).
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject

from core.grain_edit import GrainEditError


class GrainEditController(QObject):
    def __init__(self, canvas, state, toasts=None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent or canvas)
        self.canvas = canvas
        self.state = state
        self.toasts = toasts
        canvas.merge_requested.connect(self.merge)
        canvas.split_requested.connect(self.split)

    # ------------------------------------------------------------------ actions
    def merge(self, ids: Optional[List[int]] = None) -> Optional[int]:
        ids = list(ids) if ids else self.canvas.selected()
        uid = self.state.current_uid
        try:
            gid = self.state.merge_grains(uid, ids)
        except GrainEditError as exc:
            self._toast("Cannot merge", str(exc), "warning")
            return None
        self.canvas.select([gid])
        self._toast(f"Merged {len(ids)} grains into #{gid}",
                    "Measurements and ASTM G updated. Ctrl+Z to undo.", "success", undo=True)
        return gid

    def split(self, line) -> Optional[List[int]]:
        uid = self.state.current_uid
        try:
            pieces = self.state.split_grain(uid, line)
        except GrainEditError as exc:
            self._toast("Cannot split", str(exc), "warning")
            return None
        self.canvas.select(pieces)
        self._toast(f"Split grain #{pieces[0]} into {len(pieces)}",
                    "Measurements and ASTM G updated. Ctrl+Z to undo.", "success", undo=True)
        return pieces

    # ------------------------------------------------------------------ helpers
    def _toast(self, title: str, body: str, severity: str, undo: bool = False) -> None:
        if self.toasts is None:
            return
        if undo:
            self.toasts.show_toast(title, body, severity, "Undo", self.state.undo_stack.undo)
        else:
            self.toasts.show_toast(title, body, severity)


__all__ = ["GrainEditController"]
