"""Command search ("Tell me what you want to do", Ctrl+Q).

A search box on the ribbon's title strip.  Typing lists every matching command – ribbon
actions, results tables and plans – in a popup; Enter or a click runs the highlighted one.
Matching is word-based: every typed word must appear in the command's name, tooltip or
keywords, and commands whose name starts with the text rank first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAction, QIcon, QKeyEvent
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QWidget

from .theme import round_popup

MAX_RESULTS = 12


@dataclass
class Command:
    name: str
    run: Callable[[], object]
    hint: str = ""  # shortcut or category shown on the right
    keywords: str = ""  # extra search text (synonyms, category)
    icon: QIcon = field(default_factory=QIcon)
    tip: str = ""  # one-line description shown when hovering the result

    def haystack(self) -> str:
        return f"{self.name} {self.keywords} {self.hint} {self.tip}".lower()


def rank(commands: list[Command], text: str, limit: int = MAX_RESULTS) -> list[Command]:
    """Commands matching every word of ``text``: name-prefix matches first, then name matches,
    then tooltip/keyword matches; alphabetical within a rank."""
    words = text.lower().split()
    if not words:
        return []
    hits = []
    for c in commands:
        hay = c.haystack()
        if not all(w in hay for w in words):
            continue
        name = c.name.lower()
        score = 0 if name.startswith(text.lower().strip()) else 1 if all(w in name for w in words) else 2
        hits.append((score, name, c))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [c for _, _, c in hits[:limit]]


def from_action(a: QAction, category: str = "", extra: str = "") -> Command:
    """``extra``: synonyms to search by (e.g. "export download" for export commands)."""
    sc = a.shortcut().toString()
    name = a.text().replace("&", "")
    if a.isCheckable():
        name += "  ✓" if a.isChecked() else ""
    return Command(name, a.trigger, sc or category, f"{a.statusTip()} {category} {extra}", a.icon(), a.toolTip())


class CommandSearch(QLineEdit):
    """Search box; ``provider()`` returns the current list of :class:`Command` (rebuilt on every
    search so checked states, plans and enabled actions are always current)."""

    def __init__(self, provider: Callable[[], list[Command]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CommandSearch")
        self.setPlaceholderText("Search commands  (Ctrl+Q)")
        self.setToolTip("Search commands (Ctrl+Q) – type what you want to do, e.g. 'stair', 'export staad', 'drift'")
        self.setClearButtonEnabled(True)
        self.setFixedWidth(260)
        self.provider = provider
        self.popup = QListWidget()
        self.popup.setObjectName("CommandPopup")
        self.popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        round_popup(self.popup)
        self.popup.setFocusPolicy(Qt.NoFocus)
        self.popup.setFocusProxy(self)
        self.popup.setMouseTracking(True)
        self.popup.installEventFilter(self)
        self.popup.itemClicked.connect(self._run_item)
        self.textEdited.connect(self._update)
        self.results: list[Command] = []

    # ------------------------------------------------------------------ search
    def _update(self, text: str) -> None:
        self.results = rank(self.provider(), text)
        self.popup.clear()
        if not text.strip():
            self.popup.hide()
            return
        if not self.results:
            it = QListWidgetItem(f"No command matches “{text}”")
            it.setFlags(Qt.NoItemFlags)
            self.popup.addItem(it)
        for c in self.results:
            it = QListWidgetItem(c.icon, f"{c.name}\t{c.hint}" if c.hint else c.name)
            it.setToolTip(c.tip or c.name)
            self.popup.addItem(it)
        self.popup.setCurrentRow(0)
        self._show_popup()

    def _show_popup(self) -> None:
        rows = max(1, self.popup.count())
        h = min(rows, MAX_RESULTS) * (self.popup.sizeHintForRow(0) + 2) + 8
        self.popup.setFixedSize(max(self.width(), 380), h)
        self.popup.move(self.mapToGlobal(QPoint(0, self.height() + 2)))
        self.popup.show()

    def _run_item(self, item: QListWidgetItem) -> None:
        row = self.popup.row(item)
        if 0 <= row < len(self.results):
            self.run(self.results[row])

    def run(self, c: Command) -> None:
        self.popup.hide()
        self.clear()
        self.clearFocus()
        c.run()

    # ------------------------------------------------------------------ keyboard
    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if self._nav(ev):
            return
        super().keyPressEvent(ev)

    def _nav(self, ev: QKeyEvent) -> bool:
        k = ev.key()
        if k == Qt.Key_Escape:
            self.popup.hide()
            self.clear()
            self.clearFocus()
            return True
        if k in (Qt.Key_Return, Qt.Key_Enter):
            row = self.popup.currentRow()
            if self.popup.isVisible() and 0 <= row < len(self.results):
                self.run(self.results[row])
            return True
        if k in (Qt.Key_Down, Qt.Key_Up) and self.popup.count():
            step = 1 if k == Qt.Key_Down else -1
            self.popup.setCurrentRow((self.popup.currentRow() + step) % max(len(self.results), 1))
            return True
        return False

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 (Qt API)
        """The popup grabs the keyboard while shown: forward typing to the search box."""
        if obj is self.popup and ev.type() == QEvent.ShortcutOverride:
            # the box decides as if it had the focus: typed letters (tool shortcuts S, W, A …) stay text
            self.event(ev)
            return ev.isAccepted()
        if obj is self.popup and ev.type() == QEvent.KeyPress:
            if not self._nav(ev):
                self.event(ev)  # editing keys go to the box; textEdited refreshes the list
            return True
        return super().eventFilter(obj, ev)

    def activate(self) -> None:
        """Ctrl+Q: focus the box and select its text."""
        self.setFocus(Qt.ShortcutFocusReason)
        self.selectAll()
