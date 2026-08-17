"""영점 맞추기 패널 — 아날로그 입력 탭 아래에 붙는다.

🔴 규칙과 판정은 여기 없다. `host/gui/tare.py` 가 줄을 만들고 이 파일은
   그것을 그리기만 한다 (CLAUDE.md `host/gui/` 의 원칙). 무엇을 잡을 수
   있는지·차이가 얼마인지는 화면 없이 시험된다.

🔴 왜 표 안이 아니라 따로인가.

   범위 칸은 **설정**이고 영점 잡기는 **작업**이다. 설정은 값을 적어 넣고
   보내는 것이고, 작업은 센서를 0 상태로 두고 한 번 누르는 것이다. 섞어
   두면 지금 흐르는 전류(계속 변하는 값)가 입력 칸들 사이에 끼어 어느 것이
   내가 적은 값이고 어느 것이 보드가 말하는 값인지 흐려진다.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from host.gui.tare import NOMINAL_ZERO_MA, TareRow
from host.gui.theme import Color, Font, Space

#: 전류는 소수 셋째 자리까지. ADS1256 의 실측 편차가 ±0.09% 라 그 아래는
#: 보여 줘도 잡음이다.
_MA = "{:.3f}"


def _mono(text: str, color: str, *, size: int = Font.SIZE_MD) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color: {color}; font-family: {Font.MONO}; font-size: {size}pt;")
    lab.setAlignment(Qt.AlignmentFlag.AlignRight
                     | Qt.AlignmentFlag.AlignVCenter)
    return lab


class TarePanel(QFrame):
    """채널마다 한 줄. 지금 전류 · 설정된 영점 · 차이 · [잡기]."""

    #: (설정 키, 새 값) — 설정 화면이 폼에 반영하고 보드로 보낸다.
    tare_requested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._rows: tuple[TareRow, ...] = ()

        head = QLabel("영점 맞추기")
        head.setStyleSheet(
            f"color: {Color.INK}; font-size: {Font.SIZE_LG}pt;")
        hint = QLabel("센서를 0 상태로 두고 잡는다. "
                      "스팬은 건드리지 않는다 — 0 점만 옮긴다.")
        hint.setStyleSheet(
            f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;")

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, Space.SM, 0, 0)
        self._grid.setHorizontalSpacing(Space.MD)
        self._grid.setVerticalSpacing(Space.XS)
        # 🔴 마지막에 빈 열을 두어 남는 폭을 전부 먹인다. 없으면 여섯 열이
        #    폭을 나눠 가져 `지금` 과 `영점` 이 화면 양끝으로 벌어진다 —
        #    나란히 비교하라고 만든 표인데 비교가 안 된다.
        self._grid.setColumnStretch(6, 1)

        self._all = QPushButton("전체 잡기")
        self._all.clicked.connect(self._tare_all)
        self._revert = QPushButton("4 mA 로 되돌리기")
        self._revert.clicked.connect(self._revert_all)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, Space.SM, 0, 0)
        buttons.setSpacing(Space.SM)
        buttons.addWidget(self._all)
        buttons.addWidget(self._revert)
        buttons.addStretch(1)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(2)
        col.addWidget(head)
        col.addWidget(hint)
        col.addLayout(self._grid)
        col.addLayout(buttons)

        self._header()

    # ------------------------------------------------------------- 그리기

    def _header(self) -> None:
        for c, text in enumerate(("", "지금", "영점", "차이", "", "")):
            if not text:
                continue
            lab = QLabel(text)
            lab.setStyleSheet(f"color: {Color.INK_FAINT}; "
                              f"font-size: {Font.SIZE_SM}pt;")
            lab.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._grid.addWidget(lab, 0, c)

    def show_rows(self, rows: tuple[TareRow, ...]) -> None:
        """줄을 다시 그린다.

        🔴 위젯을 매번 새로 만들지 않는다. 이 패널은 텔레메트리가 올 때마다
           갱신되는데(초당 열 번), 그때마다 버튼을 새로 만들면 누르는 순간
           밑에서 사라진다 — 클릭이 먹지 않는 것처럼 보인다.
        """
        if len(rows) != len(self._rows):
            self._rebuild(rows)
        self._rows = rows
        for i, row in enumerate(rows):
            self._paint(i, row)
        self._all.setEnabled(any(r.can_tare for r in rows))

    def _rebuild(self, rows: tuple[TareRow, ...]) -> None:
        while self._grid.count() > 6:
            item = self._grid.takeAt(self._grid.count() - 1)
            if item.widget():
                item.widget().deleteLater()
        self._cells: list[dict] = []
        for i, row in enumerate(rows):
            r = i + 1
            name = QLabel(row.label)
            name.setStyleSheet(f"color: {Color.INK};")
            live = _mono("—", Color.INK_DIM)
            zero = _mono("—", Color.INK)
            gap = _mono("—", Color.INK_DIM, size=Font.SIZE_SM)
            btn = QPushButton("잡기")
            btn.setFixedWidth(64)
            btn.clicked.connect(lambda _c=False, k=i: self._tare_one(k))
            why = QLabel("")
            why.setStyleSheet(f"color: {Color.INK_FAINT}; "
                              f"font-size: {Font.SIZE_SM}pt;")
            for c, w in enumerate((name, live, zero, gap, btn, why)):
                self._grid.addWidget(w, r, c)
            self._cells.append(
                {"live": live, "zero": zero, "gap": gap,
                 "btn": btn, "why": why})

    def _paint(self, i: int, row: TareRow) -> None:
        cell = self._cells[i]
        cell["live"].setText("—" if row.live_ma is None
                             else _MA.format(row.live_ma) + " mA")
        cell["zero"].setText(_MA.format(row.zero_ma) + " mA")

        off = row.offset
        if off is None:
            cell["gap"].setText("—")
            colour = Color.INK_FAINT
        else:
            cell["gap"].setText(f"{off:+.3f}")
            # 🔴 어긋남을 색으로만 말하지 않는다. 숫자에 부호가 붙어 있어
            #    색을 못 보는 사람도 방향을 안다.
            colour = Color.WARN if row.notable else Color.INK_DIM
        cell["gap"].setStyleSheet(
            f"color: {colour}; font-family: {Font.MONO}; "
            f"font-size: {Font.SIZE_SM}pt;")

        cell["btn"].setEnabled(row.can_tare)
        cell["why"].setText(row.blocked_reason)

    # ------------------------------------------------------------- 동작

    def _tare_one(self, i: int) -> None:
        if i < len(self._rows):
            self._emit(self._rows[i])

    def _tare_all(self) -> None:
        for row in self._rows:
            self._emit(row)

    def _emit(self, row: TareRow) -> None:
        """🔴 잡을 수 없는 줄은 조용히 건너뛴다. `전체 잡기` 는 값이 없는
           채널까지 포함해 도는데, 거기서 없는 값을 밀어 넣으면 그 채널의
           영점이 통째로 틀어진다."""
        if not row.can_tare or row.live_ma is None:
            return
        from host.gui.tare import tared_zero

        self.tare_requested.emit(row.zero_key,
                                 _MA.format(tared_zero(row.live_ma)))

    def _revert_all(self) -> None:
        for row in self._rows:
            if row.editable:
                self.tare_requested.emit(row.zero_key,
                                         _MA.format(NOMINAL_ZERO_MA))
