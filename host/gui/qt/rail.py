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

import time

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from host.gui.qt.parts import hairline
from host.gui.screen import RAILS, ScreenState
from host.gui.theme import Color, Font, Space


class _Lamp(QWidget):
    """레일 하나의 표시등. 채움 = 명령됨, 빈 테두리 = 안 냈거나 모름.

    누르고 있는 동안에는 바깥에 진행 링이 그려진다 — 얼마나 더 쥐고 있어야
    하는지 손이 알아야 한다.
    """

    SIZE = 9
    BOX = SIZE + 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = False
        self._progress = 0.0
        self.setFixedSize(self.BOX, self.BOX)

    def set_on(self, on: bool) -> None:
        if self._on != on:
            self._on = on
            self.update()

    def set_progress(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if abs(self._progress - fraction) > 0.005:
            self._progress = fraction
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._progress > 0.0:
            ring = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            pen = QPen(QColor(Color.PROBING), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # 12시에서 시계방향. Qt 의 각도 단위는 1/16 도다.
            p.drawArc(ring, 90 * 16, -int(self._progress * 360 * 16))

        r = self.rect().adjusted(5, 5, -5, -5)
        color = QColor(Color.VERIFIED if self._on else Color.SHELL_DIM)
        p.setPen(color)
        p.setBrush(color if self._on else Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.end()


class RailRow(QWidget):
    """레일 한 줄. 누르고 있으면 반대 상태를 명령한다.

        ● 24V                        ON 명령됨
          마지막으로 알던 값 …

    🔴 **왜 클릭이 아니라 누르고 있기인가.**

       24 V 는 잘못 누르면 곤란한 출력이고, 예전에는 이것을 만지려면 설정
       화면까지 건너가야 했다. 그 거리가 안전장치 역할을 하고 있었다.

       대시보드로 끌어오면서 그 자리를 무엇으로 채울지가 문제였는데, 산업
       HMI 가 오래 전에 정한 답이 있다 — hold-to-run. 손을 대고 있는 동안만
       동작하고 떼면 멈춘다. 확인 대화상자와 다른 점은 **의도가 계속
       확인된다**는 것이고, 대화상자처럼 습관적으로 넘겨 버릴 수가 없다.

    🔴 `commanded` 가 `None` 이면 아무것도 보내지 않는다. 모르는 상태에서
       토글하면 무엇의 반대를 보내는지 알 수 없다 — 24 V 를 켜려다 끄게 된다.
    """

    #: (설정 키, 켤 것인가). 누르고 있기가 끝까지 갔을 때만 나간다.
    toggled = pyqtSignal(str, bool)

    HOLD_S = 0.7

    def __init__(self, name: str, key: str = "",
                 parent: QWidget | None = None, *,
                 hold_s: float | None = None,
                 monotonic=time.monotonic) -> None:
        super().__init__(parent)
        self._key = key
        self._hold_s = self.HOLD_S if hold_s is None else hold_s
        self._monotonic = monotonic
        self._pressed_at: float | None = None
        self._commanded: bool | None = None
        self._last_known = ""

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

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ticker = QTimer(self)
        self._ticker.setInterval(16)
        self._ticker.timeout.connect(self._on_tick)

    # --------------------------------------------------------- 누르고 있기

    def begin_hold(self) -> None:
        """마우스·키보드가 공통으로 부르는 자리. 시험도 여기로 들어온다."""
        if self._commanded is None:
            return
        self._pressed_at = self._monotonic()
        self._ticker.start()

    def end_hold(self) -> None:
        started, self._pressed_at = self._pressed_at, None
        self._ticker.stop()
        self._lamp.set_progress(0.0)
        self._render_state()
        if started is None:
            return
        if self._monotonic() - started >= self._hold_s:
            self.toggled.emit(self._key, not self._commanded)

    def _on_tick(self) -> None:
        if self._pressed_at is None:
            return
        held = self._monotonic() - self._pressed_at
        self._lamp.set_progress(held / self._hold_s)
        # 🔴 링이 9 px 짜리 점 둘레라 너무 조용하다. 누르고 있다는 사실과
        #    떼면 취소된다는 사실을 글자로도 말한다 — 이 앱에서 가장
        #    되돌리기 어려운 동작이고, 지금 무슨 일이 벌어지는지 손이
        #    확실히 알아야 한다.
        want = "켠다" if not self._commanded else "끈다"
        self._state.setText(f"{want} · 떼면 취소")
        self._state.setStyleSheet(
            f"background: transparent; color: {Color.PROBING};"
            f" font-size: {Font.SIZE_SM}pt;"
        )
        if held >= self._hold_s:
            # 다 찼으면 손을 떼기 전에 보낸다 — 계속 쥐고 있는데 아무 일도
            # 일어나지 않으면 안 먹은 것으로 읽힌다.
            self.end_hold()

    def mousePressEvent(self, event) -> None:       # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_hold()

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802
        self.end_hold()

    def leaveEvent(self, event) -> None:            # noqa: N802
        # 누른 채 밖으로 끌고 나가면 취소한다 — 손을 뗀 것과 같다.
        self._pressed_at = None
        self._ticker.stop()
        self._lamp.set_progress(0.0)

    def keyPressEvent(self, event) -> None:         # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            if not event.isAutoRepeat():
                self.begin_hold()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:       # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            if not event.isAutoRepeat():
                self.end_hold()
            return
        super().keyReleaseEvent(event)

    # --------------------------------------------------------- 그리기

    def set_commanded(self, on: bool | None, last_known: str = "") -> None:
        """`None` 이면 지금은 모른다. `last_known` 은 그때 보여 줄 문구다."""
        self._commanded = on
        self._last_known = last_known
        self._lamp.set_on(bool(on))
        # 누르고 있는 동안에는 그 문구를 덮지 않는다 — 워커가 100 ms 마다
        # 다시 그리는데 그때마다 "떼면 취소" 가 지워지면 깜빡인다.
        if self._pressed_at is None:
            self._render_state()

    def _render_state(self) -> None:
        on = self._commanded
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
        self._last.setText(self._last_known)
        self._last.setVisible(bool(self._last_known))


class _Section(QWidget):
    """구획 제목 + 그 아래 가는 선.

    🔴 `QFrame.HLine` 을 쓰지 않는다. 어두운 면에서 그것은 두꺼운 밝은
       띠로 렌더링돼 제목보다 눈에 띈다 — 화면에서 실제로 그랬다.
       1px 짜리 위젯을 직접 칠한다(`qt/parts.hairline`). 같은 함정이 밝은
       면에도 있어서 지금은 네 화면이 그 부품을 함께 쓴다.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lab = QLabel(text)
        lab.setObjectName("shellSection")

        line = hairline(Color.SHELL_LINE)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, Space.XS)
        v.setSpacing(Space.XS)
        v.addWidget(lab)
        v.addWidget(line)


class Rail(QFrame):
    """좌측 레일 전체."""

    #: 사용자가 레일을 누르고 있다가 끝까지 갔을 때. (설정 키, 켤 것인가)
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
        self._rails = {key: RailRow(label, key) for key, label in RAILS}
        for row in self._rails.values():
            row.toggled.connect(self.railToggled)
            lay.addWidget(row)

        # 🔴 마크다운을 쓰지 않는다. QLabel 은 `**...**` 를 그대로 찍는다 —
        #    실제로 화면에 별표가 그대로 나왔다. 강조가 필요하면 서식으로
        #    하거나, 여기처럼 문장으로 푼다.
        note = QLabel("누르고 있으면 켜고 끈다. 피드백 회로가 없어 화면이 "
                      "말하는 것은 보드가 명령한 상태이지 실제 전압이 아니다.")
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
