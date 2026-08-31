"""링크 사용량 표 — 센서를 켜고 끄는 자리에 붙는 요약 카드.

🔴 **왜 켜는 자리에 있나**

   예산 표시는 원래 필드 마스크 카드 안에만 있었다. 그런데 사용자가 켜고
   끄는 것은 **채널·포트 표**다. 켜는 자리에서 안 보이면 그때그때 못 보고,
   못 보면 여유가 없어진 것을 유실이 난 뒤에야 안다.

   그래서 같은 계산을 채널 표 바로 위에도 놓는다. 🔴 **계산은 한 곳뿐이다**
   (`host/gui/link_usage.py`) — 두 화면이 각자 재면 언젠가 한쪽만 고쳐지고,
   그때 사용자는 어느 쪽을 믿어야 할지 알 수 없다.

이 파일이 하는 일은 **그리기뿐**이다. 몇 %인지, 그것이 위험한지, 무엇을
줄여야 하는지는 전부 `link_usage` 가 정하고 문자열까지 만들어 준다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from host.gui.link_usage import LinkUsage
from host.gui.qt.parts import card_title, hairline
from host.gui.theme import Color, Font, Space

#: 오른쪽으로 붙일 칸들 — 수는 자릿수가 맞아야 눈이 크기를 비교한다.
_NUMERIC_COLUMNS = (2, 3, 4)


def _cell(text: str, *, column: int, dim: bool = False,
          strong: bool = False, colour: str = "") -> QLabel:
    label = QLabel(text)
    numeric = column in _NUMERIC_COLUMNS
    if numeric:
        label.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
    ink = colour or (Color.INK_DIM if dim else Color.INK)
    label.setStyleSheet(
        # 🔴 수는 고정폭으로 쓴다. 값이 바뀔 때마다 자릿수가 흔들리면
        #    "늘었나 줄었나" 를 눈으로 못 따라간다(theme.Font.MONO 주석).
        (f"font-family: {Font.MONO}; " if numeric else "")
        + f"font-size: {Font.SIZE_SM}pt; color: {ink};"
        + (" font-weight: 700;" if strong else "")
    )
    return label


class LinkUsageCard(QFrame):
    """종류별 한 줄과 합계. 체크박스를 켜고 끄면 즉시 다시 그린다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

        self._baud = QLabel("")
        self._baud.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        self._baud.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" color: {Color.INK_DIM};")

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(card_title("링크 사용량"))
        head.addStretch(1)
        head.addWidget(self._baud)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(Space.LG)
        self._grid.setVerticalSpacing(Space.XS)
        self._grid.setContentsMargins(0, 0, 0, 0)
        # 설명 칸만 늘어난다 — 수 칸이 늘어나면 오른쪽 끝이 흔들린다.
        self._grid.setColumnStretch(1, 1)

        self._message = QLabel("")
        self._message.setWordWrap(True)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        col.setSpacing(Space.XS)
        col.addLayout(head)
        col.addWidget(hairline())
        col.addLayout(self._grid)
        col.addWidget(self._message)

    def show_usage(self, usage: LinkUsage) -> None:
        """계산 결과를 그린다. 판정은 이미 끝나 있다."""
        _clear(self._grid)
        self._baud.setText(f"{usage.baud:,} baud".replace(",", " "))

        colour = {"fault": Color.FAULT, "warn": Color.WARN}.get(usage.level, "")

        row = 0
        for line in usage.rows:
            for column, text in enumerate(line.cells()):
                self._grid.addWidget(
                    _cell(text, column=column,
                          # 🔴 알 수 없는 칸은 흐리게. 0 처럼 보이면 안 된다.
                          dim=line.event_driven or column == 1),
                    row, column)
            row += 1

        self._grid.addWidget(hairline(), row, 0, 1, 5)
        row += 1
        for column, text in enumerate(usage.total_cells()):
            self._grid.addWidget(
                _cell(text, column=column, strong=True,
                      colour=colour if column >= 3 else ""),
                row, column)

        self._message.setText(usage.message)
        self._message.setStyleSheet(
            f"font-size: {Font.SIZE_SM}pt;"
            f" color: {colour or Color.INK_DIM};")


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
