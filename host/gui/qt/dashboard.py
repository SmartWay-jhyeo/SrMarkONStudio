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
from host.gui.qt.gauge import LoopGauge
from host.gui.theme import Color, Font, Space
from host.gui.widgets.status_chip import (
    Level,
    Verification,
    chip_style,
    rail_label,
)

#: AIN0 이 J3 이다 (데이터시트 §5.3).
CONNECTOR_OFFSET = 3
AIN_COUNT = 7


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
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet(f"color: {Color.LINE};")
        rule.setFixedHeight(1)

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


class Dashboard(QWidget):
    RAILS = (("pwr.24v", "24V"), ("pwr.14v9", "14.9V"), ("pwr.5v", "5V"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history = StateHistory()
        self._rails: dict[str, RailPill] = {}
        self._gauges: list[LoopGauge] = []

        # ── 채널 (시그니처) ──────────────────────────────────────
        self._ch_title = SectionTitle("아날로그 입력")
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.MD)
        grid.setVerticalSpacing(Space.MD)
        for ch in range(AIN_COUNT):
            g = LoopGauge(f"J{ch + CONNECTOR_OFFSET}")
            self._gauges.append(g)
            grid.addWidget(g, ch // 4, ch % 4)
        # 마지막 줄의 빈 칸을 채워 게이지가 늘어나지 않게 한다.
        for c in range(AIN_COUNT % 4, 4):
            if AIN_COUNT % 4:
                grid.addWidget(QWidget(), 1, c)

        # ── 전원 ─────────────────────────────────────────────────
        self._pwr_title = SectionTitle("전원")
        pwr = QHBoxLayout()
        pwr.setSpacing(Space.SM)
        for key, label in self.RAILS:
            pill = RailPill(label)
            self._rails[key] = pill
            pwr.addWidget(pill)
        pwr.addStretch(1)

        self._hint = QLabel(
            "전원 레일에는 피드백 회로가 없다 — 화면은 명령한 상태를 말한다"
        )
        self._hint.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        col.setSpacing(Space.SM)
        col.addWidget(self._ch_title)
        col.addSpacing(Space.XS)
        col.addLayout(grid)
        col.addStretch(1)
        col.addWidget(self._pwr_title)
        col.addSpacing(Space.XS)
        col.addLayout(pwr)
        col.addWidget(self._hint)

    # ------------------------------------------------------------- 갱신

    def update_rails(self, values: dict[str, bool], *, reachable: bool,
                     now_s: float | None = None) -> None:
        now = time.monotonic() if now_s is None else now_s
        for key, label in self.RAILS:
            on = bool(values.get(key, False))
            if reachable:
                verification = Verification.COMMANDED
                level = Level.OK if on else Level.IDLE
            else:
                verification = Verification.UNKNOWN
                level = Level.IDLE
            state = build_chip_state(
                self._history, key, label, level, verification, now,
                detail=rail_label(on, verification),
            )
            self._rails[key].apply(state)

    def update_channel(self, ch: int, ma: float | None, *,
                       level: Level = Level.OK,
                       verification: Verification = Verification.VERIFIED,
                       value: float | None = None, unit: str = "",
                       note: str = "") -> None:
        if 0 <= ch < len(self._gauges):
            self._gauges[ch].set_reading(
                ma, level=level, verification=verification,
                value=value, unit=unit, note=note,
            )

    def set_link(self, text: str, bad: bool = False) -> None:
        self._ch_title.set_aside(text, Color.FAULT if bad else None)
