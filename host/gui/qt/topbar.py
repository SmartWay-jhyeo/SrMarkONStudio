"""상단 식별 바 — 무엇에 붙어 있고 지금 무슨 모드인가.

🔴 이 줄은 항상 같은 자리에 있어야 한다. 벤치에서 여러 보드를 옮겨 다니며
   작업하는데, 지금 보고 있는 화면이 어느 보드인지 헷갈리면 24V 를 엉뚱한
   보드에 켠다.

모드는 문구가 아니라 **모양**으로도 구분한다. CONFIG 는 채워진 배지,
RUN 은 테두리 — 설정을 바꿀 수 있는 상태인지가 한눈에 보여야 한다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from host.gui.screen import ScreenState
from host.gui.theme import Color, Font, Space


class ModeBadge(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("RUN", parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(24)
        self.setMinimumWidth(78)
        self.set_mode("RUN")

    def set_mode(self, mode: str) -> None:
        self.setText(mode)
        if mode == "CONFIG":
            # 채워진 배지 = 지금 설정을 바꿀 수 있다.
            self.setStyleSheet(
                f"background: {Color.PROBING}; color: {Color.SURFACE};"
                f" border-radius: 12px; font-size: {Font.SIZE_SM}pt;"
                f" font-weight: 700; letter-spacing: 1px;"
            )
        else:
            self.setStyleSheet(
                f"background: transparent; color: {Color.INK_DIM};"
                f" border: 1.5px solid {Color.LINE}; border-radius: 12px;"
                f" font-size: {Font.SIZE_SM}pt; font-weight: 700;"
                f" letter-spacing: 1px;"
            )


class NavButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" border-bottom: 2px solid transparent; border-radius: 0;"
            f" padding: {Space.SM}px {Space.MD}px; color: {Color.INK_DIM}; }}"
            f"QPushButton:checked {{ color: {Color.INK}; font-weight: 600;"
            f" border-bottom: 2px solid {Color.INK}; }}"
            f"QPushButton:hover {{ color: {Color.INK}; }}"
        )


class TopBar(QWidget):
    page_selected = pyqtSignal(int)

    def __init__(self, pages: tuple[str, ...],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._name = QLabel("MarkON Studio")
        self._name.setStyleSheet(
            f"font-size: {Font.SIZE_LG}pt; font-weight: 700;"
            f" color: {Color.INK};"
        )
        self._ident = QLabel("—")
        self._ident.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" color: {Color.INK_DIM};"
        )

        self._buttons: list[NavButton] = []
        nav = QHBoxLayout()
        nav.setSpacing(0)
        for i, text in enumerate(pages):
            b = NavButton(text)
            b.clicked.connect(lambda _, idx=i: self.select(idx))
            self._buttons.append(b)
            nav.addWidget(b)

        self._mode = ModeBadge()
        self._link = QLabel("보드 없음")
        self._link.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        )

        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(self._name)
        left.addWidget(self._ident)

        row = QHBoxLayout()
        row.setContentsMargins(Space.XL, Space.MD, Space.XL, 0)
        row.setSpacing(Space.LG)
        row.addLayout(left)
        row.addLayout(nav)
        row.addStretch(1)
        row.addWidget(self._link)
        row.addWidget(self._mode)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet(f"color: {Color.LINE};")
        rule.setFixedHeight(1)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(Space.SM)
        col.addLayout(row)
        col.addWidget(rule)

        self.select(0)

    def select(self, index: int) -> None:
        for i, b in enumerate(self._buttons):
            b.setChecked(i == index)
        self.page_selected.emit(index)

    def set_identity(self, port: str, device_id: str = "",
                     fw: str = "", board_rev: str = "") -> None:
        bits = [port]
        if device_id:
            bits.append(f"id {device_id}")
        if fw:
            bits.append(f"fw {fw}")
        if board_rev:
            bits.append(f"rev {board_rev}")
        self._ident.setText("  ·  ".join(bits))

    def set_mode(self, mode: str) -> None:
        self._mode.set_mode(mode)

    def set_link(self, text: str, bad: bool = False) -> None:
        self._link.setText(text)
        self._link.setStyleSheet(
            f"color: {Color.FAULT if bad else Color.INK_DIM};"
            f" font-size: {Font.SIZE_SM}pt;"
        )


    # ------------------------------------------------------------- 그리기

    def render(self, state: ScreenState) -> None:
        """`ScreenState` 만 받는다 — 뷰 계약(qt/view.py).

        🔴 `set_mode` · `set_link` 도 남겨 둔다. 명령 결과(저장 성공/실패)는
           워커 주기가 아니라 사건이라, 상태에 담기보다 그때 바로 띄우는
           편이 맞다. 뷰 계약은 "state 만으로 그릴 수 있어야 한다" 이지
           "다른 입구가 있으면 안 된다" 가 아니다.
        """
        self.set_mode(state.mode)
        self.set_link(state.link.text, bad=state.link.bad)
        if state.identity.device_id or state.identity.fw:
            self.set_identity(
                state.identity.port or self._port,
                state.identity.device_id,
                state.identity.fw,
                state.identity.board_rev,
            )