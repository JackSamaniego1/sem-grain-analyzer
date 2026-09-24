"""
Design system + animated widget library (UI-01 / UI-02).

Offscreen via QT_QPA_PLATFORM set in conftest; uses pytest-qt's qtbot.
"""
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from ui.design import tokens as T  # noqa: E402
from ui.design.theme import (  # noqa: E402
    apply_theme, build_stylesheet, current_mode, current_tokens, set_reduced_motion,
    theme_manager,
)
from ui.widgets import (  # noqa: E402
    AnimatedButton, Badge, Breadcrumb, Card, Chip, CollapsibleSection, Divider, EmptyState,
    FadeStackedWidget, IconButton, KeyValueList, NavRail, ProgressRing, SearchBox,
    SegmentedControl, ShortcutOverlay, Skeleton, Spinner, StatCard, ToastManager,
)


@pytest.fixture(autouse=True)
def _theme(qapp):
    """Every test starts dark with full motion and restores that afterwards."""
    set_reduced_motion(False)
    apply_theme(qapp, "dark")
    yield
    set_reduced_motion(False)
    apply_theme(qapp, "dark")


@pytest.fixture
def reduced():
    set_reduced_motion(True)
    yield
    set_reduced_motion(False)


def _all_widgets(host):
    """One instance of every public widget, parented to ``host``."""
    stack = FadeStackedWidget(host)
    stack.addWidget(QLabel("a"))
    stack.addWidget(QLabel("b"))
    rail = NavRail(host)
    rail.add_page("a", "projects", "Projects")
    sc = StatCard("Grains", 10, "", 0, host)
    sc.set_delta("+1", "up")
    sc.set_sparkline([1, 2, 3])
    card = Card("Title", "Sub", parent=host)
    card.add_action(IconButton("more", "More"))
    return [
        AnimatedButton("Run", "run", "primary", parent=host),
        IconButton("search", "Search", parent=host),
        card, sc,
        Badge.for_status("PASS", host), Chip("x", checkable=True, closable=True, parent=host),
        SegmentedControl(["A", "B"], parent=host), CollapsibleSection("S", parent=host),
        Skeleton(parent=host), Spinner(parent=host), ProgressRing(parent=host),
        Breadcrumb(["P", "S", "L"], parent=host), SearchBox(parent=host),
        EmptyState("images", "Empty", "Body", "Act", parent=host), rail, stack,
        Divider(parent=host), Divider(Qt.Vertical, parent=host),
        KeyValueList({"k": "v"}, parent=host),
    ]


# ---------------------------------------------------------------------------
# Tokens / theme
# ---------------------------------------------------------------------------

def test_contrast_ratio_reference_values():
    assert T.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, rel=1e-3)
    assert T.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    # WCAG reference: #767676 on white is the classic 4.54:1 grey
    assert T.contrast_ratio("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)
    assert T.contrast_ratio("#FFFFFF", "#767676") == T.contrast_ratio("#767676", "#FFFFFF")


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_text_token_pairs_meet_wcag_aa(mode):
    t = T.tokens_for(mode)
    assert len(T.text_contrast_pairs(t)) > 20
    assert T.contrast_failures(t, 4.5) == []


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_dataviz_palette_is_eight_distinct_colours(mode):
    pal = T.dataviz_palette(mode)
    assert len(pal) == 8 and len(set(pal)) == 8
    bg = T.tokens_for(mode).surface.surface1
    # every series is at least distinguishable from the card background (3:1 graphics)
    assert all(T.contrast_ratio(c, bg) >= 1.8 for c in pal)


def test_tokens_module_is_qt_free():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(T))
    mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(m.startswith("PySide6") for m in mods)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        T.tokens_for("sepia")


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_apply_theme_sets_palette_and_qss(qapp, mode):
    apply_theme(qapp, mode)
    t = current_tokens()
    assert current_mode() == mode and t.mode == mode
    qss = qapp.styleSheet()
    for selector in ('QPushButton[variant="primary"]', 'QPushButton[variant="danger"]',
                     "QComboBox::drop-down", "QScrollBar::handle:vertical:hover",
                     "QHeaderView::section", "QToolTip", "QMenu::item:selected",
                     "QTabBar::tab:selected", "QProgressBar::chunk", "QSlider::handle:horizontal",
                     "QCheckBox::indicator:checked", "QStatusBar", "QGroupBox::title"):
        assert selector in qss, selector
    assert t.accent.base.lower() in qss.lower()
    assert qapp.palette().window().color().name().lower() == t.surface.bg.lower()


def test_stylesheet_differs_between_themes():
    assert build_stylesheet(T.DARK) != build_stylesheet(T.LIGHT)


def test_theme_changed_signal_and_live_restyle(qapp, qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    widgets = _all_widgets(host)
    host.show()
    with qtbot.waitSignal(theme_manager().theme_changed, timeout=1000) as blocker:
        apply_theme(qapp, "light")
    assert blocker.args == ["light"]
    assert qapp.palette().window().color().name().lower() == T.LIGHT.surface.bg.lower()
    # every widget still renders after the switch
    for w in widgets:
        assert not w.grab().isNull()


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_every_widget_instantiates_and_paints(qapp, qtbot, mode):
    apply_theme(qapp, mode)
    host = QWidget()
    qtbot.addWidget(host)
    widgets = _all_widgets(host)
    host.resize(900, 700)
    host.show()
    qtbot.waitExposed(host)
    for w in widgets:
        pm = w.grab()
        assert not pm.isNull(), type(w).__name__


def test_icons_resolve_offline(qapp):
    from ui.design import icons
    for name in icons.ICONS:
        assert not icons.icon(name).isNull(), name
    with pytest.raises(KeyError):
        icons.glyph("no-such-icon")


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------

def test_icon_button_requires_tooltip(qtbot):
    with pytest.raises(ValueError):
        IconButton("search", "")


def test_animated_button_loading_blocks_clicks(qtbot):
    b = AnimatedButton("Run", "run", "primary")
    qtbot.addWidget(b)
    b.show()
    clicks = []
    b.clicked.connect(lambda: clicks.append(1))
    qtbot.mouseClick(b, Qt.LeftButton)
    assert clicks == [1]
    b.set_loading(True)
    assert b.is_loading()
    qtbot.mouseClick(b, Qt.LeftButton)
    assert clicks == [1]
    b.set_loading(False)
    qtbot.mouseClick(b, Qt.LeftButton)
    assert clicks == [1, 1]
    assert b.sizeHint().width() > b.sizeHint().height()


def test_segmented_control_emits_and_animates(qtbot):
    seg = SegmentedControl(["Dark", "Light", "Auto"])
    qtbot.addWidget(seg)
    seg.show()
    with qtbot.waitSignals([seg.current_changed, seg.current_text_changed], timeout=1000):
        qtbot.mouseClick(seg, Qt.LeftButton, pos=QPoint(seg.width() - 5, seg.height() // 2))
    assert seg.current_index() == 2 and seg.current_text() == "Auto"
    qtbot.waitUntil(lambda: seg.indicator_position() == pytest.approx(2.0), timeout=2000)
    with qtbot.waitSignal(seg.current_changed, timeout=1000) as blk:
        qtbot.keyClick(seg, Qt.Key_Left)
    assert blk.args == [1]


def test_navrail_emits_page_selected_and_expands(qtbot):
    rail = NavRail()
    qtbot.addWidget(rail)
    for k in ("projects", "analyze", "review"):
        rail.add_page(k, k, k.title())
    rail.resize(64, 400)
    rail.show()
    assert rail.current() == "projects"
    with qtbot.waitSignal(rail.page_selected, timeout=1000) as blk:
        qtbot.mouseClick(rail.item("review"), Qt.LeftButton)
    assert blk.args == ["review"] and rail.current() == "review"
    rail.set_expanded(True)
    qtbot.waitUntil(lambda: rail.expand_progress() == pytest.approx(1.0), timeout=2000)
    assert rail.width() == T.SIZES.rail_expanded and rail.is_expanded()


def test_fade_stack_transition_completes(qtbot):
    stack = FadeStackedWidget()
    qtbot.addWidget(stack)
    a, b = QLabel("A"), QLabel("B")
    stack.addWidget(a)
    stack.addWidget(b)
    stack.resize(300, 200)
    stack.show()
    qtbot.waitExposed(stack)
    with qtbot.waitSignal(stack.transition_finished, timeout=2000):
        stack.setCurrentIndex(1)
        assert stack.is_animating()
    assert stack.currentWidget() is b and b.graphicsEffect() is None
    assert b.pos() == stack.contentsRect().topLeft()


def test_toast_auto_dismisses(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(800, 600)
    host.show()
    mgr = ToastManager(host)
    toast = mgr.show_toast("Done", "12 images", "success", "Open", timeout_ms=150)
    assert mgr.count() == 1 and toast.isVisible()
    qtbot.waitUntil(lambda: mgr.count() == 0, timeout=3000)
    from shiboken6 import isValid
    qtbot.waitUntil(lambda: not isValid(toast) or not toast.isVisible(), timeout=3000)


def test_toast_stack_limit_and_action(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(800, 600)
    host.show()
    mgr = ToastManager(host, max_visible=2)
    hits = []
    for i in range(3):
        last = mgr.show_toast(f"T{i}", timeout_ms=0, action_text="Go",
                              on_action=lambda: hits.append(1))
    assert mgr.count() == 2
    last.action_button.click()
    assert hits == [1] and mgr.count() == 1


def test_collapsible_section_toggles_height(qtbot):
    sec = CollapsibleSection("Details", expanded=True)
    qtbot.addWidget(sec)
    for i in range(4):
        sec.add_widget(QLabel(f"row {i}"))
    sec.show()
    qtbot.waitExposed(sec)
    content = sec.content_widget()
    assert content.height() > 20
    with qtbot.waitSignal(sec.toggled, timeout=1000) as blk:
        sec.toggle()
    assert blk.args == [False]
    qtbot.waitUntil(lambda: not content.isVisible(), timeout=2000)
    assert sec.chevron_angle() == pytest.approx(0.0)
    sec.toggle()
    qtbot.waitUntil(lambda: content.maximumHeight() > 10000, timeout=2000)
    qtbot.waitUntil(lambda: content.height() > 20, timeout=2000)
    assert sec.chevron_angle() == pytest.approx(90.0)


def test_statcard_count_up_ends_at_target(qtbot):
    sc = StatCard("Grains", 0, "", 0)
    qtbot.addWidget(sc)
    sc.show()
    sc.set_value(1284)
    assert sc.value() == 1284
    qtbot.waitUntil(lambda: sc.displayed_text() == "1,284", timeout=3000)
    sc2 = StatCard("Mean", 0, "um", 2)
    qtbot.addWidget(sc2)
    sc2.set_value(12.414)
    qtbot.waitUntil(lambda: sc2.displayed_text() == "12.41", timeout=3000)


def test_reduced_motion_makes_animations_instant(qtbot, reduced):
    sc = StatCard("Grains", 0)
    qtbot.addWidget(sc)
    sc.set_value(500)
    assert sc.displayed_text() == "500"
    seg = SegmentedControl(["A", "B", "C"])
    qtbot.addWidget(seg)
    seg.set_current_index(2)
    assert seg.indicator_position() == 2.0
    ring = ProgressRing()
    qtbot.addWidget(ring)
    ring.set_value(75)
    assert ring.displayed_value() == 75.0
    sec = CollapsibleSection("S", expanded=True)
    qtbot.addWidget(sec)
    sec.add_widget(QLabel("x"))
    sec.show()
    sec.set_expanded(False)
    assert not sec.content_widget().isVisible() and sec.chevron_angle() == 0.0
    rail = NavRail()
    qtbot.addWidget(rail)
    rail.add_page("a", "projects", "A")
    rail.set_expanded(True)
    assert rail.width() == T.SIZES.rail_expanded
    stack = FadeStackedWidget()
    qtbot.addWidget(stack)
    stack.addWidget(QLabel("1"))
    stack.addWidget(QLabel("2"))
    stack.show()
    stack.setCurrentIndex(1)
    assert stack.currentIndex() == 1 and not stack.is_animating()
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(600, 400)
    host.show()
    mgr = ToastManager(host)
    t = mgr.show_toast("x", timeout_ms=0)
    assert t.opacity.opacity() == 1.0
    mgr.dismiss(t)
    assert not t.isVisible()


def test_search_box_debounces(qtbot):
    box = SearchBox(debounce_ms=50)
    qtbot.addWidget(box)
    box.show()
    got = []
    box.search_changed.connect(got.append)
    qtbot.keyClicks(box, "grain")
    assert got == []
    qtbot.waitUntil(lambda: got == ["grain"], timeout=1000)
    box.clear_search()
    assert got[-1] == "" and box.text() == ""


def test_breadcrumb_clicks_only_ancestors(qtbot):
    bc = Breadcrumb(["Projects", "Alloy 718", "S-014"])
    qtbot.addWidget(bc)
    bc.show()
    got = []
    bc.segment_clicked.connect(lambda i, s: got.append((i, s)))
    bc.buttons()[1].click()
    bc.buttons()[2].click()  # current segment: no navigation
    assert got == [(1, "Alloy 718")]
    bc.set_segments(["A" * 400])
    assert len(bc.buttons()[0].text()) < 400 and bc.buttons()[0].toolTip() == "A" * 400


def test_chip_toggle_and_close(qtbot):
    chip = Chip("Edge", checkable=True, closable=True)
    qtbot.addWidget(chip)
    chip.show()
    with qtbot.waitSignal(chip.toggled, timeout=500):
        chip.set_checked(True)
    assert chip.is_checked()
    with qtbot.waitSignal(chip.closed, timeout=500):
        qtbot.keyClick(chip, Qt.Key_Delete)


def test_badge_status_mapping():
    assert Badge.for_status("PASS").kind() == "success"
    assert Badge.for_status("fail").kind() == "danger"
    assert Badge.for_status("INCONCLUSIVE").kind() == "warning"
    assert Badge.for_status("Draft").kind() == "neutral"
    assert Badge.for_status("Approved").kind() == "success"


def test_shortcut_overlay_toggles(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(800, 600)
    host.show()
    ov = ShortcutOverlay(host, {"Canvas": [("Ctrl+Z", "Undo"), ("Del", "Delete")]})
    assert not ov.is_open()
    ov.toggle()
    assert ov.is_open() and ov.geometry() == host.rect()
    ov.toggle()
    qtbot.waitUntil(lambda: not ov.isVisible(), timeout=2000)


def test_key_value_list_updates(qtbot):
    kv = KeyValueList([("Pixel size", "0.41")], mono_keys=("Pixel size",))
    qtbot.addWidget(kv)
    kv.set_value("Pixel size", "0.412")
    kv.set_value("Detector", "SE2")
    assert kv.value("Pixel size") == "0.412" and kv.value("Detector") == "SE2"
