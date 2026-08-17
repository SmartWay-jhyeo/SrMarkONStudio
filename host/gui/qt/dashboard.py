"""대시보드 — 계기판.

🔴 화면의 중심은 **채널 스트립**이다. 이 보드가 하는 일이 7채널 4–20 mA
   수집이고, 보드 위에서 J3~J9 가 한 줄로 늘어서 있다. 화면도 그 순서로
   늘어놓는다 — 사용자가 보드를 보면서 화면을 보기 때문이다.

전원 레일은 카드 세 장이 아니라 **띠 하나**다. 측정값이 아니라 불리언
셋이고, 채널과 같은 무게로 그리면 무엇이 이 화면의 주인공인지 흐려진다.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from host.gui.last_known import StateHistory, build_chip_state
from host.gui.qt.channel_card import ChannelCard
from host.gui.qt.sensor_card import SensorCard
from host.gui.qt.parts import hairline
from host.gui.screen import (
    AIN_COUNT,
    CONNECTOR_OFFSET,
    RAILS,
    ScreenState,
    summarize,
)
from host.gui.theme import Color, Font, Space
from host.gui.widgets.status_chip import (
    Level,
    Verification,
    chip_style,
    rail_label,
)

# 🔴 커넥터 오프셋·채널 수·레일 목록은 host/gui/screen.py 가 유일한
#    출처다. 여기서 다시 정의하면 두 곳이 갈릴 수 있고, 그때 화면과
#    상태가 서로 다른 채널을 가리킨다.


class SectionTitle(QWidget):
    """제목 + 오른쪽 보조 문구. 가로줄로 구획을 나눈다.

    🔴 그림자를 쓰지 않는다. 경계선과 여백으로 층을 나눈다 — 계기 패널의
       어법이고, 흰 바탕에서 그림자는 지저분해진다.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel(text)
        self._title.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
            f" font-weight: 600; letter-spacing: 1px;"
        )
        self._aside = QLabel("")
        self._aside.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        )
        rule = hairline()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Space.SM)
        row.addWidget(self._title)
        row.addWidget(rule, 1)
        row.addWidget(self._aside)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addLayout(row)

    def set_aside(self, text: str, colour: str | None = None) -> None:
        self._aside.setText(text)
        self._aside.setStyleSheet(
            f"color: {colour or Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        )


class RailPill(QFrame):
    """전원 레일 하나. 알약 모양 — 채널 게이지와 무게가 달라야 한다.

    🔴 `QWidget` 이 아니라 `QFrame` 이다. 순수 `QWidget` 서브클래스는
       스타일시트의 배경·테두리를 스스로 그리지 않는다 — `paintEvent` 에서
       `QStyleOption` 을 직접 그려 줘야 한다. `QFrame` 은 그것을 해 준다.
       처음에 `QWidget` 으로 두었더니 테두리가 통째로 사라졌다.

    🔴 절대 채워지지 않는다. 피드백 회로가 없으므로 GPIO 를 올렸다는 것과
       실제로 24V 가 나온다는 것은 다른 사실이고, 보드는 후자를 모른다.
       테두리로 그려 "명령했을 뿐" 임을 형태로 말한다.
    """

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = QLabel(label)
        self._name.setStyleSheet(f"font-weight: 600; color: {Color.INK};")
        self._state = QLabel("확인 불가")
        self._state.setStyleSheet(
            f"color: {Color.UNKNOWN}; font-size: {Font.SIZE_SM}pt;"
        )
        self._last = QLabel("")
        self._last.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        col.setSpacing(1)
        col.addWidget(self._name)
        col.addWidget(self._state)
        col.addWidget(self._last)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._apply_border(Color.LINE)

    def _apply_border(self, colour: str) -> None:
        self.setStyleSheet(
            f"RailPill {{ background: {Color.SURFACE};"
            f" border: 1.5px solid {colour}; border-radius: 16px; }}"
        )

    def apply(self, state) -> None:
        style = chip_style(state.level, state.verification)
        self._apply_border(style.border)
        self._state.setText(state.detail or "")
        self._state.setStyleSheet(
            f"color: {style.border}; font-size: {Font.SIZE_SM}pt;"
        )
        text = state.last_known.text or ""
        self._last.setText(text)
        self._last.setVisible(bool(text))
        if text:
            colour = Color.WARN if state.last_known.was_bad else Color.INK_DIM
            self._last.setStyleSheet(
                f"color: {colour}; font-size: {Font.SIZE_SM}pt;"
            )


class _Summary(QFrame):
    """수집 요약 — 카드 일곱 장을 한 줄로.

    🔴 채널 카드와 같은 무게로 그리지 않는다. 값이 아니라 **값에 대한
       사실**이라서, 나란히 두면 무엇이 이 화면의 주인공인지 흐려진다.
       테두리 없이 글자만 얹는다.

    🔴 고장 수는 있을 때만 쓴다. 늘 `단선 0` 을 띄우면 눈이 그 자리를 읽지
       않게 되고, 정작 1 이 됐을 때도 안 보인다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._title = QLabel("수집")
        self._title.setStyleSheet(
            f"color: {Color.INK_FAINT}; font-size: {Font.SIZE_SM}pt;"
            f" font-weight: 700; letter-spacing: 1.2px;"
        )
        self._rows = QVBoxLayout()
        self._rows.setSpacing(2)
        self._lines: list[QLabel] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(Space.SM)
        col.addWidget(self._title)
        col.addWidget(hairline())
        col.addLayout(self._rows)
        col.addStretch(1)

    def _line(self, index: int) -> QLabel:
        while len(self._lines) <= index:
            lab = QLabel("")
            self._lines.append(lab)
            self._rows.addWidget(lab)
        return self._lines[index]

    def render(self, state: ScreenState) -> None:
        summary = summarize(state.channels)
        facts: list[tuple[str, str, str]] = [
            ("값이 오는 채널", f"{summary.live} / {summary.total}",
             Color.INK if summary.live else Color.INK_FAINT),
        ]
        if summary.interval_ms is not None:
            facts.append(("표본 간격", f"{summary.interval_ms:.0f} ms",
                          Color.INK_DIM))
        if summary.broken:
            facts.append(("단선", str(summary.broken), Color.FAULT))
        if summary.over:
            facts.append(("범위 밖", str(summary.over), Color.WARN))

        for i, (name, value, colour) in enumerate(facts):
            lab = self._line(i)
            lab.setText(f"{name}    {value}")
            lab.setStyleSheet(
                f"color: {colour}; font-size: {Font.SIZE_SM}pt;"
                f" font-family: {Font.MONO};"
            )
            lab.setVisible(True)
        for i in range(len(facts), len(self._lines)):
            self._lines[i].setVisible(False)


class Dashboard(QWidget):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history = StateHistory()
        self._rails: dict[str, RailPill] = {}
        self._cards: list[ChannelCard] = []

        # ── 채널 (시그니처) ──────────────────────────────────────
        #
        # 🔴 한 줄에 셋. 예전에는 넷이었는데, 1936px 화면에서 카드 하나가
        #    450px 가 되면서 게이지만 길쭉해지고 값은 왼쪽 구석에 몰렸다.
        #    셋이면 카드가 계기 비율에 가까워지고, 7채널이 3+3+1 이 아니라
        #    3+3+1 로 떨어져 마지막 줄에 여백이 남는다 — 그 여백은 J9 가
        #    보드에서도 줄 끝이라는 사실과 맞는다.
        self._ch_title = SectionTitle("아날로그 입력")
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.MD)
        grid.setVerticalSpacing(Space.MD)
        cols = 3
        for ch in range(AIN_COUNT):
            card = ChannelCard(f"J{ch + CONNECTOR_OFFSET}")
            # 🔴 커서를 화면 전체로 퍼뜨린다. 한 계기 위에서 움직인 마우스가
            #    일곱 계기 모두를 같은 시각으로 돌려세운다 — 채널 사이의
            #    관계는 이 방법으로만 보인다.
            card.gauge.cursorMoved.connect(self._on_cursor)
            self._cards.append(card)
            grid.addWidget(card, ch // cols, ch % cols)
        # 🔴 마지막 줄의 남는 칸. 예전에는 빈 위젯으로 자리만 잡아 두었고,
        #    화면 오른쪽 아래에 흰 구멍이 남았다. 수집이 지금 어떻게 되고
        #    있는지를 거기 넣는다 — 카드 일곱 장을 한 줄로 요약한 것이라
        #    자리도 맞다.
        self._summary = _Summary()
        empty = cols - AIN_COUNT % cols
        grid.addWidget(self._summary, AIN_COUNT // cols,
                       AIN_COUNT % cols, 1, empty)
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        # 🔴 줄에도 늘어날 몫을 준다. 예전에는 격자가 위에 붙고 아래로 화면
        #    끝까지 빈 흰 바탕이 남았다. 이제 카드가 높이를 나눠 갖고,
        #    늘어난 자리는 트레이스가 먹는다.
        for r in range((AIN_COUNT + cols - 1) // cols):
            grid.setRowStretch(r, 1)

        # 🔴 전원 레일은 이제 좌측 레일(qt/rail.py)에 있다. 여기서는 그리지
        #    않는다 — 5V 가 없으면 채널이 하나도 안 도는 관계라, 채널 아래에
        #    두는 것보다 늘 보이는 자리에 있는 편이 맞다.
        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        col.setSpacing(Space.SM)
        # ── I2C 센서 ─────────────────────────────────────────────
        #
        # 🔴 자리를 미리 잡아 두지 않는다. 아날로그는 채널이 일곱으로 고정
        #    이지만 I2C 는 무엇이 꽂히느냐에 따라 카드 수가 달라진다 —
        #    온습도 하나가 카드 둘을 만든다. 빈 카드를 여섯 장 깔아 두면
        #    화면 절반이 "—" 가 된다.
        self._sensor_title = SectionTitle("I2C 센서")
        self._sensor_grid = QGridLayout()
        self._sensor_grid.setHorizontalSpacing(Space.MD)
        self._sensor_grid.setVerticalSpacing(Space.MD)
        self._sensor_cards: dict[tuple[str, str], SensorCard] = {}
        for c in range(cols):
            self._sensor_grid.setColumnStretch(c, 1)
        self._sensor_title.setVisible(False)

        col.addWidget(self._ch_title)
        col.addSpacing(Space.XS)
        col.addLayout(grid, 1)
        col.addSpacing(Space.SM)
        col.addWidget(self._sensor_title)
        col.addSpacing(Space.XS)
        col.addLayout(self._sensor_grid)

    # ------------------------------------------------------------- 커서

    def _on_cursor(self, at_ms: int) -> None:
        """한 계기에서 나온 **시각**을 일곱 계기 전부에 건다.

        🔴 시각으로 거는 것이 요점이다. 채널마다 수집 주기가 따로라
           인덱스로 맞추면 서로 다른 순간을 보여 주면서 "같은 순간" 이라고
           말하게 된다. 각 계기가 자기 표본 중 그 시각에 가장 가까운 것을
           고른다.
        """
        cursor = None if at_ms < 0 else at_ms
        for card in self._cards:
            card.gauge.set_cursor(cursor)

    # ------------------------------------------------------------- 갱신

    def render(self, state: ScreenState) -> None:
        """`ScreenState` 만 받는다 — 뷰 계약(qt/view.py).

        🔴 예전에는 채널마다 `update_channel(ch, ma, level=..., ...)` 를
           불러야 했고, 그 인자를 만드는 코드가 MainWindow 에 있었다.
           배치를 바꾸면 그 변환 코드까지 따라다녔다.
        """
        for ch in state.channels:
            if not (0 <= ch.index < len(self._cards)):
                continue
            card = self._cards[ch.index]
            card.gauge.set_reading(
                ch.ma, level=ch.level, verification=ch.verification,
                value=ch.value, unit=ch.unit, note=ch.note,
                trace=ch.trace, trace_t=ch.trace_t, span=ch.span,
            )
            # 🔴 카드 띠와 게이지가 **같은** 판정을 쓴다. 따로 계산하면
            #    언젠가 갈리고, 그때 어느 쪽을 믿어야 할지 알 수 없다.
            card.set_state(ch.level, ch.verification)

        self._render_sensors(state)
        self._summary.render(state)

    def _render_sensors(self, state: ScreenState) -> None:
        """🔴 카드를 매번 새로 만들지 않는다. 텔레메트리는 초당 열 번 오는데
           그때마다 위젯을 새로 만들면 마우스가 카드 위에 있을 때 계속
           밑에서 사라진다 — 값을 읽을 수가 없다."""
        cols = 3
        self._sensor_title.setVisible(bool(state.sensors))
        for i, sensor in enumerate(state.sensors):
            key = (sensor.connector, sensor.quantity)
            card = self._sensor_cards.get(key)
            if card is None:
                card = SensorCard()
                self._sensor_cards[key] = card
                self._sensor_grid.addWidget(card, i // cols, i % cols)
            card.render(sensor)
        self._ch_title.set_aside(
            f"{AIN_COUNT}채널 · 4–20 mA" if state.reachable else "확인 불가",
            Color.FAULT if not state.reachable else None,
        )

    def set_link(self, text: str, bad: bool = False) -> None:
        self._ch_title.set_aside(text, Color.FAULT if bad else None)
