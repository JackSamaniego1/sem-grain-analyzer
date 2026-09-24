"""Typed editors for hierarchy-profile metadata fields (HIER-01).

``make_editor(field, value)`` builds the right input for a
:class:`data.hierarchy.FieldDef` kind — text, number, date, choice, or the
legacy multi-line notes — and ``editor_value`` / ``set_editor_value`` read
and write it as the plain string stored in the level's JSON file.
"""
from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QComboBox, QDateEdit, QLineEdit, QPlainTextEdit, QWidget


def make_editor(fd, value="", placeholder: str = "") -> QWidget:
    kind = getattr(fd, "kind", "text") or "text"
    tip = fd.label + ("  (required)" if getattr(fd, "required", False) else "")
    if kind == "multiline":
        w = QPlainTextEdit(str(value or ""))
        w.setFixedHeight(60)
    elif kind == "date":
        w = QDateEdit()
        w.setCalendarPopup(True)
        w.setDisplayFormat("yyyy-MM-dd")
        d = QDate.fromString(str(value or ""), "yyyy-MM-dd")
        w.setDate(d if d.isValid() else QDate.currentDate())
    elif kind == "choice":
        w = QComboBox()
        w.addItem("—", "")
        for c in (getattr(fd, "choices", None) or []):
            w.addItem(str(c), str(c))
        if value and w.findData(str(value)) < 0:
            w.addItem(str(value), str(value))
        w.setCurrentIndex(max(0, w.findData(str(value or ""))))
    else:
        w = QLineEdit(str(value if value is not None else ""))
        if kind == "number":
            v = QDoubleValidator(w)
            v.setNotation(QDoubleValidator.StandardNotation)
            w.setValidator(v)
        if placeholder:
            w.setPlaceholderText(placeholder)
    w.setToolTip(tip)
    w.setAccessibleName(fd.label)
    w.setProperty("field_key", fd.key)
    return w


def editor_value(w: QWidget) -> str:
    if isinstance(w, QDateEdit):
        return w.date().toString("yyyy-MM-dd")
    if isinstance(w, QPlainTextEdit):
        return w.toPlainText().strip()
    if isinstance(w, QComboBox):
        d = w.currentData()
        return str(d if d is not None else w.currentText()).strip()
    if isinstance(w, QLineEdit):
        return w.text().strip()
    return ""


def set_editor_value(w: QWidget, value) -> None:
    value = "" if value is None else str(value)
    if isinstance(w, QDateEdit):
        d = QDate.fromString(value, "yyyy-MM-dd")
        if d.isValid():
            w.setDate(d)
    elif isinstance(w, QPlainTextEdit):
        w.setPlainText(value)
    elif isinstance(w, QComboBox):
        i = w.findData(value)
        if i < 0 and value:
            w.addItem(value, value)
            i = w.count() - 1
        w.setCurrentIndex(max(0, i))
    elif isinstance(w, QLineEdit):
        w.setText(value)


def mark_invalid(w: QWidget, on: bool) -> None:
    w.setProperty("invalid", "true" if on else "false")
    w.style().unpolish(w)
    w.style().polish(w)


__all__ = ["make_editor", "editor_value", "set_editor_value", "mark_invalid"]
