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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from host.gui.qt.parts import hairline
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


class CtlModeSwitch(QWidget):
    """제어 모드 전환 — `테스트` / `운전` (규격 §6.4).

    🔴 상단의 `CONFIG` 배지와 **다른 축**이다. 저쪽은 하트비트로 관측되는
       것이고 이쪽은 사람이 선언하는 것이라, 나란히 두되 생김새를 다르게 한다.

    🔴 지금 어느 쪽인지가 **틀리면 안 되는** 표시다. 테스트인 줄 알고 공정을
       돌리거나 운전 중인 줄 모르고 밸브를 흔드는 것이 이 시스템에서 가장
       나쁜 사고다. 그래서 선택된 쪽을 채워 그린다.
    """

    requested = pyqtSignal(str)          # "TEST" | "ACTIVE"

    MODES = (("TEST", "테스트"), ("ACTIVE", "운전"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Space.XS)
        for value, label in self.MODES:
            b = QPushButton(label)
            b.setObjectName("ctlmode")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=value: self.requested.emit(v))
            self._buttons[value] = b
            row.addWidget(b)
        self.set_mode("ACTIVE")

    def set_mode(self, mode: str) -> None:
        for value, b in self._buttons.items():
            b.setChecked(value == mode)
            # 🔴 테스트 쪽만 경고색으로 채운다. 운전이 평상 상태이므로
            #    거기에 색을 쓰면 색이 뜻을 잃는다.
            b.setProperty("danger", value == "TEST" and value == mode)
            b.style().unpolish(b)
            b.style().polish(b)


class TopBar(QWidget):
    page_selected = pyqtSignal(int)
    #: 사용자가 제어 모드를 바꾸려 한다. 실제 전환은 보드가 한다.
    ctl_mode_requested = pyqtSignal(str)

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
        self._ctl = CtlModeSwitch()
        self._ctl.requested.connect(self.ctl_mode_requested)
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
        row.addWidget(self._ctl)
        row.addSpacing(Space.MD)
        row.addWidget(self._link)
        row.addWidget(self._mode)

        rule = hairline()

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
        # 🔴 보드가 말한 것을 그린다. 사용자가 누른 것이 아니다 — 명령이
        #    거부될 수 있고(RUN 에서 TEST 진입), 그때 화면이 눌린 대로
        #    보여 주면 실제와 다른 모드를 믿게 된다.
        self._ctl.set_mode(state.ctl_mode)
        self.set_link(state.link.text, bad=state.link.bad)
        if state.identity.device_id or state.identity.fw:
            self.set_identity(
                state.identity.port or self._port,
                state.identity.device_id,
                state.identity.fw,
                state.identity.board_rev,
            )