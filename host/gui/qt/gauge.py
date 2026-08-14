"""4–20 mA 루프 게이지 위젯 — 이 화면의 시그니처.

🔴 판단은 `widgets/loop_gauge.py` 가 한다. 여기서는 그린다.

   특히 `fraction is None` 이면 바를 그리지 않는다. 단선·과입력·비유한값이
   전부 None 이고, 그래서 **고장을 정상처럼 그릴 수가 없다** — 위젯의
   성실함에 맡기지 않는 구조다.

무엇을 그리나
------------

    4                    20 mA
    ├──┬───────────────────┤
    │╱╱│███████████░░░░░░░░│
    └──┴───────────────────┘
     ↑
     살아 있는 0 점 아래 = 해칭

0–20 mA 였다면 "센서가 0 을 읽었다" 와 "선이 끊어졌다" 를 구분할 수 없다.
4 mA 를 살아 있는 0 점으로 띄워 두었기 때문에 그 아래는 측정값이 아니라
고장이고, 게이지가 그 사실을 눈에 보이게 해야 한다. 일반적인 대시보드
게이지는 이 구분을 표현하지 못한다.

🔴 해칭 구간에 색을 쓰지 않는다. 색은 심각도에 이미 쓰고 있고(스펙 §2.5),
   여기서 또 쓰면 의미가 겹친다. 무늬로 구분한다.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from host.gui.theme import Color, Font
from host.gui.widgets.loop_gauge import LOOP_MAX_MA, LOOP_MIN_MA, read_loop
from host.gui.widgets.status_chip import Level, Verification, chip_style

#: 해칭 구간이 차지하는 폭. 4 mA 아래는 실제로는 0~4 mA 지만, 화면에서
#: 20% 를 내주면 정상 구간이 좁아진다. 존재를 알릴 만큼만 준다.
BREAK_ZONE_RATIO = 0.14


class LoopGauge(QWidget):
    """세로로 선 게이지 하나. 커넥터 이름 · 값 · 바."""

    def __init__(self, connector: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._connector = connector
        self._ma: float | None = None
        self._level = Level.IDLE
        self._verification = Verification.UNKNOWN
        self._unit = ""
        self._value: float | None = None
        self._note = ""
        # 🔴 카드 안에 들어가므로 높이를 확보한다. 게이지 30 + 눈금 15 +
        #    값 영역이 들어갈 자리다.
        # 게이지 30 + 눈금 15 + 값 34 + 물리량 18 + 여백
        self.setMinimumSize(150, 146)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------- 입력

    def set_reading(self, ma: float | None, *, level: Level,
                    verification: Verification,
                    value: float | None = None, unit: str = "",
                    note: str = "") -> None:
        self._ma = ma
        self._level = level
        self._verification = verification
        self._value = value
        self._unit = unit
        self._note = note
        self.update()

    # ------------------------------------------------------------- 그리기

    def paintEvent(self, _event) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        style = chip_style(self._level, self._verification)
        accent = QColor(style.border)

        # ── 커넥터 이름 ───────────────────────────────────────────
        f = QFont(Font.UI.split(",")[0])
        f.setPointSize(Font.SIZE_MD)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(Color.INK))
        p.drawText(0, 0, w, 20, Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, self._connector)

        # 확인 여부를 점으로. 채움 = 실측, 테두리 = 명령만.
        dot = QRectF(w - 13, 5, 9, 9)
        p.setPen(QPen(accent, 1.6))
        p.setBrush(QColor(style.fill) if style.filled else Qt.BrushStyle.NoBrush)
        p.drawEllipse(dot)

        # ── 값 ────────────────────────────────────────────────────
        reading = read_loop(self._ma) if self._ma is not None else None

        mono = QFont(Font.MONO.split(",")[0])
        mono.setPointSize(Font.SIZE_XL)
        # 🔴 표 형식 숫자. 값이 갱신될 때 자릿수가 흔들리면 눈이 따라가지
        #    못한다. 실시간 계기에서는 미관이 아니라 기능이다.
        mono.setStyleHint(QFont.StyleHint.Monospace)
        p.setFont(mono)

        if reading is None:
            p.setPen(QColor(Color.UNKNOWN))
            p.drawText(0, 22, w, 34, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, "—")
        elif reading.fraction is None:
            # 🔴 숫자를 보여주지 않는다. 보여주면 측정값으로 읽힌다.
            p.setPen(QColor(Color.FAULT if reading.broken else Color.WARN))
            f2 = QFont(Font.UI.split(",")[0])
            f2.setPointSize(Font.SIZE_LG)
            f2.setWeight(QFont.Weight.DemiBold)
            p.setFont(f2)
            p.drawText(0, 22, w, 34, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, reading.label)
        else:
            p.setPen(QColor(Color.INK))
            p.drawText(0, 22, w, 34, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, f"{reading.ma:.2f}")
            small = QFont(Font.UI.split(",")[0])
            small.setPointSize(Font.SIZE_SM)
            p.setFont(small)
            p.setPen(QColor(Color.INK_DIM))
            fm = QFontMetrics(mono)
            p.drawText(fm.horizontalAdvance(f"{reading.ma:.2f}") + 6, 22, 40, 34,
                       Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, "mA")

        # ── 게이지 ────────────────────────────────────────────────
        # 🔴 높이를 22 에서 키웠다. 얇으면 **진행 막대**로 읽힌다 — 진행
        #    막대는 0 에서 100 으로 가는 것이고, 이것은 4 에서 20 사이의
        #    측정값이다. 눈금을 넣을 자리도 필요하다.
        top = 72
        h = 30
        track = QRectF(0.5, top + 0.5, w - 1.0, h)
        brk_w = track.width() * BREAK_ZONE_RATIO
        brk = QRectF(track.left(), track.top(), brk_w, track.height())
        live = QRectF(track.left() + brk_w, track.top(),
                      track.width() - brk_w, track.height())

        p.setBrush(QColor(Color.WELL))
        p.setPen(QPen(QColor(Color.LINE), 1))
        p.drawRect(track)

        # 🔴 4 mA 미만 구간은 사선 해칭. 색을 바꾸지 않는다 — 색은 심각도에
        #    이미 쓰고 있고 여기서 또 쓰면 의미가 겹친다.
        p.save()
        p.setClipRect(brk)
        p.setPen(QPen(QColor(Color.UNKNOWN), 1))
        step = 4
        x = brk.left() - brk.height()
        while x < brk.right() + brk.height():
            p.drawLine(int(x), int(brk.bottom()),
                       int(x + brk.height()), int(brk.top()))
            x += step
        p.restore()

        # 🔴 단선이면 해칭 구간에 표식을 남긴다. 바가 비어 있는 것만으로는
        #    "값이 0" 과 "선이 끊어졌다" 가 같아 보인다 — 4–20 mA 를 쓰는
        #    이유가 그 둘을 구분하는 것인데 화면이 다시 뭉개면 안 된다.
        if reading is not None and reading.broken:
            p.setPen(QPen(QColor(Color.FAULT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(brk.adjusted(1, 1, -1, -1))

        # 채움
        if reading is not None and reading.bar_fraction > 0.0:
            fill_w = live.width() * min(1.0, reading.bar_fraction)
            bar = QRectF(live.left(), live.top() + 1, fill_w, live.height() - 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawRect(bar)

        # 🔴 중간 눈금. 4 와 20 만 있으면 눈이 값을 가늠할 기준이 없다 —
        #    바가 어디쯤인지는 보이는데 그것이 몇 mA 인지 읽히지 않는다.
        #    계기는 값을 **읽게** 해야 하고, 그러려면 사이에 짚을 데가
        #    있어야 한다. 안쪽으로만 그어 바를 가리지 않는다.
        p.setPen(QPen(QColor(Color.INK_FAINT), 1))
        for ma in (8, 12, 16):
            x = live.left() + live.width() * (ma - LOOP_MIN_MA) / (
                LOOP_MAX_MA - LOOP_MIN_MA)
            p.drawLine(int(x), int(track.top() + 1),
                       int(x), int(track.top() + 5))
            p.drawLine(int(x), int(track.bottom() - 5),
                       int(x), int(track.bottom() - 1))

        # 살아 있는 0 점 표시 — 해칭과 실측 구간의 경계
        p.setPen(QPen(QColor(Color.INK_DIM), 1.4))
        p.drawLine(int(live.left()), int(track.top() - 3),
                   int(live.left()), int(track.bottom() + 3))

        # 눈금 숫자
        small = QFont(Font.UI.split(",")[0])
        small.setPointSize(Font.SIZE_SM)
        p.setFont(small)
        p.setPen(QColor(Color.INK_FAINT))
        p.drawText(int(live.left()) - 12, int(track.bottom() + 3), 24, 15,
                   Qt.AlignmentFlag.AlignHCenter, "4")
        for ma in (8, 12, 16):
            x = live.left() + live.width() * (ma - LOOP_MIN_MA) / (
                LOOP_MAX_MA - LOOP_MIN_MA)
            p.drawText(int(x) - 12, int(track.bottom() + 3), 24, 15,
                       Qt.AlignmentFlag.AlignHCenter, str(ma))
        # 🔴 오른쪽 끝은 잘리기 쉽다. 폭을 넉넉히 주고 끝에 붙인다 —
        #    처음에 34px 만 줬더니 "20 mA" 가 "!0 mA" 로 잘렸다.
        p.drawText(w - 56, int(track.bottom() + 3), 56, 15,
                   Qt.AlignmentFlag.AlignRight, "20 mA")

        # ── 물리량 / 곁들임 ───────────────────────────────────────
        p.setPen(QColor(Color.INK_DIM))
        if self._note:
            p.setPen(QColor(Color.WARN))
            p.drawText(0, int(track.bottom() + 20), w, 18,
                       Qt.AlignmentFlag.AlignLeft, self._note)
        elif self._value is not None and reading and reading.fraction is not None:
            text = f"{self._value:.3f}"
            if self._unit:
                text += f" {self._unit}"
            p.drawText(0, int(track.bottom() + 20), w, 18,
                       Qt.AlignmentFlag.AlignLeft, text)

        p.end()
