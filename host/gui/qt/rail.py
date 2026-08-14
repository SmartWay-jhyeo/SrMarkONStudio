"""좌측 레일 — 어두운 면. 전원과 링크 상태가 늘 여기 있다.

🔴 왜 왼쪽에 어두운 띠를 두는가.

   1. **무게중심.** 전부 흰 바탕에 얇은 선이면 눈이 앉을 데가 없다. 화면을
      띄워 보고 알았다 — 어디부터 읽어야 할지 알 수 없었다.

   2. **전원이 고아가 아니게.** 처음에는 레일 세 개를 화면 맨 아래 작은
      칩으로 두었다. 그러니 채널과 아무 관계 없어 보였는데, 실제로는
      **5V 가 없으면 채널이 하나도 안 도는** 관계다(ADS1256 의 AVDD·
      VREFP 가 5V 레일이다). 늘 보이는 자리로 올린다.

   3. 채널은 값이 흐르는 곳이고 레일은 상태가 머무는 곳이다. 성격이 다른
      둘을 같은 평면에 놓으면 무엇이 주인공인지 흐려진다.

🔴 레일 표시는 **명령 상태**다. 피드백 회로가 없으므로 `정상 ON` 이 아니라
   `ON 명령됨` 이다 (설계 원칙 4). 그 문구를 줄이지 않는다 — 짧게 쓰려고
   `ON` 이라고만 하면 화면이 거짓말을 한다.

🔴 어두운 면 위에서는 **배경을 명시적으로 투명하게** 둬야 한다. 전역
   `QWidget {{ background: GROUND }}` 이 모든 위젯에 걸리기 때문이다.
   처음에 놓쳤더니 5V·14.9V·24V 글자가 흰 상자에 가려 안 보였다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from host.gui.screen import RAILS, ScreenState
from host.gui.theme import Color, Font, Space


class _Lamp(QWidget):
    """레일 하나의 표시등. 채움 = 명령됨, 빈 테두리 = 안 냈거나 모름."""

    SIZE = 9

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = False
        self.setFixedSize(self.SIZE + 6, self.SIZE + 6)

    def set_on(self, on: bool) -> None:
        if self._on != on:
            self._on = on
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(3, 3, -3, -3)
        color = QColor(Color.VERIFIED if self._on else Color.SHELL_DIM)
        p.setPen(color)
        p.setBrush(color if self._on else Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.end()


class RailRow(QWidget):
    """레일 한 줄.

        ● 24V                        ON 명령됨
          마지막으로 알던 값 …
    """

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lamp = _Lamp()

        self._name = QLabel(name)
        # 🔴 인라인 setStyleSheet 는 부모의 `QFrame#shell QLabel` 규칙을
        #    통째로 덮는다. 배경을 안 적으면 전역 GROUND 가 살아나 흰
        #    상자가 찍힌다 — 모듈 머리의 설명 참조.
        self._name.setStyleSheet(
            f"background: transparent; color: {Color.SHELL_INK};"
            f" font-size: {Font.SIZE_MD}pt; font-weight: 600;"
        )

        self._state = QLabel("확인 불가")
        self._state.setObjectName("shellState")
        self._state.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)

        #: 🔴 연결이 끊겼을 때 **마지막으로 알던 값**. 확인이 끊겼다고 문제가
        #:    사라지지 않는다 — 24V 가 켜진 채 통신만 끊겼을 수 있고, 그때
        #:    화면이 "확인 불가" 만 말하면 사용자는 안심한다.
        self._last = QLabel("")
        self._last.setObjectName("shellDim")
        self._last.setVisible(False)
        self._last.setContentsMargins(_Lamp.SIZE + 6 + Space.SM, 0, 0, 0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(Space.SM)
        top.addWidget(self._lamp)
        top.addWidget(self._name)
        top.addStretch(1)
        top.addWidget(self._state)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, Space.XS, 0, Space.XS)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self._last)

    def set_commanded(self, on: bool | None, last_known: str = "") -> None:
        """`None` 이면 지금은 모른다. `last_known` 은 그때 보여 줄 문구다."""
        self._lamp.set_on(bool(on))
        if on is None:
            self._state.setText("확인 불가")
            colour = Color.SHELL_DIM
        else:
            # 🔴 `ON` 이 아니라 `ON 명령됨`. 피드백 회로가 없어 실제로
            #    전압이 나오는지 보드도 모른다 (설계 원칙 4).
            self._state.setText("ON 명령됨" if on else "OFF 명령됨")
            colour = Color.VERIFIED if on else Color.SHELL_DIM
        self._state.setStyleSheet(
            f"background: transparent; color: {colour};"
            f" font-size: {Font.SIZE_SM}pt;"
        )
        self._last.setText(last_known)
        self._last.setVisible(bool(last_known))


class _Section(QWidget):
    """구획 제목 + 그 아래 가는 선.

    🔴 `QFrame.HLine` 을 쓰지 않는다. 어두운 면에서 그것은 두꺼운 밝은
       띠로 렌더링돼 제목보다 눈에 띈다 — 화면에서 실제로 그랬다.
       1px 짜리 위젯을 직접 칠한다.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lab = QLabel(text)
        lab.setObjectName("shellSection")

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {Color.SHELL_LINE};")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, Space.XS)
        v.setSpacing(Space.XS)
        v.addWidget(lab)
        v.addWidget(line)


class Rail(QFrame):
    """좌측 레일 전체."""

    #: 사용자가 화면에서 레일을 눌렀을 때. 아직 붙이지 않았지만 자리를 둔다.
    railToggled = pyqtSignal(str, bool)

    WIDTH = 236

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 🔴 `QFrame` 이어야 스타일시트 배경이 먹는다. 순수 `QWidget` 은
        #    스스로 그리지 않는다 — 처음에 QWidget 으로 두었더니 레일이
        #    흰색으로 남고 라벨만 어둡게 찍혀 얼룩졌다.
        self.setObjectName("shell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.MD, Space.LG, Space.MD, Space.MD)
        lay.setSpacing(0)

        lay.addWidget(_Section("전원"))
        # 🔴 목록과 순서를 여기서 만들지 않는다. screen.RAILS 가 유일한
        #    출처다 — 두 곳에 적으면 언젠가 갈리고, 그때 화면과 상태가
        #    다른 레일을 가리킨다.
        self._rails = {key: RailRow(label) for key, label in RAILS}
        for row in self._rails.values():
            lay.addWidget(row)

        # 🔴 마크다운을 쓰지 않는다. QLabel 은 `**...**` 를 그대로 찍는다 —
        #    실제로 화면에 별표가 그대로 나왔다. 강조가 필요하면 서식으로
        #    하거나, 여기처럼 문장으로 푼다.
        note = QLabel("피드백 회로가 없다. 화면이 말하는 것은 보드가 "
                      "명령한 상태이지 실제 전압이 아니다.")
        note.setObjectName("shellDim")
        note.setWordWrap(True)
        note.setContentsMargins(0, Space.SM, 0, 0)
        lay.addWidget(note)

        lay.addSpacing(Space.XL)
        lay.addWidget(_Section("링크"))
        self._link = QLabel("—")
        self._link.setObjectName("shellMono")
        lay.addWidget(self._link)
        self._drops = QLabel("")
        self._drops.setObjectName("shellMono")
        self._drops.setVisible(False)
        lay.addWidget(self._drops)

        lay.addStretch(1)

    # ------------------------------------------------------------- 그리기

    def render(self, state: ScreenState) -> None:
        """`ScreenState` 만 받는다 — 뷰 계약(qt/view.py).

        🔴 예전에는 `set_rails(dashboard.rail_states(...))` 였다. 레일이
           레일 값을 얻으려고 대시보드를 거쳤고, 그래서 배치를 바꾸면
           데이터 배선까지 다시 짜야 했다.
        """
        for rail in state.rails:
            row = self._rails.get(rail.key)
            if row is not None:
                row.set_commanded(rail.commanded, rail.last_known)

        self._link.setText(state.link.text or "—")
        self._link.setStyleSheet(
            f"background: transparent;"
            f" color: {Color.FAULT if state.link.bad else Color.SHELL_DIM};"
            f" font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
        )
