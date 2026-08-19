"""진단 화면 — `$STAT` 이 말하는 것을 사람이 읽는 말로 보여준다.

🔴 **여기에는 판정이 없다.** 무엇이 경고인지, `"hsi"` 가 무슨 뜻인지,
   항목이 빠졌을 때 뭐라고 쓸지는 전부 `host/gui/diagnostics.py` 가 정한다.
   이 파일은 그 결과를 배치하고 색을 입히기만 한다 — `screen.py` ↔
   `qt/dashboard.py` 와 같은 관계다.

   그 경계가 있어야 "HSI 는 경고다" 를 디스플레이 없이 시험할 수 있다.
   여기에 `if src == "hsi"` 가 한 줄이라도 들어오면 그 순간부터 화면을
   띄우지 않고는 검증할 수 없게 된다.

🔴 화면 구성은 **경고를 먼저 읽게** 되어 있다. 맨 위 한 줄이 "주의 N 건"
   이고, 문제 있는 항목만 색 있는 왼쪽 띠를 단다. 나머지는 조용한 회색이라
   훑어 내려가다 눈에 걸리는 것이 곧 봐야 할 것이다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from host.gui.diagnostics import DiagnosticsState, Group, Reading
from host.gui.qt.parts import card_title, hairline
from host.gui.theme import Color, Font, Space
from host.gui.widgets.status_chip import chip_style

#: 이름표 열 너비(px). 값이 세로로 줄맞춤돼야 훑어 내려갈 수 있다.
LABEL_WIDTH = 150


class ReadingRow(QFrame):
    """진단 항목 한 줄 — 이름표 · 값 · 뜻."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diagRow")

        self._label = QLabel()
        self._label.setObjectName("dim")
        self._label.setFixedWidth(LABEL_WIDTH)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._value = QLabel()
        self._value.setWordWrap(True)
        self._note = QLabel()
        self._note.setObjectName("dim")
        self._note.setWordWrap(True)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        right.addWidget(self._value)
        right.addWidget(self._note)

        row = QHBoxLayout(self)
        row.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.XS)
        row.setSpacing(Space.MD)
        row.addWidget(self._label)
        row.addLayout(right, 1)

    def apply(self, r: Reading) -> None:
        style = chip_style(r.level, r.verification)
        self._label.setText(r.label)
        self._value.setText(r.value)
        self._value.setStyleSheet(
            f"font-family: {Font.MONO}; font-weight: 600;"
            f" color: {style.border};"
        )
        self._note.setText(r.note)
        self._note.setVisible(bool(r.note))

        # 🔴 경고만 왼쪽에 띠를 단다. 전부 칠하면 훑을 때 아무것도 안 걸린다.
        if r.warning:
            self.setStyleSheet(
                f"QFrame#diagRow {{ background: {Color.SURFACE};"
                f" border-left: 3px solid {style.border}; }}"
            )
        else:
            self.setStyleSheet(
                "QFrame#diagRow { border-left: 3px solid transparent; }"
            )


class GroupCard(QFrame):
    """구획 하나 — 제목 + 항목들."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._rows: dict[str, ReadingRow] = {}

        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self._body.setSpacing(Space.XS)
        self._body.addWidget(card_title(title))
        self._body.addWidget(hairline())

    def apply(self, group: Group) -> None:
        # 🔴 항목 목록이 그대로면 위젯을 다시 만들지 않는다. 큐 항목은 채널을
        #    켜고 끌 때만 바뀌는데, 매 갱신마다 위젯을 새로 만들면 스트림
        #    화면이 겪은 것과 같은 함정에 빠진다(qt/stream_view.py 머리말).
        keys = [r.key for r in group.readings]
        if keys != list(self._rows):
            for w in self._rows.values():
                w.setParent(None)
            self._rows = {}
            for r in group.readings:
                row = ReadingRow()
                self._rows[r.key] = row
                self._body.addWidget(row)
        for r in group.readings:
            self._rows[r.key].apply(r)


class DiagnosticsPage(QWidget):
    """`$STAT` 진단 화면."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, GroupCard] = {}

        self._headline = QLabel()
        self._headline.setObjectName("h1")
        self._age = QLabel()
        self._age.setObjectName("dim")

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._headline)
        head.addStretch(1)
        head.addWidget(self._age)

        self._cards_box = QVBoxLayout()
        self._cards_box.setContentsMargins(0, 0, 0, 0)
        self._cards_box.setSpacing(Space.MD)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(Space.MD)
        inner_layout.addLayout(self._cards_box)
        inner_layout.addStretch(1)

        inner = QWidget()
        inner.setLayout(inner_layout)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        col.setSpacing(Space.SM)
        col.addLayout(head)
        col.addWidget(scroll, 1)

    def render(self, state: DiagnosticsState) -> None:
        self._headline.setText(state.headline)
        # 🔴 경고가 있을 때만 색을 준다. 늘 빨간 제목은 며칠이면 안 보인다.
        self._headline.setStyleSheet(
            f"color: {Color.FAULT};" if state.error
            else f"color: {Color.WARN};" if state.warnings
            else ""
        )

        # 🔴 "언제 읽은 값인가" 를 늘 말한다. 낡은 값을 지금 값처럼 그리면
        #    화면이 거짓말을 한다 — 특히 링크가 끊긴 뒤가 그렇다.
        age = state.age_text
        self._age.setText(
            "" if not age else
            (f"{age}" if state.fresh else f"{age} · 지금 값이 아니다")
        )
        self._age.setStyleSheet(
            "" if state.fresh else f"color: {Color.WARN};"
        )

        for group in state.groups:
            card = self._cards.get(group.key)
            if card is None:
                card = GroupCard(group.title)
                self._cards[group.key] = card
                self._cards_box.addWidget(card)
            card.apply(group)
