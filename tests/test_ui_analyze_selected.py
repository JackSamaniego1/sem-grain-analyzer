"""Analyze page: tick boxes on image rows and the "Analyze selected" button."""
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from tests.test_ui_ux_v301 import _open, env  # noqa: E402,F401
from tests.ui_shell_helpers import confirm_setup, make_session  # noqa: E402


def _uids(shell):
    return [im.uid for im in shell.state.images()]


def test_button_visibility_and_count(env, qtbot):
    shell = _open(qtbot, make_session(env, 3))
    a, u = shell.analyze, _uids(shell)
    assert a.btn_sel.isHidden()
    a.film.set_checked(u[:1])
    assert a.btn_sel.isHidden()
    a.film.set_checked(u[:2])
    assert not a.btn_sel.isHidden() and a.btn_sel.text() == "Analyze selected (2)"
    a.film.set_checked(u)
    assert a.btn_sel.text() == "Analyze selected (3)"
    a.film.set_checked(u[:1])
    assert a.btn_sel.isHidden()
    shell.close()


def test_click_on_box_ticks_without_changing_image_and_row_click_does_not_tick(env, qtbot):
    shell = _open(qtbot, make_session(env, 3))
    a, u = shell.analyze, _uids(shell)
    tree = a.film.tree
    a.film.set_current(u[0])
    shell.state.set_current_image(u[0])
    cur = shell.state.current_uid
    vp = tree.viewport()
    rect = tree.visualItemRect(a.film.item(u[1]))
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(rect.left() + 14, rect.center().y()))
    assert a.film.checked_uids() == [u[1]]
    assert shell.state.current_uid == cur
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(rect.right() - 20, rect.center().y()))
    assert a.film.checked_uids() == [u[1]]          # row click did not toggle
    assert shell.state.current_uid == u[1]          # ...but it did select the row
    shell.close()


def test_analyze_selected_runs_exactly_ticked(env, qtbot):
    shell = _open(qtbot, make_session(env, 3))
    a, u = shell.analyze, _uids(shell)
    a.params.set_mode("threshold")
    confirm_setup(shell, qtbot)
    a.film.set_checked([u[0], u[2]])
    got = []
    orig = a.queue.start
    a.queue.start = lambda jobs: (got.extend(j.uid for j in jobs), orig(jobs))[1]
    with qtbot.waitSignal(a.queue.queue_finished, timeout=90000):
        a.btn_sel.click()
        assert a.btn_sel.is_loading() and not a.btn_all.isEnabled()
    assert sorted(got) == sorted([u[0], u[2]])
    st = {im.uid: im.status for im in shell.state.images()}
    assert st[u[0]] == "done" and st[u[2]] == "done" and st[u[1]] != "done"
    assert a.btn_sel.isEnabled() and not a.btn_sel.is_loading()
    shell.close()


def test_ticks_survive_refresh_and_drop_removed(env, qtbot):
    shell = _open(qtbot, make_session(env, 3))
    a, u = shell.analyze, _uids(shell)
    a.film.set_checked(u)
    a.film.set_images(shell.state.images())
    a.film.update_item(u[0])
    a.film.refresh_all()
    assert a.film.checked_uids() == u
    a.remove_images([u[2]])
    assert a.film.checked_uids() == u[:2] and a.btn_sel.text() == "Analyze selected (2)"
    a.remove_images([u[1]])
    assert a.btn_sel.isHidden()
    shell.close()


def test_flag_off_means_no_checkboxes(env, qtbot):
    from ui.pages.image_tree import ImageTree
    shell = _open(qtbot, make_session(env, 2))
    u = _uids(shell)
    t = ImageTree(shell.state)
    qtbot.addWidget(t)
    t.resize(300, 400)
    t.show()
    t.set_images(shell.state.images())
    assert not t.checkable
    rect = t.tree.visualItemRect(t.item(u[0]))
    QTest.mouseClick(t.tree.viewport(), Qt.LeftButton, Qt.NoModifier,
                     QPoint(rect.left() + 14, rect.center().y()))
    assert t.checked_uids() == []
    assert ImageTree(shell.state, checkable=True).checkable
    shell.close()


def test_box_click_makes_row_current_for_space_and_double_click_toggles_again(env, qtbot):
    shell = _open(qtbot, make_session(env, 3))
    a, u = shell.analyze, _uids(shell)
    tree = a.film.tree
    shell.state.set_current_image(u[0])
    rect = tree.visualItemRect(a.film.item(u[2]))
    pos = QPoint(rect.left() + 14, rect.center().y())
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    assert shell.state.current_uid == u[0]
    assert tree.currentItem() is a.film.item(u[2])
    assert u[2] not in a.film.selected_uids()
    assert a.film.checked_uids() == [u[2]]
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    activated = []
    tree.itemActivated.connect(lambda *_: activated.append(1))
    dbl = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(pos), tree.viewport().mapToGlobal(QPointF(pos)),
                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(tree.viewport(), dbl)
    assert a.film.checked_uids() == []           # the fast second click toggles again
    assert not activated and shell.state.current_uid == u[0]
    a.film.set_checked([u[2]])
    a.film.set_checked([])
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
    QTest.keyClick(tree, Qt.Key_Space)          # acts on the clicked row
    assert a.film.checked_uids() == []
    assert shell.state.current_uid == u[0]
    shell.close()
