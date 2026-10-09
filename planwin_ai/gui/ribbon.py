"""Office / ETABS-style ribbon: a title strip with the File menu and quick-access buttons,
command tabs, and labelled groups of large and small buttons.

The ribbon only lays out existing :class:`QAction` objects, so every command keeps one
definition (text, icon, shortcut, tooltip, checked state) whether it is reached from the
ribbon, a keyboard shortcut or a context menu.  Double-click a tab to collapse the ribbon
to its tab row; click any tab to show it again.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

LARGE_ICON = QSize(30, 30)
SMALL_ICON = QSize(16, 16)


def wrap_label(text: str, width: int = 9) -> str:
    """Two-line label for a large button: break at the space nearest the middle."""
    text = text.replace("&", "")
    if len(text) <= width or " " not in text:
        return text
    mid = len(text) / 2
    cut = min((i for i, ch in enumerate(text) if ch == " "), key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1 :]


class LargeButton(QToolButton):
    """Ribbon button with the icon above a label wrapped onto two lines."""

    def _relabel(self) -> None:
        a = self.defaultAction()
        if a is not None:
            self.setText(wrap_label(a.text()))

    def setDefaultAction(self, action: QAction) -> None:  # noqa: N802 (Qt API)
        super().setDefaultAction(action)
        self._relabel()

    def actionEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        super().actionEvent(ev)  # Qt copies the action text back on every change
        if ev.type() == QEvent.ActionChanged:
            self._relabel()


class RibbonGroup(QFrame):
    """A titled block of buttons: large buttons in a row, small ones stacked three high."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("RibbonGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 0)
        outer.setSpacing(0)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(2)
        self.grid.setVerticalSpacing(0)
        outer.addLayout(self.grid, 1)
        lbl = QLabel(title)
        lbl.setObjectName("RibbonGroupTitle")
        lbl.setAlignment(Qt.AlignHCenter)
        outer.addWidget(lbl)
        self._col = 0
        self._small_row = 0
        self.buttons: list[QToolButton] = []

    def _button(self, action: QAction, large: bool, menu: QMenu | None = None) -> QToolButton:
        """``menu``: the button opens it (the action itself then only labels the button)."""
        b = LargeButton() if large else QToolButton()
        b.setDefaultAction(action)
        b.setAutoRaise(True)
        if large:
            b.setObjectName("RibbonLarge")
            b.setIconSize(LARGE_ICON)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        else:
            b.setObjectName("RibbonSmall")
            b.setIconSize(SMALL_ICON)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if menu is not None:
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
        self.buttons.append(b)
        return b

    def large(self, action: QAction, menu: QMenu | None = None) -> QToolButton:
        if self._small_row:
            self._col += 1
            self._small_row = 0
        b = self._button(action, True, menu)
        self.grid.addWidget(b, 0, self._col, 3, 1)
        self._col += 1
        return b

    def small(self, action: QAction, menu: QMenu | None = None) -> QToolButton:
        b = self._button(action, False, menu)
        self.grid.addWidget(b, self._small_row, self._col)
        self._small_row += 1
        if self._small_row == 3:
            self._small_row = 0
            self._col += 1
        return b

    def labelled(self, label: str, w: QWidget) -> QWidget:
        """A control with a small caption above it, filling a whole column of the group."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 0)
        lay.setSpacing(2)
        cap = QLabel(label)
        cap.setObjectName("RibbonCaption")
        lay.addWidget(cap)
        lay.addWidget(w)
        lay.addStretch(1)
        if self._small_row:
            self._col += 1
            self._small_row = 0
        self.grid.addWidget(box, 0, self._col, 3, 1)
        self._col += 1
        return w

    def widget(self, w: QWidget, rows: int = 1) -> QWidget:
        """Arbitrary control (e.g. a combo box) in the small-button column."""
        self.grid.addWidget(w, self._small_row, self._col, rows, 1)
        self._small_row += rows
        if self._small_row >= 3:
            self._small_row = 0
            self._col += 1
        return w


class RibbonPage(QScrollArea):
    """One tab of groups; scrolls sideways when the window is narrower than the groups."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body = QWidget()
        body.setObjectName("RibbonBody")
        self.setWidget(body)
        self.lay = QHBoxLayout(body)
        self.lay.setContentsMargins(4, 2, 4, 2)
        self.lay.setSpacing(2)
        self.lay.addStretch(1)
        self.groups: dict[str, RibbonGroup] = {}

    def group(self, title: str) -> RibbonGroup:
        g = RibbonGroup(title)
        self.lay.insertWidget(self.lay.count() - 1, g)
        self.groups[title] = g
        return g


class Ribbon(QWidget):
    tabChanged = Signal(int)

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("Ribbon")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        top = QWidget()
        top.setObjectName("RibbonTop")
        h = QHBoxLayout(top)
        h.setContentsMargins(0, 0, 6, 0)
        h.setSpacing(2)
        self.app_button = QToolButton()
        self.app_button.setObjectName("AppButton")
        self.app_button.setText("File")
        self.app_button.setPopupMode(QToolButton.InstantPopup)
        self.app_menu = QMenu(self.app_button)
        self.app_button.setMenu(self.app_menu)
        h.addWidget(self.app_button)
        self.quick = QHBoxLayout()
        self.quick.setSpacing(0)
        h.addLayout(self.quick)
        self.tabs = QTabBar()
        self.tabs.setObjectName("RibbonTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        h.addWidget(self.tabs)
        h.addStretch(1)
        self.center = QHBoxLayout()  # command search
        h.addLayout(self.center)
        h.addSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("RibbonTitle")
        h.addWidget(self.title)
        self.right = QHBoxLayout()
        h.addLayout(self.right)
        v.addWidget(top)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(116)  # three small rows + group title + room for the sideways scrollbar
        v.addWidget(self.stack)
        self.pages: dict[str, RibbonPage] = {}
        self.tabs.currentChanged.connect(self._tab)
        self.tabs.tabBarDoubleClicked.connect(lambda _i: self.set_collapsed(not self.collapsed))
        self.collapsed = False

    # ------------------------------------------------------------------ API
    def page(self, name: str) -> RibbonPage:
        if name not in self.pages:
            pg = RibbonPage()
            self.pages[name] = pg
            self.stack.addWidget(pg)
            self.tabs.addTab(name)
        return self.pages[name]

    def add_quick(self, action: QAction, icon: QIcon | None = None) -> QToolButton:
        """Icon-only button on the title strip; ``icon`` overrides the action's (light on the accent bar)."""
        b = QToolButton()
        b.setObjectName("QuickButton")
        b.setDefaultAction(action)
        if icon is not None:
            b.setIcon(icon)
            action.changed.connect(lambda: b.setIcon(icon))  # Qt resets the icon from the action
        b.setAutoRaise(True)
        b.setIconSize(QSize(18, 18))
        b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.quick.addWidget(b)
        return b

    def add_right(self, w: QWidget) -> None:
        self.right.addWidget(w)

    def add_center(self, w: QWidget) -> None:
        """Widget on the title strip between the tabs and the project name (the command search)."""
        self.center.addWidget(w)

    def select(self, name: str) -> None:
        names = list(self.pages)
        if name in names:
            self.tabs.setCurrentIndex(names.index(name))

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.stack.setVisible(not collapsed)

    def _tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        if self.collapsed:
            self.set_collapsed(False)
        self.tabChanged.emit(i)

    def action_buttons(self) -> list[QToolButton]:
        return [b for pg in self.pages.values() for g in pg.groups.values() for b in g.buttons]
