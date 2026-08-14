"""채널 카드 — 게이지 하나를 담는 그릇.

🔴 왜 그릇이 필요한가.

   처음에는 게이지를 격자에 그냥 늘어놓았다. 화면을 띄워 보니 채널 일곱
   개가 **흩어진 줄**로 보였다 — 어디까지가 J3 이고 어디부터 J4 인지 눈이
   경계를 못 찾았고, 값이 없는 채널(대부분)은 그저 빈 자리였다.

   담아 두면 두 가지가 생긴다. 채널마다 **자기 영역**이 생겨 훑을 수 있고,
   값이 없어도 **자리가 유지돼** 보드의 커넥터 배치와 대응이 유지된다.
   화면이 보드를 닮아야 한다는 것이 이 대시보드의 전제다.

🔴 왼쪽 세로 띠가 상태를 말한다.

   색은 이미 심각도에 쓰고 있으므로(스펙 §2.5) 카드 전체를 물들이지
   않는다. 띠 하나만 칠하면 일곱 개를 나란히 놓았을 때 **어느 채널이
   살아 있는지 한눈에** 들어오고, 값 영역의 흰 바탕은 그대로 남는다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from host.gui.qt.gauge import LoopGauge
from host.gui.theme import Color, Space
from host.gui.widgets.status_chip import Level, Verification, chip_style


class _Accent(QWidget):
    """카드 왼쪽의 상태 띠."""

    WIDTH = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(Color.LINE)
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_color(self, color: str) -> None:
        if self._color.name() != QColor(color).name():
            self._color = QColor(color)
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), self._color)
        p.end()


class ChannelCard(QFrame):
    """커넥터 하나. 상태 띠 + 게이지."""

    def __init__(self, connector: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        # 🔴 QFrame 이어야 스타일시트의 배경·테두리가 먹는다. QWidget 은
        #    paintEvent 를 직접 쓰지 않으면 배경을 안 칠한다.
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

        self._accent = _Accent()
        self.gauge = LoopGauge(connector)

        body = QVBoxLayout()
        body.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        body.addWidget(self.gauge)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._accent)
        lay.addLayout(body, 1)

    def set_state(self, level: Level, verification: Verification) -> None:
        """게이지가 쓰는 것과 **같은** 판정을 띠에도 쓴다.

        🔴 따로 계산하면 언젠가 갈린다 — 띠는 초록인데 값은 빨간 상태가
           생기고, 그때 어느 쪽을 믿어야 할지 알 수 없다.
        """
        style = chip_style(level, verification)
        self._accent.set_color(style.border if style.filled else Color.LINE)
