"""I2C 센서 카드 — 값 하나와 그 흐름 (규격 §7.5).

🔴 아날로그 카드를 재사용하지 않는다.

   `LoopGauge` 는 4~20 mA 루프를 그린다 — 양 끝 눈금, 단선(4 mA 미만)
   표시, 루프 막대가 전부 그 전제 위에 있다. I2C 센서는 루프가 아니다.
   조도 400 lx 에 "4 mA" 눈금을 그리면 화면이 거짓말을 한다.

   그래서 더 단순한 카드다: 값 · 단위 · 흐름. 범위를 모르는 물리량에
   눈금을 지어내지 않는다 — 흐름선은 **자기 이력 안에서** 상대적으로
   그린다.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from host.gui.screen import SensorState
from host.gui.theme import Color, Font, Space


class _Spark(QWidget):
    """이력 흐름선.

    🔴 눈금이 없다. 물리량의 범위를 모르기 때문이다 — 조도는 0~10만 lx 이고
       온도는 -40~85 ℃ 다. 없는 범위를 가정해 눈금을 그리면 화면이 아는
       척을 하게 된다. 대신 **자기 이력의 최소~최대**로 채워 그린다:
       "지금 값이 최근 흐름의 어디쯤인가" 만 말한다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._trace: tuple[float | None, ...] = ()
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_trace(self, trace: tuple[float | None, ...]) -> None:
        self._trace = trace
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        points = [v for v in self._trace if v is not None]
        if len(points) < 2:
            p.end()
            return
        lo, hi = min(points), max(points)
        span = hi - lo
        # 평평한 신호는 가운데 선으로. 0 으로 나누지 않는다.
        if span <= 0:
            span = 1.0
            lo = lo - 0.5

        pen = QPen(QColor(Color.PROBING))
        pen.setWidthF(1.6)
        p.setPen(pen)

        # 🔴 `None` 에서 선을 끊는다. 이어 그리면 값이 안 온 구간을
        #    부드럽게 지나간 것처럼 보인다.
        n = len(self._trace)
        step = w / max(n - 1, 1)
        run: list[QPointF] = []
        for i, v in enumerate(self._trace):
            if v is None:
                if len(run) > 1:
                    p.drawPolyline(QPolygonF(run))
                run = []
                continue
            y = h - 3 - (v - lo) / span * (h - 6)
            run.append(QPointF(i * step, y))
        if len(run) > 1:
            p.drawPolyline(QPolygonF(run))
        p.end()


class SensorCard(QFrame):
    """포트 하나가 내는 값 하나."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;")

        self._value = QLabel("—")
        self._value.setStyleSheet(
            f"color: {Color.INK}; font-family: {Font.MONO}; "
            f"font-size: {Font.SIZE_XL}pt;")
        self._unit = QLabel("")
        self._unit.setStyleSheet(
            f"color: {Color.INK_FAINT}; font-size: {Font.SIZE_SM}pt;")

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(Space.XS)
        line.addWidget(self._value)
        line.addWidget(self._unit, 0, Qt.AlignmentFlag.AlignBottom)
        line.addStretch(1)

        self._spark = _Spark()

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        col.setSpacing(2)
        col.addWidget(self._title)
        col.addLayout(line)
        col.addWidget(self._spark, 1)

    def render(self, sensor: SensorState) -> None:
        self._title.setText(f"{sensor.connector} · {sensor.label}")
        if sensor.value is None:
            # 🔴 값이 없으면 비운다. 마지막 값을 계속 띄우면 죽은 센서가
            #    살아 있는 것처럼 보인다 (규격 §7.5).
            #    `status=3`(지원 안 함)은 고장이 아니므로 "—" 대신 이유를
            #    말하고, 색도 FAULT 로 물들이지 않는다.
            self._value.setText("지원 안 함" if sensor.unsupported else "—")
            self._value.setStyleSheet(
                f"color: {Color.FAULT if sensor.broken else Color.INK_FAINT}; "
                f"font-family: {Font.MONO}; font-size: {Font.SIZE_XL}pt;")
        else:
            self._value.setText(f"{sensor.value:,.2f}")
            self._value.setStyleSheet(
                f"color: {Color.INK}; font-family: {Font.MONO}; "
                f"font-size: {Font.SIZE_XL}pt;")
        self._unit.setText(sensor.unit)
        self._spark.set_trace(sensor.trace)
