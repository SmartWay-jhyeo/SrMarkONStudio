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
    DIN_PORTS,
    RAILS,
    DinState,
    GnssState,
    ScreenState,
    fix_label,
    gnss_position_text,
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


class DinPill(QFrame):
    """디지털 입력 하나 (J18~J20, 규격 §7.6). `RailPill` 과 같은 모양이지만
    **누를 수 없다** — 이것은 보드가 관측한 값이지 사용자가 명령하는 값이
    아니다. 옵토 신호를 EXTI 로 읽어 보내므로 켜고 끄는 주체가 반대편이다.

    🔴 이 화면에서 유일하게 `Verification.VERIFIED` 가 실제로 채워지는
       자리다 — 전원 레일은 피드백이 없어 영원히 COMMANDED 지만, din 은
       보드가 직접 측정한 값이다. 그래서 `rail_label` 이 내는 "정상 ON"
       문구가 여기서는 거짓말이 아니다.
    """

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = QLabel(label)
        self._name.setStyleSheet(f"font-weight: 600; color: {Color.INK};")
        self._state = QLabel("확인 불가")
        self._state.setStyleSheet(
            f"color: {Color.UNKNOWN}; font-size: {Font.SIZE_SM}pt;"
        )
        # 🔴 "마지막으로 바뀐 시각" — 보드가 찍은 원시 ms(`t`) 를 그대로
        #    보여 준다. 사람이 읽는 날짜·시각으로 바꾸지 않는다: time_source
        #    가 device_clock 이면 이 값은 UTC epoch 이 아니라 부팅 후 경과
        #    ms 다(규격 §7.1.2). 여기서 임의로 "n초 전" 을 지어내면, GNSS/PPS
        #    가 들어오기 전까지는 근거 없는 정밀도를 주장하는 것이 된다.
        self._changed = QLabel("")
        self._changed.setStyleSheet(
            f"color: {Color.INK_FAINT}; font-size: {Font.SIZE_SM}pt;"
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
        col.addWidget(self._changed)
        col.addWidget(self._last)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._apply_border(Color.LINE)

    def _apply_border(self, colour: str) -> None:
        self.setStyleSheet(
            f"DinPill {{ background: {Color.SURFACE};"
            f" border: 1.5px solid {colour}; border-radius: 16px; }}"
        )

    def render(self, d: DinState) -> None:
        if d.state is None:
            level, verification = Level.IDLE, Verification.UNKNOWN
        else:
            level = Level.OK if d.state else Level.IDLE
            verification = Verification.VERIFIED

        style = chip_style(level, verification)
        self._apply_border(style.border)
        text = ("확인 불가" if d.state is None
                else rail_label(d.state, verification))
        self._state.setText(text)
        self._state.setStyleSheet(
            f"color: {style.border}; font-size: {Font.SIZE_SM}pt;"
        )
        self._changed.setText(
            "바뀐 적 없음" if d.changed_at is None
            else f"바뀜: t={d.changed_at:,} ms"
        )
        self._last.setText(d.last_known)
        self._last.setVisible(bool(d.last_known))


class GnssPanel(QFrame):
    """GNSS 측위 (J16, 규격 §7.8).

    🔴 카드 여럿이 아니라 **띠 하나**다. 위도만 있고 경도가 없으면 아무
       말도 안 되고, 위성 수·측위 품질은 그 위치를 믿어도 되는지를 말하는
       곁가지다 — 전원 레일을 카드 셋이 아니라 띠 하나로 그린 것과 같은
       판단이다(모듈 머리말).

    🔴 좌표는 **소수 7자리를 그대로** 보여 준다(규격 §7.8.2). 화면에서
       자릿수를 줄이면 사용자가 보는 값과 저장된 값이 갈리고, 기본
       `tx.float_digits`(4자리)로 줄이면 그 차이가 11 m 다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pos = QLabel("위치 없음")
        # 🔴 좌표는 고정폭이어야 한다. 자릿수가 계속 바뀌는 수라 비례폭으로
        #    그리면 매 초 글자가 좌우로 흔들려 읽을 수가 없다.
        self._pos.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_LG}pt;"
            f" font-weight: 600; color: {Color.INK};"
        )
        self._quality = QLabel("GNSS 꺼짐")
        self._quality.setStyleSheet(
            f"color: {Color.UNKNOWN}; font-size: {Font.SIZE_SM}pt;"
        )
        self._detail = QLabel("")
        self._detail.setStyleSheet(
            f"color: {Color.INK_FAINT}; font-size: {Font.SIZE_SM}pt;"
        )
        self._last = QLabel("")
        self._last.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        col.setSpacing(1)
        col.addWidget(self._pos)
        col.addWidget(self._quality)
        col.addWidget(self._detail)
        col.addWidget(self._last)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._apply_border(Color.LINE)

    def _apply_border(self, colour: str) -> None:
        self.setStyleSheet(
            f"GnssPanel {{ background: {Color.SURFACE};"
            f" border: 1.5px solid {colour}; border-radius: 16px; }}"
        )

    def render(self, g: GnssState) -> None:
        known = g.lat is not None
        level = Level.OK if known else Level.IDLE
        verification = (Verification.VERIFIED if known
                        else Verification.UNKNOWN)
        style = chip_style(level, verification)
        self._apply_border(style.border)

        self._pos.setText(gnss_position_text(g))
        self._pos.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_LG}pt;"
            f" font-weight: 600;"
            f" color: {Color.INK if known else Color.INK_DIM};"
        )

        # 🔴 세 상태를 구분해 말한다(설계 원칙 3·4). "없음" 만 띄우면
        #    사용자는 배선을 뜯는다 — 실기기에서 실제로 그랬다(규격 §7.4의
        #    `pps_unpaired_reason` 이 생긴 계기).
        if not g.seen:
            quality = "GNSS 꺼짐 · 레코드 없음"
        else:
            sats = "—" if g.sats is None else f"{g.sats}"
            quality = f"{fix_label(g.fix)} · 위성 {sats}"
        self._quality.setText(quality)
        self._quality.setStyleSheet(
            f"color: {style.border}; font-size: {Font.SIZE_SM}pt;"
        )

        # 🔴 `fix_t` 를 사람이 읽는 날짜로 바꾸지 않는다 — DinPill 의
        #    `changed_at` 과 같은 이유. 다만 이쪽은 `fix_t` 가 **언제나
        #    UTC** 라(규격 §7.8.3) 뜻이 흔들리지 않는다. 그래도 원시 ms 로
        #    두는 것은 `t` 와 나란히 놓고 차이를 읽게 하기 위해서다 —
        #    그 차이가 "문장이 얼마나 늦게 도착했나" 이고, Q2 에서 미해결로
        #    남은 대역폭 문제를 이 레코드에서 볼 수 있는 유일한 창이다.
        bits: list[str] = []
        if g.alt is not None:
            bits.append(f"고도 {g.alt:.3f} m")
        if g.speed is not None:
            bits.append(f"{g.speed:.3f} m/s")
        if g.course is not None:
            bits.append(f"{g.course:.2f}°")
        if g.hdop is not None:
            bits.append(f"HDOP {g.hdop:.2f}")
        if g.fix_t is not None and g.t is not None:
            bits.append(f"지연 {g.t - g.fix_t} ms")
        self._detail.setText(" · ".join(bits))
        self._detail.setVisible(bool(bits))

        self._last.setText(g.last_known)
        self._last.setVisible(bool(g.last_known))


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
        self._grid = grid
        self._cols = 0            # _reflow 가 첫 resizeEvent 에서 정한다
        for ch in range(AIN_COUNT):
            card = ChannelCard(f"J{ch + CONNECTOR_OFFSET}")
            # 🔴 커서를 화면 전체로 퍼뜨린다. 한 계기 위에서 움직인 마우스가
            #    일곱 계기 모두를 같은 시각으로 돌려세운다 — 채널 사이의
            #    관계는 이 방법으로만 보인다.
            card.gauge.cursorMoved.connect(self._on_cursor)
            self._cards.append(card)
        # 🔴 마지막 줄의 남는 칸 — 수집 요약. 카드 일곱 장을 한 줄로 요약한
        #    것이라 자리도 맞다.
        self._summary = _Summary()
        self._reflow(3)

        # 🔴 전원 레일은 이제 좌측 레일(qt/rail.py)에 있다. 여기서는 그리지
        #    않는다 — 5V 가 없으면 채널이 하나도 안 도는 관계라, 채널 아래에
        #    두는 것보다 늘 보이는 자리에 있는 편이 맞다.
        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        col.setSpacing(Space.SM)
        # ── 디지털 입력 (규격 §7.6) ──────────────────────────────
        #
        # 🔴 J18~J20 은 카탈로그에 없다 — 출력이 아니라 입력이라 "켤 종류·
        #    사용 여부" 를 설정할 것이 없다(사용자 확정 2026-08-18). I2C 는
        #    포트 종류에 따라 카드 수가 늘거나 주는데, 이 셋은 **보드
        #    리비전이 고정**이라 항상 셋이다 — 그래서 I2C 처럼 처음 나타난
        #    키만 지연 생성하지 않고 여기서 미리 셋 다 만든다(채널 카드와
        #    같은 방식).
        # ── GNSS 측위 ────────────────────────────────────────────
        #
        # 🔴 J16 하나뿐이라 반복이 없다 — DinPill 처럼 미리 만들어 둔다.
        #    `gnss.enabled` 가 꺼져 있어도 자리는 남긴다(설계 원칙 3: 안
        #    꽂힌 것은 정상 상태이고, 자리가 사라지면 사용자는 이 장비에
        #    GNSS 가 있다는 것조차 화면에서 알 수 없다).
        self._gnss_title = SectionTitle("GNSS 측위")
        self._gnss = GnssPanel()

        self._din_title = SectionTitle("디지털 입력")
        din_row = QHBoxLayout()
        din_row.setSpacing(Space.MD)
        self._din_pills: dict[int, DinPill] = {}
        for cid in DIN_PORTS:
            pill = DinPill(f"J{cid}")
            self._din_pills[cid] = pill
            din_row.addWidget(pill)
        din_row.addStretch(1)

        # ── I2C 센서 ─────────────────────────────────────────────
        #
        # 🔴 여기서는 자리를 만들지 않는다. 여섯 포트를 항상 세우는 것은
        #    `host/gui/screen.py` 의 `seed_sensors()` 가 하는 일이고(아날로그
        #    `empty_channels()` 와 같은 이유 — 설계 원칙 3), Qt 위젯은
        #    `state.sensors` 에 처음 나타난 키만 골라 만든다. 카드 수는
        #    포트 종류에 따라 여섯(전부 없음)에서 늘어난다 — 온습도 하나가
        #    카드 둘을 만들기 때문이다.
        self._sensor_title = SectionTitle("I2C 센서")
        self._sensor_grid = QGridLayout()
        self._sensor_grid.setHorizontalSpacing(Space.MD)
        self._sensor_grid.setVerticalSpacing(Space.MD)
        self._sensor_cards: dict[tuple[str, str], SensorCard] = {}
        for c in range(self._cols):
            self._sensor_grid.setColumnStretch(c, 1)
        self._sensor_title.setVisible(False)

        col.addWidget(self._ch_title)
        col.addSpacing(Space.XS)
        col.addLayout(grid, 1)
        col.addSpacing(Space.SM)
        col.addWidget(self._gnss_title)
        col.addSpacing(Space.XS)
        col.addWidget(self._gnss)
        col.addSpacing(Space.SM)
        col.addWidget(self._din_title)
        col.addSpacing(Space.XS)
        col.addLayout(din_row)
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

        self._gnss.render(state.gnss)
        self._render_dins(state)
        self._render_sensors(state)
        self._summary.render(state)

    def _render_dins(self, state: ScreenState) -> None:
        for d in state.dins:
            pill = self._din_pills.get(d.key)
            if pill is not None:
                pill.render(d)

    def _reflow(self, cols: int) -> None:
        """카드를 `cols` 열로 다시 깐다 — 창 폭이 칸 수를 정한다.

        🔴 열 수를 3 으로 박아 두면 두 끝에서 다 틀린다(사용자 지적
           2026-08-20). 넓은 모니터에서는 카드 하나가 700px 넘게 부풀고
           ("카드가 너무 크다"), 좁은 창에서는 카드 최소폭 × 3 이 안
           들어가 가로 스크롤로 잘린다. 카드 크기를 고치는 것이 아니라
           **칸 수가 폭을 따라가야** 양쪽이 같이 풀린다.
        """
        if cols == self._cols:
            return
        self._cols = cols
        g = self._grid
        while g.count():
            g.takeAt(0)
        for ch, card in enumerate(self._cards):
            g.addWidget(card, ch // cols, ch % cols)
        n = len(self._cards)
        empty = cols - n % cols if n % cols else cols
        g.addWidget(self._summary, n // cols,
                    n % cols if n % cols else 0, 1, empty)
        # 🔴 예전 열의 stretch 를 0 으로 되돌린다 — 안 지우면 3열로
        #    돌아왔을 때 유령 4·5열이 폭을 나눠 가진다.
        for c in range(max(cols, 8)):
            g.setColumnStretch(c, 1 if c < cols else 0)
        rows = (n + cols - 1) // cols + (1 if n % cols == 0 else 0)
        for r in range(max(rows, 8)):
            g.setRowStretch(r, 1 if r < rows else 0)
        # I2C 격자도 같은 열 수를 따른다 — 두 구획의 칸이 어긋나면
        # 세로선이 안 맞아 산만하다. 생성자의 첫 호출 시점에는 아직
        # 안 만들어져 있다 — 그때는 아날로그만 깔고 끝낸다.
        if not hasattr(self, "_sensor_grid"):
            return
        cards = list(self._sensor_cards.values())
        sg = self._sensor_grid
        while sg.count():
            sg.takeAt(0)
        for i, card in enumerate(cards):
            sg.addWidget(card, i // cols, i % cols)
        for c in range(max(cols, 8)):
            sg.setColumnStretch(c, 1 if c < cols else 0)

    def set_available_width(self, width: int) -> None:
        """스크롤 뷰포트가 알려주는 실제 가용 폭으로 칸 수를 정한다.

        🔴 자기 `resizeEvent` 를 쓰면 안 된다 — 대시보드는 QScrollArea 안에
           있어서, 창이 자신의 최소폭(칸수 × 카드 최소폭)보다 좁아지면
           스크롤 영역이 **줄이는 대신 가로 스크롤바를 내고 멈춘다.**
           그러면 resizeEvent 가 다시 안 오고 칸 수가 넓던 시절(5칸)에
           박힌 채, 사용자는 5칸 배치의 첫 열(J3·J8)만 보게 된다 —
           실기기 스크린샷으로 확인(2026-08-20). 뷰포트 폭은 창을 줄이는
           만큼 정직하게 줄므로 이것이 기준이어야 한다.

        칸 하나에 필요한 폭 어림 330px — 게이지 최소폭 + 카드 안쪽 여백 +
        간격. 이 값을 줄이면 같은 폭에서 칸이 늘어난다(카드가 작아진다).
        """
        self._reflow(max(1, min(width // 330, 5)))

    def _render_sensors(self, state: ScreenState) -> None:
        """🔴 카드를 매번 새로 만들지 않는다. 텔레메트리는 초당 열 번 오는데
           그때마다 위젯을 새로 만들면 마우스가 카드 위에 있을 때 계속
           밑에서 사라진다 — 값을 읽을 수가 없다."""
        cols = self._cols or 3
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
