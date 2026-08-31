"""반복 그룹을 접은 표.

🔴 접을지 말지는 `settings_form.matrix_of` 가 **구조로** 판정한다. 여기서
   "ain 은 표" 라고 적지 않는다 — 그러면 하드코딩으로 되돌아간다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from host.gui.qt.parts import card_title, hairline
from host.gui.settings_form import COL_SCALE, COL_ZERO, Matrix
from host.gui.theme import Color, Font, Space


class MatrixCard(QFrame):
    """반복 그룹을 접은 표. 행 = 번호, 열 = 속성.

    🔴 열 제목이 단위를 함께 말한다(`수집 주기 (ms)`). 칸마다 단위를 붙이면
       같은 글자가 일곱 번 반복되고, 표에서 그것은 잡음이다.
    """

    #: 영점·스케일 두 열이 접혀 들어갈 자리의 제목.
    RANGE_LABEL = "측정 범위 (4 → 20 mA)"

    def __init__(self, title: str, matrix: Matrix,
                 make_row, make_range=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

        plan = self._plan(matrix, make_range is not None)
        self._made: list[QWidget] = []

        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.MD)
        grid.setVerticalSpacing(Space.XS)

        for c, (kind, source) in enumerate(plan):
            if kind == "range":
                label = self.RANGE_LABEL
            else:
                unit = _first_unit(matrix, source)
                label = matrix.column_labels[source]
                label = f"{label} ({unit})" if unit else label
            head = QLabel(label)
            head.setStyleSheet(
                f"color: {Color.INK_FAINT}; font-size: {Font.SIZE_SM}pt;"
            )
            grid.addWidget(head, 0, c + 1)

        for r, mrow in enumerate(matrix.rows, start=1):
            name = QLabel(mrow.label)
            name.setStyleSheet(
                f"font-family: {Font.MONO}; font-weight: 600;"
            )
            name.setFixedWidth(52)
            grid.addWidget(name, r, 0)
            for c, (kind, source) in enumerate(plan):
                if kind == "range":
                    zero, scale = (mrow.cells[i] for i in source)
                    if zero is None or scale is None:
                        continue
                    grid.addWidget(make_range(zero, scale), r, c + 1)
                    continue
                cell = mrow.cells[source]
                if cell is None:
                    # 🔴 없는 칸은 비워 둔다. 당겨 채우면 열이 밀리고,
                    #    밀린 표는 조용히 거짓말을 한다.
                    continue
                made = make_row(cell)
                self._made.append(made)
                grid.addWidget(made, r, c + 1)
        grid.setColumnStretch(len(plan) + 1, 1)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(Space.SM)
        # 🔴 탭이 이미 그룹 이름을 말한다. 카드에 또 쓰면 같은 말이 두 번
        #    나오고, 그런 제목은 읽히지 않는 자리를 만든다.
        if title:
            col.addWidget(card_title(title))
            col.addWidget(hairline())
        col.addLayout(grid)

        # 🔴 사유를 **중복 없이** 모아 표 아래에 한 번만 쓴다. 보드가 포트
        #    여섯에 같은 경고를 붙여 보내면 표 안에서는 같은 문장이 여섯 번
        #    반복되고, 그러면 아무도 읽지 않는다.
        notes = _distinct_notes(self._made)
        if notes:
            col.addSpacing(Space.XS)
            for text in notes:
                note = QLabel(f"· {text}")
                note.setWordWrap(True)
                note.setStyleSheet(
                    f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;")
                col.addWidget(note)


    @staticmethod
    def _plan(matrix: Matrix, fold: bool) -> list[tuple[str, object]]:
        """열을 어떻게 늘어놓을지. `("cell", 열번호)` 또는 `("range", (영점, 스케일))`.

        🔴 영점·스케일 두 열을 하나로 접는다. 둘은 따로 만질 값이 아니라
           **한 사실의 두 조각**이다(규격 §7.2.1) — 하나만 바꾸면 범위가
           엉뚱해지는데 화면은 그것을 보여 주지 않는다.

        보드가 둘 중 하나만 주면 접지 않는다. 없는 것을 있는 척하지 않는다.
        """
        columns = list(matrix.columns)
        if not fold or COL_ZERO not in columns or COL_SCALE not in columns:
            return [("cell", i) for i in range(len(columns))]

        zero_i, scale_i = columns.index(COL_ZERO), columns.index(COL_SCALE)
        plan: list[tuple[str, object]] = []
        for i in range(len(columns)):
            if i == zero_i:
                plan.append(("range", (zero_i, scale_i)))
            elif i != scale_i:
                plan.append(("cell", i))
        return plan


def _distinct_notes(widgets) -> list[str]:
    """표 안에서 나온 사유들을 **순서를 지켜** 중복 없이.

    🔴 `set` 으로 걸러 정렬하지 않는다. 보드가 보낸 순서에 뜻이 있고,
       실행할 때마다 문구 순서가 바뀌면 화면이 깜빡이는 것처럼 보인다.
    """
    seen: list[str] = []
    for w in widgets:
        text = w.note_text
        if text and text not in seen:
            seen.append(text)
    return seen


def _first_unit(matrix: Matrix, column: int) -> str:
    for mrow in matrix.rows:
        cell = mrow.cells[column]
        if cell is not None and cell.unit:
            return cell.unit
    return ""


def _distinct_notes(widgets) -> list[str]:
    """표 안에서 나온 사유들을 **순서를 지켜** 중복 없이.

    🔴 `set` 으로 걸러 정렬하지 않는다. 보드가 보낸 순서에 뜻이 있고,
       실행할 때마다 문구 순서가 바뀌면 화면이 깜빡이는 것처럼 보인다.
    """
    seen: list[str] = []
    for w in widgets:
        text = w.note_text
        if text and text not in seen:
            seen.append(text)
    return seen


def _first_unit(matrix: Matrix, column: int) -> str:
    for mrow in matrix.rows:
        cell = mrow.cells[column]
        if cell is not None and cell.unit:
            return cell.unit
    return ""


