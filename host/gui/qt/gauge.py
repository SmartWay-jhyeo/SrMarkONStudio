"""채널 하나의 계기 — 값·이력·범위.

🔴 판단은 `widgets/loop_gauge.py` 가 한다. 여기서는 그린다.

   특히 `fraction is None` 이면 바를 그리지 않는다. 단선·과입력·비유한값이
   전부 None 이고, 그래서 **고장을 정상처럼 그릴 수가 없다** — 위젯의
   성실함에 맡기지 않는 구조다.

무엇을 그리나
------------

    J3                                   ●     ← 커넥터 · 확인 여부
    19.30 mA                     9.563 bar     ← 값이 주인공이다
    ╭──╮      ╭─╮
    ╯  ╰──────╯ ╰──────                        ← 최근 이력 (시간축)
    ┃╱╱┃████████████▏░░░░░┃                    ← 지금 값의 범위 위치
     4    8    12   16  20 mA

🔴 **왜 바와 트레이스를 둘 다 두는가.** 역할이 다르다 — 트레이스는
   *변해온 것*, 바는 *지금 4–20 사이 어디인가*. 바를 30 px 에서 12 px 로
   줄여 자리를 값과 이력에 넘겼다. 바가 두꺼우면 진행 막대로 읽히는데,
   진행 막대는 0 에서 100 으로 가는 것이고 이것은 측정값이다.

🔴 해칭 구간에 색을 쓰지 않는다. 색은 심각도에 이미 쓰고 있고(스펙 §2.5),
   여기서 또 쓰면 의미가 겹친다. 무늬로 구분한다.

🔴 커서가 올라가면 값은 **과거**를 가리킨다. 그때 지금 값처럼 보이면 안 되므로
   흐리게 쓰고 얼마나 지난 값인지 함께 적는다 — 화면이 거짓말하지 않는 것이
   이 앱의 규칙이다(설계 원칙 4 와 같은 부류).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from host.gui.theme import Color, Font
from host.gui.widgets.loop_gauge import (
    LOOP_MAX_MA,
    LOOP_MIN_MA,
    peak_hold,
    read_loop,
)
from host.gui.widgets.status_chip import Level, Verification, chip_style

#: 해칭 구간이 차지하는 폭. 4 mA 아래는 실제로는 0~4 mA 지만, 화면에서
#: 20% 를 내주면 정상 구간이 좁아진다. 존재를 알릴 만큼만 준다.
BREAK_ZONE_RATIO = 0.14

#: 트레이스가 그리는 값의 범위. 4–20 을 벗어난 표본도 자리는 보여 주되
#: 옆 줄을 침범하지 않게 살짝만 넘긴다.
_TRACE_OVERSHOOT = 0.05

# 🔴 20 % 축소 (사용자 요청 2026-08-20 — "카드가 너무 큰 것 같다").
#    줄이기 전 값: HEAD 22 · VALUE 46 · BAR 12 · TICK 15 · GAP 8,
#    최소폭 150, 이력띠 최소 40. 비율로 일괄 축소해 배치 관계는 그대로다.
_HEAD_H = 18        # 커넥터 이름 줄
_VALUE_H = 37       # 값 줄
_BAR_H = 10         # 바
_TICK_H = 12        # 눈금 숫자 (mA 한 줄 + 물리량 한 줄)
_GAP = 6


def _tick(value: float) -> str:
    """눈금 숫자. 자릿수를 늘리지 않는다 — 눈금은 읽는 것이지 재는 것이 아니다."""
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


class LoopGauge(QWidget):
    """채널 하나. 값 · 이력 · 범위."""

    #: 마우스가 트레이스 위에서 움직였다. **보드가 찍은 시각(ms)**. 벗어나면 -1.
    #:
    #: 🔴 인덱스가 아니라 시각을 보내는 이유: 채널마다 수집 주기가 따로다
    #:    (`ainN.period_ms`). 인덱스로 맞추면 느린 채널이 빠른 채널의 절반
    #:    시각을 가리키게 되고, 화면은 "같은 순간의 일곱 값" 이라고 말하면서
    #:    서로 다른 순간을 보여 준다.
    cursorMoved = pyqtSignal(int)

    def __init__(self, connector: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._connector = connector
        self._ma: float | None = None
        self._level = Level.IDLE
        self._verification = Verification.UNKNOWN
        self._unit = ""
        self._value: float | None = None
        self._note = ""
        self._trace: tuple[float | None, ...] = ()
        self._trace_t: tuple[int | None, ...] = ()
        self._span: tuple[float, float] | None = None
        self._cursor: int | None = None

        self.setMouseTracking(True)
        self.setMinimumSize(
            120, _HEAD_H + _VALUE_H + 32 + _BAR_H + _TICK_H * 2 + _GAP)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------- 입력

    def set_reading(self, ma: float | None, *, level: Level,
                    verification: Verification,
                    value: float | None = None, unit: str = "",
                    note: str = "",
                    trace: tuple[float | None, ...] = (),
                    trace_t: tuple[int | None, ...] = (),
                    span: tuple[float, float] | None = None) -> None:
        self._span = span
        self._ma = ma
        self._level = level
        self._verification = verification
        self._value = value
        self._unit = unit
        self._note = note
        self._trace = trace
        self._trace_t = trace_t
        self.update()

    def set_cursor(self, at_ms: int | None) -> None:
        """공유 커서를 **시각**으로 건다. `None` 이면 지금 값을 보여 준다."""
        if self._cursor != at_ms:
            self._cursor = at_ms
            self.update()

    def cursor_index(self) -> int | None:
        """커서가 가리키는 시각에 **가장 가까운** 표본의 자리.

        🔴 정확히 일치하는 시각을 찾지 않는다. 채널마다 수집 시점이
           미세하게 다르고(ADS1256 이 채널을 돌아가며 읽는다), 같은 순간에
           읽힌 표본은 애초에 없다. 가장 가까운 것이 곧 "그때 그 채널의 값"이다.
        """
        if self._cursor is None:
            return None
        best, best_gap = None, None
        for i, t in enumerate(self._trace_t):
            if t is None:
                continue
            gap = abs(t - self._cursor)
            if best_gap is None or gap < best_gap:
                best, best_gap = i, gap
        return best

    # ------------------------------------------------------------- 마우스

    def _trace_rect(self) -> QRectF:
        top = _HEAD_H + _VALUE_H
        bottom = self.height() - (_BAR_H + _TICK_H * 2 + _GAP)
        return QRectF(0.0, float(top), float(self.width()),
                      max(24.0, bottom - top))

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        rect = self._trace_rect()
        n = len(self._trace)
        if n < 2 or not rect.contains(event.position()):
            self.cursorMoved.emit(-1)
            return
        frac = (event.position().x() - rect.left()) / rect.width()
        index = round(min(1.0, max(0.0, frac)) * (n - 1))
        stamp = self._trace_t[index] if index < len(self._trace_t) else None
        # 시각을 모르는 표본 위에서는 커서를 걸지 않는다 — 다른 계기가
        # 무엇에 맞춰야 할지 알 수 없다.
        self.cursorMoved.emit(-1 if stamp is None else stamp)

    def leaveEvent(self, event) -> None:           # noqa: N802
        self.cursorMoved.emit(-1)

    # ------------------------------------------------------------- 읽기

    def _at_cursor(self) -> float | None:
        """커서가 가리키는 값. 커서가 없거나 그 자리가 구멍이면 None."""
        i = self.cursor_index()
        return self._trace[i] if i is not None and i < len(self._trace) else None

    # ------------------------------------------------------------- 그리기

    def paintEvent(self, _event) -> None:          # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        style = chip_style(self._level, self._verification)
        accent = QColor(style.border)

        holding = self._cursor is not None
        shown = self._at_cursor() if holding else self._ma

        self._paint_head(p, w, style, accent)
        self._paint_value(p, w, shown, holding)
        self._paint_trace(p, accent)
        self._paint_bar(p, w, accent)

        p.end()

    def _paint_head(self, p: QPainter, w: int, style, accent: QColor) -> None:
        f = QFont(Font.UI.split(",")[0])
        f.setPointSize(Font.SIZE_MD)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(Color.INK))
        p.drawText(0, 0, w, _HEAD_H, Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, self._connector)

        # 확인 여부를 점으로. 채움 = 실측, 테두리 = 명령만.
        dot = QRectF(w - 13, 6, 9, 9)
        p.setPen(QPen(accent, 1.6))
        p.setBrush(QColor(style.fill) if style.filled
                   else Qt.BrushStyle.NoBrush)
        p.drawEllipse(dot)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_value(self, p: QPainter, w: int, shown: float | None,
                     holding: bool) -> None:
        """값 — 이 화면의 주인공이다.

        🔴 22 pt 에서 34 pt 로 올렸다. 이 앱이 하는 일이 전류 측정인데 그
           값이 화면에서 가장 작은 요소 중 하나였다.
        """
        top = _HEAD_H
        reading = read_loop(shown) if shown is not None else None
        # 커서가 과거를 가리키는 동안에는 흐리게 — 지금 값으로 읽히면 안 된다.
        ink = QColor(Color.INK_DIM if holding else Color.INK)

        mono = QFont(Font.MONO.split(",")[0])
        mono.setPointSize(34)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        if reading is None:
            p.setFont(mono)
            p.setPen(QColor(Color.UNKNOWN))
            p.drawText(0, top, w, _VALUE_H, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, "—")
        elif reading.fraction is None:
            # 🔴 숫자를 보여주지 않는다. 보여주면 측정값으로 읽힌다.
            f2 = QFont(Font.UI.split(",")[0])
            f2.setPointSize(Font.SIZE_LG)
            f2.setWeight(QFont.Weight.DemiBold)
            p.setFont(f2)
            p.setPen(QColor(Color.FAULT if reading.broken else Color.WARN))
            p.drawText(0, top, w, _VALUE_H, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, reading.label)
        else:
            text = f"{reading.ma:.2f}"
            p.setFont(mono)
            # 🔴 폭은 **그 글꼴로** 잰다. 글꼴을 바꾼 뒤 재면 "mA" 가 값 위에
            #    겹치거나 멀찍이 떨어진다.
            text_w = QFontMetrics(mono).horizontalAdvance(text)
            p.setPen(ink)
            p.drawText(0, top, w, _VALUE_H, Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, text)
            small = QFont(Font.UI.split(",")[0])
            small.setPointSize(Font.SIZE_SM)
            p.setFont(small)
            p.setPen(QColor(Color.INK_FAINT if holding else Color.INK_DIM))
            p.drawText(text_w + 7, top, 40, _VALUE_H,
                       Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, "mA")

        # 오른쪽 곁들임 — 평소에는 물리량, 커서를 올리면 얼마나 지난 값인지.
        aside, colour = self._aside(reading, holding)
        if aside:
            small = QFont(Font.MONO.split(",")[0])
            small.setPointSize(Font.SIZE_SM)
            p.setFont(small)
            p.setPen(QColor(colour))
            p.drawText(0, top, w, _VALUE_H, Qt.AlignmentFlag.AlignRight
                       | Qt.AlignmentFlag.AlignVCenter, aside)

    def _elapsed_at_cursor(self) -> float | None:
        """커서가 가리키는 표본이 **얼마나 지난 것인가**, 초.

        🔴 주기를 가정해 인덱스에 곱하지 않는다. 보드가 찍은 시각끼리 뺀다 —
           수집 주기는 설정으로 바뀌고, 가정하면 그때부터 화면이 조용히
           틀린 시각을 말한다 (설계 원칙 2).
        """
        i = self.cursor_index()
        if i is None or i >= len(self._trace_t):
            return None
        then = self._trace_t[i]
        now = next((t for t in reversed(self._trace_t) if t is not None), None)
        if then is None or now is None:
            return None
        return (now - then) / 1000.0

    def _aside(self, reading, holding: bool) -> tuple[str, str]:
        if holding:
            # 🔴 과거를 보고 있다는 사실이 값 옆에 늘 붙어 있어야 한다.
            back = self._elapsed_at_cursor()
            if back is None:
                # 보드가 시각을 안 줬다. 지어내지 말고 그 사실을 쓴다.
                return ("과거", Color.WARN)
            return (f"−{back:.1f}초", Color.WARN)
        if self._note:
            return (self._note, Color.WARN)
        if self._value is not None and reading and reading.fraction is not None:
            text = f"{self._value:.3f}"
            return ((f"{text} {self._unit}" if self._unit else text),
                    Color.INK_DIM)
        return ("", Color.INK_DIM)

    def _paint_trace(self, p: QPainter, accent: QColor) -> None:
        """최근 이력. 이 화면에 시간축을 주는 유일한 것.

        🔴 구멍(`None`)에서 선을 끊는다. 이어 그리면 오지 않은 값을 온 것처럼
           그리게 된다 — 트레이스가 있다는 사실만으로 계기처럼 보이지만,
           그 선이 거짓이면 없느니만 못하다.
        """
        rect = self._trace_rect()
        p.fillRect(rect, QColor(Color.WELL))

        # 4 mA · 20 mA 기준선. 눈이 값을 가늠할 데가 있어야 한다.
        p.setPen(QPen(QColor(Color.LINE), 1))
        for f in (0.0, 1.0):
            y = round(self._trace_y(rect, f)) + 0.5
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        n = len(self._trace)
        if n < 2:
            f = QFont(Font.UI.split(",")[0])
            f.setPointSize(Font.SIZE_SM)
            p.setFont(f)
            p.setPen(QColor(Color.INK_FAINT))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "이력 없음")
            return

        step = rect.width() / (n - 1)
        pen = QPen(accent, 1.6)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)

        run: list[QPointF] = []
        for i, v in enumerate(self._trace):
            if v is None:
                if len(run) > 1:
                    p.drawPolyline(*run)
                run = []
                continue
            frac = (v - LOOP_MIN_MA) / (LOOP_MAX_MA - LOOP_MIN_MA)
            frac = min(1.0 + _TRACE_OVERSHOOT, max(-_TRACE_OVERSHOOT, frac))
            run.append(QPointF(rect.left() + i * step,
                               self._trace_y(rect, frac)))
        if len(run) > 1:
            p.drawPolyline(*run)

        self._paint_cursor(p, rect, step, n, accent)

    @staticmethod
    def _trace_y(rect: QRectF, frac: float) -> float:
        return rect.bottom() - frac * rect.height()

    def _paint_cursor(self, p: QPainter, rect: QRectF, step: float,
                      n: int, accent: QColor) -> None:
        i = self.cursor_index()
        if i is None or not (0 <= i < n):
            return
        x = rect.left() + i * step

        pen = QPen(QColor(Color.INK_DIM), 1)
        pen.setDashPattern([2, 3])
        p.setPen(pen)
        p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        v = self._trace[i]
        if v is None:
            return
        frac = (v - LOOP_MIN_MA) / (LOOP_MAX_MA - LOOP_MIN_MA)
        frac = min(1.0 + _TRACE_OVERSHOOT, max(-_TRACE_OVERSHOOT, frac))
        p.setPen(QPen(accent, 1.8))
        p.setBrush(QColor(Color.SURFACE))
        p.drawEllipse(QPointF(x, self._trace_y(rect, frac)), 3.2, 3.2)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_bar(self, p: QPainter, w: int, accent: QColor) -> None:
        reading = read_loop(self._ma) if self._ma is not None else None
        top = self.height() - (_BAR_H + _TICK_H * 2)
        track = QRectF(0.5, top + 0.5, w - 1.0, _BAR_H)
        brk_w = track.width() * BREAK_ZONE_RATIO
        brk = QRectF(track.left(), track.top(), brk_w, track.height())
        live = QRectF(track.left() + brk_w, track.top(),
                      track.width() - brk_w, track.height())

        p.setBrush(QColor(Color.WELL))
        p.setPen(QPen(QColor(Color.LINE), 1))
        p.drawRect(track)

        # 🔴 4 mA 미만 구간은 사선 해칭. 색을 바꾸지 않는다.
        p.save()
        p.setClipRect(brk)
        p.setPen(QPen(QColor(Color.UNKNOWN), 1))
        x = brk.left() - brk.height()
        while x < brk.right() + brk.height():
            p.drawLine(QPointF(x, brk.bottom()),
                       QPointF(x + brk.height(), brk.top()))
            x += 4
        p.restore()

        # 🔴 단선이면 해칭 구간에 표식을 남긴다. 바가 비어 있는 것만으로는
        #    "값이 0" 과 "선이 끊어졌다" 가 같아 보인다.
        if reading is not None and reading.broken:
            p.setPen(QPen(QColor(Color.FAULT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(brk.adjusted(1, 1, -1, -1))

        if reading is not None and reading.bar_fraction > 0.0:
            fill = live.width() * min(1.0, reading.bar_fraction)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawRect(QRectF(live.left(), live.top() + 1, fill,
                              live.height() - 2))

        # 🔴 피크 홀드. 100 ms 마다 값이 바뀌면 순간 최대값은 눈에 안 보인다.
        peak = peak_hold(self._trace)
        if peak is not None and LOOP_MIN_MA <= peak <= LOOP_MAX_MA:
            px = live.left() + live.width() * (
                (peak - LOOP_MIN_MA) / (LOOP_MAX_MA - LOOP_MIN_MA))
            p.setPen(QPen(QColor(Color.INK), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(px, track.top() - 2),
                       QPointF(px, track.bottom() + 2))

        # 살아 있는 0 점 — 해칭과 실측 구간의 경계
        p.setPen(QPen(QColor(Color.INK_DIM), 1.4))
        p.drawLine(QPointF(live.left(), track.top() - 2),
                   QPointF(live.left(), track.bottom() + 2))

        self._paint_ticks(p, w, track, live)

    def _paint_ticks(self, p: QPainter, w: int, track: QRectF,
                     live: QRectF) -> None:
        small = QFont(Font.UI.split(",")[0])
        small.setPointSize(Font.SIZE_SM)
        p.setFont(small)
        p.setPen(QColor(Color.INK_FAINT))
        y = int(track.bottom() + 2)
        p.drawText(int(live.left()) - 12, y, 24, _TICK_H,
                   Qt.AlignmentFlag.AlignHCenter, "4")
        for ma in (8, 12, 16):
            x = live.left() + live.width() * (
                (ma - LOOP_MIN_MA) / (LOOP_MAX_MA - LOOP_MIN_MA))
            p.drawText(int(x) - 12, y, 24, _TICK_H,
                       Qt.AlignmentFlag.AlignHCenter, str(ma))
        # 🔴 오른쪽 끝은 잘리기 쉽다. 폭을 넉넉히 주고 끝에 붙인다 —
        #    처음에 34 px 만 줬더니 "20 mA" 가 "!0 mA" 로 잘렸다.
        p.drawText(w - 56, y, 56, _TICK_H, Qt.AlignmentFlag.AlignRight,
                   "20 mA")
        self._paint_span_ticks(p, w, live, y + _TICK_H - 2)

    def _paint_span_ticks(self, p: QPainter, w: int, live: QRectF,
                          y: int) -> None:
        """물리량 눈금 한 줄 더.

        🔴 mA 축을 물리량 축으로 **갈아 끼우지 않는다.** 4 mA 미만 해칭이
           단선을 뜻하는 것은 전류 축에서만 성립하고, 그 판정이 이 게이지의
           존재 이유다. 물리량은 그 아래에 덧붙인다.

        🔴 범위를 모르면 아무것도 안 그린다. 지어낸 눈금은 사용자가 그것을
           보드 설정으로 읽는다.
        """
        if self._span is None:
            return
        low, high = self._span
        f = QFont(Font.UI.split(",")[0])
        f.setPointSize(Font.SIZE_SM)
        p.setFont(f)
        p.setPen(QColor(Color.INK_FAINT))

        text = _tick(high)
        if self._unit:
            text += f" {self._unit}"
        # 🔴 오른쪽 끝 라벨의 실제 폭을 잰다. 단위가 길면(`L/min`) 고정폭으로
        #    잡아 둔 자리를 넘어 바로 앞 눈금과 겹쳤다 — `45` 와 `60 L/min`
        #    이 `4560 L/min` 으로 찍혔다.
        right = QFontMetrics(f).horizontalAdvance(text) + 10

        for ma in (LOOP_MIN_MA, 8, 12, 16):
            frac = (ma - LOOP_MIN_MA) / (LOOP_MAX_MA - LOOP_MIN_MA)
            x = live.left() + live.width() * frac
            if x + 22 > w - right:
                continue          # 끝 라벨과 부딪힌다 — 하나 건너뛴다
            p.drawText(int(x) - 22, y, 44, _TICK_H,
                       Qt.AlignmentFlag.AlignHCenter,
                       _tick(low + (high - low) * frac))
        p.drawText(w - right, y, right, _TICK_H,
                   Qt.AlignmentFlag.AlignRight, text)
