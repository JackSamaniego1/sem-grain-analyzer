"""
UPDATE 4 item 14: the display mode (Original / Overlay / Mask / Excluded)
the user picks on Analyze and on Review stays when another image is
selected; an image without results falls back to Original for itself only.
"""
import pytest

pytest.importorskip("pytestqt")

from tests.test_ui_shell_analysis import _analyse_all, _open_shell, env  # noqa: E402,F401
from tests.ui_shell_helpers import make_session  # noqa: E402


@pytest.fixture
def analysed3(env, qtbot):  # noqa: F811
    shell = _open_shell(qtbot, make_session(env, 3, label="Run", black=True))
    _analyse_all(shell, qtbot)
    yield shell
    shell.close()


def _pick(shell, qtbot, uid):
    shell.state.set_current_image(uid)
    qtbot.waitUntil(lambda: shell.state.current_uid == uid, timeout=5000)


def _check(shell, qtbot, page, mode_index, mode_key):
    st = shell.state
    uids = [im.uid for im in st.images()]
    _pick(shell, qtbot, uids[0])
    page.view_seg.set_current_index(mode_index)
    assert page.canvas.view() == mode_key
    for uid in (uids[1], uids[2], uids[0]):                 # survives every switch
        _pick(shell, qtbot, uid)
        assert page.canvas.view() == mode_key
        assert page.view_seg.current_index() == mode_index
    # an image without results shows Original, without forgetting the choice
    im = st.images()[1]
    saved = im.result
    im.result = None
    try:
        _pick(shell, qtbot, uids[1])
        assert page.canvas.view() == "original"
        assert page._view_pref == mode_key
    finally:
        im.result = saved
    _pick(shell, qtbot, uids[2])
    assert page.canvas.view() == mode_key
    assert page.view_seg.current_index() == mode_index


def test_analyze_display_mode_persists_across_images(analysed3, qtbot):
    shell = analysed3
    shell.go("analyze")
    _check(shell, qtbot, shell.analyze, 0, "original")
    _check(shell, qtbot, shell.analyze, 2, "excluded")
    _check(shell, qtbot, shell.analyze, 1, "overlay")


def test_review_display_mode_persists_across_images(analysed3, qtbot):
    shell = analysed3
    shell.go("review")
    _check(shell, qtbot, shell.review, 2, "mask")
    _check(shell, qtbot, shell.review, 0, "original")
    _check(shell, qtbot, shell.review, 3, "excluded")


def test_pages_keep_their_own_display_mode(analysed3, qtbot):
    shell = analysed3
    uids = [im.uid for im in shell.state.images()]
    shell.go("analyze")
    shell.analyze.view_seg.set_current_index(0)             # Analyze: Original
    shell.go("review")
    shell.review.view_seg.set_current_index(2)              # Review: Mask
    _pick(shell, qtbot, uids[1])
    assert shell.review.canvas.view() == "mask"
    shell.go("analyze")
    _pick(shell, qtbot, uids[2])
    assert shell.analyze.canvas.view() == "original"
