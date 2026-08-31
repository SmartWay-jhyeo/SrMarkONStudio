"""상태 칩 위젯. 모양은 `widgets/status_chip.py` 가 정한다.

🔴 이 파일은 판단하지 않는다. 색을 고르지도, 심각도를 정하지도 않는다.
   `chip_style()` 이 준 값을 그리기만 한다.

   그 경계가 있어야 시각 언어를 디스플레이 없이 시험할 수 있다. 여기에
   `if level == FAULT: red` 같은 것이 한 줄이라도 들어오면 그 순간부터
   화면을 띄우지 않고는 검증할 수 없게 된다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from host.gui.last_known import ChipState
from host.gui.theme import Color, Font, Space
from host.gui.widgets.status_chip import chip_style


class StatusChip(QFrame):
    """색 = 심각도, 채움 = 확인 여부."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dot = QLabel()
        self._dot.setFixedSize(12, 12)
        self._text = QLabel()

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Space.SM)
        row.addWidget(self._dot)
        row.addWidget(self._text)
        row.addStretch(1)

    def apply(self, state: ChipState) -> None:
        style = chip_style(state.level, state.verification)

        # 채움 = 확인됨, 테두리 = 명령만 나감 (스펙 §2.5).
        self._dot.setStyleSheet(
            f"background: {style.fill};"
            f"border: 2px solid {style.border};"
            f"border-radius: 6px;"
        )
        self._text.setStyleSheet(f"color: {style.border};")
        self._text.setText(state.label)


class ChipCard(QFrame):
    """칩 하나 + 값 + 곁들임을 담는 카드.

    🔴 곁들임(`last_known`)이 이 카드의 존재 이유다. 확인이 끊겼을 때
       "한 번도 모름" 과 "고장이었는데 연락이 끊김" 이 같은 회색으로 보이면
       안 된다 — 24V 가 걸린 벤치에서 완전히 다른 상황이다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

        self._chip = StatusChip()
        self._value = QLabel()
        self._value.setObjectName("value")
        self._detail = QLabel()
        self._detail.setObjectName("dim")
        self._detail.setWordWrap(True)
        self._last = QLabel()
        self._last.setObjectName("dim")

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(Space.XS)
        col.addWidget(self._chip)
        col.addWidget(self._value)
        col.addWidget(self._detail)
        col.addWidget(self._last)

    def apply(self, state: ChipState) -> None:
        self._chip.apply(state)

        self._value.setText(state.detail or "")
        self._value.setVisible(bool(state.detail))
        self._detail.setVisible(False)

        text = state.last_known.text
        self._last.setText(text or "")
        self._last.setVisible(bool(text))
        if text:
            # 나빴던 것이 확인 안 되는 상태면 회색으로 묻지 않는다.
            colour = Color.WARN if state.last_known.was_bad else Color.INK_DIM
            self._last.setStyleSheet(f"color: {colour};")

        # 지금 봐야 하는 카드는 테두리로 알린다.
        if state.needs_attention:
            self.setStyleSheet(
                f"QFrame#card {{ background: {Color.SURFACE};"
                f" border: 1px solid {Color.WARN}; border-radius: 6px; }}"
            )
        else:
            self.setStyleSheet("")


class LabeledValue(QWidget):
    """라벨 위, 값 아래. 값은 고정폭이라 갱신돼도 자릿수가 안 흔들린다."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel(label)
        self._label.setObjectName("dim")
        self._value = QLabel("—")
        self._value.setObjectName("value")

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(self._label)
        col.addWidget(self._value)

    def set_value(self, text: str, colour: str | None = None) -> None:
        self._value.setText(text)
        self._value.setStyleSheet(
            f"font-family: {Font.MONO}; color: {colour};" if colour else ""
        )
