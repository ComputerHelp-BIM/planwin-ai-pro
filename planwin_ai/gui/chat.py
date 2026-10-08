"""AI assistant side panel (open/close with Ctrl+K or the ✦ toolbar button)."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QDockWidget, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout,
                               QWidget)

from .theme import PALETTES

if TYPE_CHECKING:
    from .main_window import MainWindow

CHIPS = [("Analyze", "analyze"), ("Design", "design"), ("Auto-size columns", "auto size columns"),
         ("Export STAAD", "export staad"), ("Export ETABS", "export etabs"), ("PDF report", "export pdf"), ("Help", "help")]

EXAMPLES = ["G+4 residential in Pune, 3x2 bays of 4.5 m with mumty and 1.2 m balcony",
            "Office G+6 in Bengaluru, 4 by 3 bays of 6 m, floor height 3.6",
            "Make it G+7 and use M30 Fe500",
            "Zone IV, soft soil, SBC 150, then analyze and design"]


class _Worker(QObject):
    done = Signal(str, object)

    def __init__(self, assistant, text, context):
        super().__init__()
        self.assistant = assistant
        self.text = text
        self.context = context  # model summary captured on the GUI thread

    def run(self):
        """Runs in a worker thread: only interprets the prompt (LLM call), never touches the model."""
        try:
            reply, actions = self.assistant.plan(self.text, self.context)
        except Exception as exc:
            reply, actions = f"✖ Assistant error: {exc}", []
        self.done.emit(reply, actions)


class _Input(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)


class ChatDock(QDockWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__("AI Assistant", main)
        self.main = main
        self.setObjectName("ChatDock")
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        self.provider_lbl = QLabel()
        self.provider_lbl.setStyleSheet("color: gray; font-size: 8.5pt")
        lay.addWidget(self.provider_lbl)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.anchorClicked.connect(self._anchor)
        self.view.setOpenLinks(False)
        lay.addWidget(self.view, 1)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        row = None
        for i, (label, cmd) in enumerate(CHIPS):
            if i % 4 == 0:
                row = QHBoxLayout()
                row.setSpacing(4)
                wl.addLayout(row)
            b = QPushButton(label)
            b.setObjectName("chip")
            b.clicked.connect(lambda _=False, c=cmd: self.send(c))
            row.addWidget(b)
        row.addStretch(1)
        lay.addWidget(wrap)
        self.input = _Input()
        self.input.setPlaceholderText("Describe what to build or change…  (Enter to send, Shift+Enter for new line)")
        self.input.setMaximumHeight(90)
        self.input.submit.connect(lambda: self.send(self.input.toPlainText()))
        lay.addWidget(self.input)
        send = QPushButton("Send ✦")
        send.setObjectName("primary")
        send.clicked.connect(lambda: self.send(self.input.toPlainText()))
        lay.addWidget(send)
        self.setWidget(w)
        self._thread = None
        self._busy = False
        self._msgs: list[tuple[str, str, bool]] = []
        self.refresh_provider()
        self._welcome()

    def refresh_provider(self):
        cfg = self.main.assistant.config
        name = {"offline": "Offline engine (no internet)", "claude": "Claude", "openai": "OpenAI", "ollama": "Ollama (local)"}
        self.provider_lbl.setText(f"Engine: {name.get(cfg.provider, cfg.provider)} {cfg.model}  ·  Settings ▸ AI to change")

    def _welcome(self):
        ex = "".join(f'<li><a href="ex:{html.escape(e)}">{html.escape(e)}</a></li>' for e in EXAMPLES)
        self._add("assistant", "Hi! I'm your structural assistant. Describe a building and I'll create the PlanWin plans, "
                               "FrameWin levels, loads and design. Try one of these:" + f"<ul>{ex}</ul>", raw=True)

    def _anchor(self, url):
        s = url.toString()
        if s.startswith("ex:"):
            self.input.setPlainText(s[3:])
            self.input.setFocus()

    def _add(self, role: str, text: str, raw: bool = False):
        self._msgs.append((role, text, raw))
        self.rerender()

    def rerender(self):
        """(Re)build the transcript HTML using the current theme colours."""
        pal = PALETTES[self.main.theme_name]
        parts = []
        for role, text, raw in self._msgs:
            bg = pal["chat_user"] if role == "user" else pal["chat_bot"]
            body = text if raw else html.escape(text).replace("\n", "<br>")
            who = "You" if role == "user" else "✦ Assistant"
            parts.append(f'<table width="100%" cellpadding="8" style="margin-bottom:6px"><tr><td style="background:{bg};'
                         f'color:{pal["text"]}"><b>{who}</b><br>{body}</td></tr></table>')
        css = f"<style>a {{ color: {pal['link']}; }}</style>"
        self.view.setHtml(css + "".join(parts))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def send(self, text: str):
        text = text.strip()
        if not text or self._busy:
            return
        self.input.clear()
        self._add("user", text)
        self._busy = True
        self._add("assistant", "thinking…")
        self._thread = QThread(self)
        self._worker = _Worker(self.main.assistant, text, self.main.assistant.context())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._done)
        self._worker.done.connect(self._thread.quit)
        self._thread.start()

    def _done(self, reply: str, actions):
        """Back on the GUI thread: execute the actions and refresh."""
        if self._msgs and self._msgs[-1][1] == "thinking…":
            self._msgs.pop()
        if actions:
            self.main.before_ai_change()
        final, res = self.main.run_ai_actions(reply, actions)
        self._busy = False
        self._add("assistant", final)
        self.main.after_ai_change(res)
