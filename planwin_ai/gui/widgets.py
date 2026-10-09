"""Small reusable widgets for the dock panels: collapsible card sections and a transparent scroll panel.

The look comes from the application style sheet (``theme.qss``) through object names:
``QWidget#PanelSection`` (the card), ``QToolButton#SectionHeader`` (full-width header, checked = expanded),
``QLabel#SectionBadge`` (count on the right of a header), ``QWidget#SectionBody`` and
``QScrollArea#PanelScroll`` / ``QWidget#PanelScrollInner``.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _as_bool(v, default: bool) -> bool:
    """QSettings returns "true"/"false" strings from INI files and the registry, real bools elsewhere."""
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


class SectionHeader(QToolButton):
    """Full-width, checkable section header: a chevron and the title, left aligned.

    The background, border, font and colour come from the style sheet (``QToolButton#SectionHeader``);
    the chevron and the title are painted here because a style sheet cannot left-align a tool button's text.
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SectionHeader")
        self.title = title
        self.setText(title.replace("&", "&&"))  # no mnemonic shortcut from "Stairs & tanks"
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.right_inset = 0  # room kept free on the right for the badge

    def sizeHint(self) -> QSize:  # Qt API
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self.title) + 48 + self.right_inset, max(fm.height() + 14, 28))

    def minimumSizeHint(self) -> QSize:  # Qt API
        return QSize(60, self.sizeHint().height())

    def paintEvent(self, _event) -> None:  # Qt API
        p = QStylePainter(self)
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        opt.text, opt.icon, opt.arrowType = "", QIcon(), Qt.NoArrow
        p.drawComplexControl(QStyle.CC_ToolButton, opt)  # panel only: hover / checked / focus from the sheet
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        color = self.palette().color(QPalette.ButtonText)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x, cy, s = 11.0, r.center().y() + 0.5, 3.5
        if self.isChecked():  # chevron down
            pts = [(x, cy - s / 2), (x + s, cy + s / 2), (x + 2 * s, cy - s / 2)]
        else:  # chevron right
            pts = [(x + s / 2, cy - s), (x + 1.5 * s, cy), (x + s / 2, cy + s)]
        p.drawPolyline(QPolygonF([QPointF(a, b) for a, b in pts]))
        tx = int(x + 2 * s + 8)
        width = max(r.width() - tx - 8 - self.right_inset, 10)
        text = self.fontMetrics().elidedText(self.title, Qt.ElideRight, width)
        p.drawText(QRect(tx, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)


class CollapsibleSection(QWidget):
    """A titled card whose body folds away when the header is clicked.

    The expanded state is remembered in ``settings`` (a :class:`QSettings`) under ``panels/<key>``.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        key: str,
        expanded: bool = True,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("PanelSection")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.key = key
        self._settings = settings
        self._grow = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.header = SectionHeader(title)
        hl = QHBoxLayout(self.header)  # the badge sits on the right-hand end of the header
        hl.setContentsMargins(0, 0, 10, 0)
        hl.addStretch(1)
        self.badge = QLabel()
        self.badge.setObjectName("SectionBadge")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge.hide()
        hl.addWidget(self.badge)
        lay.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("SectionBody")
        self.body.setAttribute(Qt.WA_StyledBackground, True)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 6, 10, 10)
        self.body_layout.setSpacing(6)
        lay.addWidget(self.body)

        self._apply(self._load(expanded))
        self.header.toggled.connect(self._on_header)

    # ----------------------------------------------------------- state
    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    @property
    def title(self) -> str:
        return self.header.title

    def set_expanded(self, on: bool) -> None:
        on = bool(on)
        if on == self.expanded:
            return
        self.header.setChecked(on)  # -> _on_header

    def set_badge(self, text: str) -> None:
        """Small count text on the right of the header (empty hides it)."""
        self.badge.setText(str(text))
        self.badge.setVisible(bool(str(text)))
        self.header.right_inset = self.badge.sizeHint().width() + 10 if str(text) else 0
        self.header.update()

    def set_grow(self, grow: bool) -> None:
        """Let an expanded section take spare vertical space in its :class:`ScrollPanel`."""
        self._grow = bool(grow)
        self._update_policy()

    # ----------------------------------------------------------- internals
    def _on_header(self, on: bool) -> None:
        self._apply(on)
        self._save(on)
        self.toggled.emit(on)

    def _apply(self, on: bool) -> None:
        self.header.blockSignals(True)
        self.header.setChecked(on)
        self.header.blockSignals(False)
        self.header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)
        self._update_policy()

    def _update_policy(self) -> None:
        # collapsed: header height only; expanded: at least the (height-for-width) contents, growing sections
        # also take the spare height of the panel
        if not self.header.isChecked():
            v = QSizePolicy.Fixed
        else:
            v = QSizePolicy.Expanding if self._grow else QSizePolicy.Preferred
        self.setSizePolicy(QSizePolicy.Preferred, v)
        self.updateGeometry()

    def _settings_key(self) -> str:
        return f"panels/{self.key}"

    def _load(self, default: bool) -> bool:
        if self._settings is None:
            return bool(default)
        try:
            return _as_bool(self._settings.value(self._settings_key()), bool(default))
        except Exception:  # a broken settings store must never stop a panel from opening
            return bool(default)

    def _save(self, on: bool) -> None:
        if self._settings is None:
            return
        try:
            self._settings.setValue(self._settings_key(), bool(on))
        except Exception:
            pass


class FlowLayout(QLayout):
    """Lays its widgets out left to right and wraps to a new line when the width runs out
    (button rows that must fit a narrow dock without a horizontal scroll bar)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):  # Qt API
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):  # Qt API
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:  # Qt API
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:  # Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # Qt API
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # Qt API
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self) -> QSize:  # Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # Qt API
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, move: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            if x > r.x() and x + hint.width() > r.right() + 1:
                x, y, line_h = r.x(), y + line_h + self._gap, 0
            if move:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class ScrollPanel(QScrollArea):
    """Frameless vertical scroller that stacks sections, with a see-through viewport.

    The viewport and the inner widget do not fill their background from the palette, so the panel
    always shows the dock / style-sheet colour underneath – never a black box on platforms whose
    native palette is dark while the light style sheet is active.
    """

    def __init__(self, parent: QWidget | None = None, margins: int = 6, spacing: int = 8):
        super().__init__(parent)
        self.setObjectName("PanelScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.inner = QWidget()
        self.inner.setObjectName("PanelScrollInner")
        self.inner.setAttribute(Qt.WA_StyledBackground, True)
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.setContentsMargins(margins, margins, margins, margins)
        self.inner_layout.setSpacing(spacing)
        self.inner_layout.addStretch(0)  # spare height: growing sections first, else below the last section
        self.setWidget(self.inner)
        # QScrollArea.setWidget() switches auto-fill on for the widget; both must stay transparent
        self.inner.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)

    def add_widget(self, w: QWidget, stretch: int = 0) -> QWidget:
        """Add ``w`` below the widgets added so far (above the trailing stretch)."""
        self.inner_layout.insertWidget(self.inner_layout.count() - 1, w, stretch)
        return w

    def add_section(
        self,
        title: str,
        key: str,
        expanded: bool = True,
        settings: QSettings | None = None,
        grow: bool = False,
    ) -> CollapsibleSection:
        sec = CollapsibleSection(title, key, expanded, settings)
        sec.set_grow(grow)
        self.add_widget(sec, 1 if grow else 0)
        return sec
