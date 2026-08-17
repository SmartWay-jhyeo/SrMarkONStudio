"""설정 화면. 폼 구성은 `settings_form.py` 가 정한다.

🔴 항목을 하드코딩하지 않는다. `$CFG,LIST` 응답만으로 그린다 — 어떤 항목이
   있는지, 무슨 위젯인지, 범위가 얼마인지 전부 보드가 알려 준다.

🔴 **반복되는 그룹은 표로 접는다.**

   45 개 항목 중 35 개가 7 채널 × 5 속성이다. 세로로 35 줄을 쌓으면 스크롤이
   길어지는 것보다 나쁜 일이 생긴다 — "J5 의 영점이 J6 보다 큰가" 를 볼 수
   없다. 채널끼리 비교하는 것이 이 화면의 주된 용도인데도.

   접을지 말지는 `settings_form.matrix_of` 가 **구조로** 판정한다. 여기서
   "ain 은 표" 라고 적지 않는다 — 그러면 하드코딩으로 되돌아간다.
"""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.limits import DEFAULT_BAUD
from host.core.scaling import range_of, zero_scale_for
from host.gui.field_budget import (
    budget_message,
    compute_budget,
    sample_record,
)
from host.gui.qt.parts import hairline
from host.gui.settings_form import (
    COL_SCALE,
    COL_ZERO,
    KEY_FIELD_MASK,
    Matrix,
    Row,
    SettingsForm,
    TelemetryShape,
    Widget,
    group_label,
    matrix_of,
    telemetry_shape,
)
from host.gui.theme import Color, Font, Space

#: 폼 한 줄이 차지할 최대 폭. 🔴 예전에는 사유 칸이 stretch 라 창을 넓힐수록
#: 오른쪽이 비었다 — 1130 px 캔버스에서 700 px 가 빈 채였다. 눈이 라벨에서
#: 입력까지 건너가는 거리도 그만큼 멀어진다.
FORM_WIDTH = 620

#: 표 안의 입력 폭. 열이 다섯이라 각자 좁아야 한 화면에 들어온다.
CELL_WIDTH = {
    Widget.TOGGLE: 40,
    Widget.NUMBER: 88,
    Widget.CHOICE: 104,
    Widget.TEXT: 88,
}


class RowWidget(QWidget):
    """설정 한 줄. 라벨 · 입력 · 단위, 그리고 그 아래 사유.

    🔴 사유를 **입력 아래**로 내렸다. 예전에는 오른쪽 열에 있었는데 대개
       비어 있어서 화면의 절반을 빈 칸이 먹었고, 정작 문장이 들어오면
       좁은 열에서 지저분하게 접혔다.

    `compact` 면 라벨과 단위를 뺀다 — 표 안에서는 그것을 열 제목이 말한다.
    """

    changed = pyqtSignal(str, str)   # key, text

    def __init__(self, row: Row, parent: QWidget | None = None, *,
                 compact: bool = False) -> None:
        super().__init__(parent)
        self._key = row.key
        self._compact = compact
        self._editor: QWidget

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(Space.MD)

        if not compact:
            label = QLabel(row.label)
            label.setFixedWidth(190)
            if not row.editable:
                label.setStyleSheet(f"color: {Color.INK_DIM};")
            line.addWidget(label)

        self._editor = self._make_editor(row)
        self._editor.setEnabled(row.editable)
        self._editor.setFixedWidth(
            CELL_WIDTH.get(row.widget, 88) if compact else 150)
        line.addWidget(self._editor)

        if not compact:
            unit = QLabel(row.unit)
            unit.setObjectName("dim")
            unit.setFixedWidth(44)
            line.addWidget(unit)
        line.addStretch(1)

        # 사유·오류가 들어가는 자리. 항상 같은 위치라 눈이 찾기 쉽다.
        self._note = QLabel()
        self._note.setObjectName("dim")
        self._note.setWordWrap(True)
        self._note.setVisible(False)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 3, 0, 3)
        col.setSpacing(2)
        col.addLayout(line)
        col.addWidget(self._note)

        # 🔴 보드가 준 사유를 그대로 띄운다. 편집 가능 여부와 무관하다.
        #
        #    못 바꾸는 항목이면 "왜 못 바꾸는지" 가 되고("읽기 전용" 이라고만
        #    쓰면 그 이유가 사라진다), 바꿀 수 있는 항목이면 "바꾸면 무슨 일이
        #    생기는지" 가 된다.
        #
        #    후자가 실제로 필요해졌다 — pwr.5v 를 끌 수 있게 하면서
        #    (사용자 확정 2026-08-14) "끄면 쿨링 팬·아날로그 수집·WS2812 가
        #    함께 멈춘다" 는 경고가 유일한 안전장치가 됐는데, 예전 코드는
        #    편집 가능하다는 이유로 그 문구를 버리고 있었다.
        text = row.reason if not row.editable else row.note
        if text:
            # 🔴 표 안에서는 사유를 줄로 깔지 않는다. 여섯 포트가 같은 문장을
            #    이고 있으면 정작 값이 안 읽힌다 — 표 아래에 한 번만 모아
            #    보여 주고(`MatrixCard`), 여기서는 손이 닿는 곳에만 둔다.
            if not compact:
                self._set_note(text)
            self.setToolTip(text)
            self._editor.setToolTip(text)

    @property
    def note_text(self) -> str:
        """이 줄이 이고 있는 사유. 표가 모아 쓰기 위해 꺼내 간다."""
        return self.toolTip()

    def _set_note(self, text: str, colour: str = "") -> None:
        self._note.setText(text)
        self._note.setVisible(bool(text))
        self._note.setStyleSheet(f"color: {colour};" if colour else "")

    def _make_editor(self, row: Row) -> QWidget:
        if row.widget is Widget.TOGGLE:
            box = QCheckBox()
            box.setChecked(row.value == "true")
            box.toggled.connect(
                lambda on: self.changed.emit(self._key, "true" if on else "false")
            )
            return box

        if row.widget is Widget.CHOICE:
            combo = QComboBox()
            for choice in row.choices:
                combo.addItem(str(choice))
            idx = combo.findText(row.value)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentTextChanged.connect(
                lambda text: self.changed.emit(self._key, text)
            )
            return combo

        # NUMBER 와 TEXT 둘 다 QLineEdit 이다.
        #
        # 🔴 QSpinBox 를 쓰지 않는다. 스핀박스는 범위 밖 값을 스스로 잘라
        #    넣는데, 그러면 사용자가 친 값과 보내는 값이 달라지고 아무도
        #    그것을 모른다. 규격이 잘라 담지 말라고 정한 것과 같은 이유다.
        edit = QLineEdit(row.value)
        edit.textChanged.connect(lambda text: self.changed.emit(self._key, text))
        return edit

    def set_value(self, text: str) -> None:
        """바깥에서 값을 바꿨다. 위젯만 맞춘다 — 폼은 부른 쪽이 고친다.

        🔴 신호를 막고 넣는다. 안 막으면 `changed` 가 돌아 나가 폼을 또
           고치고, 그 사이 사용자가 친 값이 있으면 덮어쓴다.
        """
        self._editor.blockSignals(True)
        try:
            if isinstance(self._editor, QCheckBox):
                self._editor.setChecked(text == "true")
            elif isinstance(self._editor, QComboBox):
                idx = self._editor.findText(text)
                if idx >= 0:
                    self._editor.setCurrentIndex(idx)
            elif isinstance(self._editor, QLineEdit):
                self._editor.setText(text)
        finally:
            self._editor.blockSignals(False)

    def show_error(self, message: str) -> None:
        self._set_note(message, Color.FAULT)
        self._editor.setStyleSheet(f"border: 2px solid {Color.FAULT};")

    def clear_error(self, fallback: str = "") -> None:
        self._set_note(fallback)
        self._editor.setStyleSheet("")

    def mark_dirty(self, dirty: bool) -> None:
        self._editor.setStyleSheet(
            f"border: 2px solid {Color.PROBING};" if dirty else ""
        )


class RangeFields(QWidget):
    """센서의 측정 범위 — `0 ~ 150`. 두 칸이 영점·스케일 **둘 다**를 쓴다.

    🔴 왜 영점·스케일을 직접 안 보여 주는가.

       0~150 bar 센서를 다는 사람이 알고 있는 것은 `0` 과 `150` 이지
       `9.375` 가 아니다. 예전에는 그 나눗셈(150 ÷ 16)을 손으로 해야 했고,
       그래서 바꿀 수 있게 되어 있는 항목이 실질적으로 못 바꾸는 상태였다.
       환산은 규격 §7.2.1 이 정한 식이라 화면이 대신할 수 있다.

    🔴 한 칸을 고쳐도 **두 항목이 함께** 나간다. 영점만 바뀌고 스케일이
       그대로면 범위가 엉뚱해지는데, 화면은 사용자가 적은 범위를 보여 주고
       있으므로 그 어긋남이 눈에 안 띈다.
    """

    changed = pyqtSignal(str, str)

    def __init__(self, zero_row: Row, scale_row: Row,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zero_key = zero_row.key
        self._scale_key = scale_row.key
        editable = zero_row.editable and scale_row.editable

        self._low = QLineEdit()
        self._high = QLineEdit()
        for box in (self._low, self._high):
            box.setFixedWidth(72)
            box.setEnabled(editable)
            box.editingFinished.connect(self._emit)

        tilde = QLabel("~")
        tilde.setStyleSheet(f"color: {Color.INK_FAINT};")

        self._note = QLabel("")
        self._note.setStyleSheet(
            f"color: {Color.FAULT}; font-size: {Font.SIZE_SM}pt;")
        self._note.setVisible(False)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(Space.XS)
        line.addWidget(self._low)
        line.addWidget(tilde)
        line.addWidget(self._high)
        line.addStretch(1)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 3, 0, 3)
        col.setSpacing(2)
        col.addLayout(line)
        col.addWidget(self._note)

        self.show_settings(zero_row.value, scale_row.value)

    # --------------------------------------------------------- 값

    def show_settings(self, zero_text: str, scale_text: str) -> None:
        """보드가 들고 있는 영점·스케일을 범위로 되짚어 보여 준다."""
        try:
            span = range_of(float(zero_text), float(scale_text))
        except (TypeError, ValueError):
            span = None
        if span is None:
            # 🔴 되짚을 수 없으면 비워 둔다. 0 같은 그럴듯한 값을 채우면
            #    사용자가 그것을 보드의 설정으로 읽는다.
            self._low.setText("")
            self._high.setText("")
            self._note.setText("스케일이 0이라 범위를 알 수 없다")
            self._note.setVisible(True)
            return
        self._low.setText(_trim(span[0]))
        self._high.setText(_trim(span[1]))
        self._note.setVisible(False)

    def _emit(self) -> None:
        try:
            low = float(self._low.text())
            high = float(self._high.text())
        except (TypeError, ValueError):
            self.show_error("숫자를 적어야 한다")
            return
        pair = zero_scale_for(low, high)
        if pair is None:
            self.show_error("양 끝이 같으면 환산할 수 없다")
            return
        self.clear_error()
        zero, scale = pair
        self.changed.emit(self._zero_key, _trim(zero))
        self.changed.emit(self._scale_key, _trim(scale))

    # --------------------------------------------------------- 폼과의 계약

    def set_value(self, text: str) -> None:
        """레일처럼 바깥에서 값을 밀어넣는 경로는 이 위젯에 없다."""

    def show_error(self, message: str) -> None:
        self._note.setText(message)
        self._note.setVisible(True)

    def clear_error(self, fallback: str = "") -> None:
        self._note.setVisible(False)

    def mark_dirty(self, dirty: bool) -> None:
        style = f"border: 2px solid {Color.PROBING};" if dirty else ""
        self._low.setStyleSheet(style)
        self._high.setStyleSheet(style)


def _trim(value: float) -> str:
    """`9.375` 는 그대로, `4.0` 은 `4` 로. 전선에 나갈 형태이기도 하다."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


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
            col.addWidget(_card_title(title))
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
        text = getattr(w, "note_text", "")
        if text and text not in seen:
            seen.append(text)
    return seen


def _first_unit(matrix: Matrix, column: int) -> str:
    for mrow in matrix.rows:
        cell = mrow.cells[column]
        if cell is not None and cell.unit:
            return cell.unit
    return ""


class FieldMaskCard(QFrame):
    """NDJSON 에 무엇을 실을지 고르는 자리.

    🔴 예전에는 이 항목이 `698` 이라는 생짜 비트마스크 숫자였다. 손으로
       비트를 계산하지 않으면 못 바꾼다 — 바꿀 수 있는 항목인데 실질적으로
       못 바꾸는 상태였다.

       보드는 `$CFG,LIST` 로 필드마다 비트·이름·라벨·기본값을 이미 다 보내
       준다(규격 §7.3). 화면이 그것을 안 쓰고 있었을 뿐이다.

    🔴 **고르는 순간에 결과를 보여 준다.** 체크박스 열 개만 있으면 그것이
       대역폭에 무슨 뜻인지 보이지 않는다. Q2 에서 UART 직결에 2% 유실이
       실측됐고(HANDOFF 2026-06-17) 그 링크에서 필드를 몇 개 더 켜는 것은
       사소한 선택이 아니다. 켜기 전에 알아야 한다.
    """

    changed = pyqtSignal(str, str)     # key, 마스크 문자열

    def __init__(self, row: Row, fields: dict, baud: int,
                 shape_of, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._key = row.key
        self._baud = baud
        self._shape_of = shape_of
        self._boxes: dict[int, QCheckBox] = {}

        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.XL)
        grid.setVerticalSpacing(Space.XS)
        # 비트 순서대로 — 보드가 정한 순서다. 두 열로 접어 세로를 아낀다.
        bits = sorted(fields)
        half = (len(bits) + 1) // 2
        for n, bit in enumerate(bits):
            info = fields[bit]
            box = QCheckBox(getattr(info, "label", "") or info.name)
            box.setToolTip(f"{info.name}  ·  bit {bit}")
            box.setEnabled(row.editable)
            box.toggled.connect(self._emit)
            self._boxes[bit] = box
            grid.addWidget(box, n % half, n // half)
        grid.setColumnStretch(2, 1)

        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" color: {Color.INK_DIM};"
        )
        self._budget = QLabel("")
        self._budget.setStyleSheet(f"font-size: {Font.SIZE_SM}pt;")

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(Space.SM)
        col.addWidget(_card_title(row.label))
        col.addWidget(hairline())
        col.addLayout(grid)
        col.addSpacing(Space.XS)
        col.addWidget(_card_title("나갈 줄"))
        col.addWidget(self._preview)
        col.addWidget(self._budget)

        self.set_value(row.value)

    # --------------------------------------------------------- 값

    def _mask(self) -> int:
        mask = 0
        for bit, box in self._boxes.items():
            if box.isChecked():
                mask |= 1 << bit
        return mask

    def _emit(self) -> None:
        self.changed.emit(self._key, str(self._mask()))
        self.refresh()

    def set_value(self, text: str) -> None:
        try:
            mask = int(float(text))
        except (TypeError, ValueError):
            mask = 0
        for bit, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(mask & (1 << bit)))
            box.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        """미리보기와 대역폭을 다시 잰다. 어림하지 않고 실제 줄을 만든다."""
        shape = self._shape_of()
        names = [self._name_of(bit) for bit, box in sorted(self._boxes.items())
                 if box.isChecked()]
        record = sample_record(names, float_digits=shape.float_digits)
        budget = compute_budget(record, channels_enabled=shape.channels,
                                period_ms=shape.period_ms, baud=self._baud)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        # 🔴 JSON 한 줄에는 공백이 없어서 `setWordWrap` 이 접을 자리를 못 찾고
        #    카드 밖으로 삐져나간다(가로 스크롤이 생겼다). 쉼표 뒤에 폭 없는
        #    공백을 넣어 **필드 경계**에서 접히게 한다 — 눈으로도 그 자리가
        #    끊어질 자리다. 재는 것은 이 문자열이 아니라 원본이라 길이에
        #    영향이 없다.
        self._preview.setText(line.replace(",", ",​"))

        level, message = budget_message(budget)
        colour = {"fault": Color.FAULT, "warn": Color.WARN}.get(
            level, Color.INK_DIM)
        self._budget.setText(
            f"한 줄 {budget.line_bytes} B  ·  {message}")
        self._budget.setStyleSheet(
            f"font-size: {Font.SIZE_SM}pt; color: {colour};")

    def _name_of(self, bit: int) -> str:
        box = self._boxes[bit]
        tip = box.toolTip()
        return tip.split("  ·  ")[0] if tip else str(bit)

    # --------------------------------------------------------- 폼과의 계약
    #
    # 🔴 `RowWidget` 과 같은 입구를 갖는다. 설정 화면은 항목이 무슨 위젯으로
    #    그려지는지 몰라야 하고, 거부·오류·dirty 처리가 두 벌이 되면 안 된다.

    def show_error(self, message: str) -> None:
        self._budget.setText(message)
        self._budget.setStyleSheet(
            f"font-size: {Font.SIZE_SM}pt; color: {Color.FAULT};")

    def clear_error(self, fallback: str = "") -> None:
        self.refresh()

    def mark_dirty(self, dirty: bool) -> None:
        self.setStyleSheet(
            f"QFrame#card {{ border: 2px solid {Color.PROBING}; }}"
            if dirty else "")


def _card_title(text: str) -> QLabel:
    title = QLabel(text)
    title.setStyleSheet(
        f"color: {Color.INK_DIM}; font-size: {Font.SIZE_SM}pt;"
        f" font-weight: 700; letter-spacing: 1px;"
    )
    return title


class SettingsPage(QWidget):
    """카탈로그로 그린 설정 화면."""

    #: 보드에 보낼 (키, 값) 목록
    apply_requested = pyqtSignal(list)
    #: $CFG,SAVE — Flash 에 남긴다
    save_requested = pyqtSignal()
    #: $CFG,RESET — 전부 기본값으로
    reset_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, *,
                 baud: int = DEFAULT_BAUD) -> None:
        super().__init__(parent)
        self._form: SettingsForm | None = None
        self._baud = baud
        self._rows: dict[str, object] = {}
        #: 보드에 적용됐지만 아직 Flash 에 저장되지 않은 변경이 있는가.
        #: 🔴 보드의 dirty 플래그와 별개로 호스트가 자기가 보낸 것을 센다 —
        #:    보드는 그것을 알려 주지 않고, 물어볼 명령도 없다.
        self._unsaved = False
        #: 시험에서 확인 대화상자를 건너뛰기 위한 고리.
        self._confirm = self._ask_confirm

        # 🔴 그룹마다 자기 화면을 준다. 45 개 항목을 한 두루마리에 이어
        #    붙이면 무엇이 어디 있는지 알 수 없고, 스크롤 위치가 곧 문맥이
        #    되어 버린다. 탭으로 나누면 한 번에 한 가지만 본다.
        self._tabs = QHBoxLayout()
        self._tabs.setSpacing(Space.XS)
        self._tabs.setContentsMargins(0, 0, 0, 0)
        self._tab_buttons: list[QPushButton] = []

        self._pages = QStackedWidget()

        self._status = QLabel("보드에 연결되면 설정을 불러온다.")
        self._status.setObjectName("dim")

        self._apply = QPushButton("보드에 적용")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._on_apply)

        self._revert = QPushButton("되돌리기")
        self._revert.setEnabled(False)
        self._revert.clicked.connect(self._on_revert)

        # 🔴 적용과 저장은 다른 사실이다.
        #
        #    적용은 보드의 RAM 에 들어간 것이고, 저장은 Flash 에 남은 것이다.
        #    적용만 하고 전원을 끄면 사라진다. 화면이 둘을 같은 것처럼 보이면
        #    사용자는 설정이 남은 줄 알고 보드를 떼어 간다.
        #
        #    그래서 저장만 채운 버튼이다 — 이 화면의 주 동작이다.
        self._save = QPushButton("보드에 저장")
        self._save.setObjectName("primary")
        self._save.setEnabled(False)
        self._save.clicked.connect(lambda: self.save_requested.emit())

        # 🔴 되돌릴 수 없는 동작이라 나머지와 떼어 놓는다. 손이 미끄러져
        #    옆 버튼을 누르는 일이 생기면 안 되는 쪽이다.
        self._reset = QPushButton("기본값으로")
        self._reset.setObjectName("danger")
        self._reset.setEnabled(False)
        self._reset.clicked.connect(self._on_reset)

        bar = QHBoxLayout()
        bar.setSpacing(Space.SM)
        bar.addWidget(self._reset)
        bar.addSpacing(Space.XL)
        bar.addWidget(self._status, 1)
        bar.addWidget(self._revert)
        bar.addWidget(self._apply)
        bar.addWidget(self._save)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.LG)
        col.setSpacing(Space.SM)
        col.addLayout(self._tabs)
        col.addWidget(hairline())
        col.addWidget(self._pages, 1)
        col.addLayout(bar)

    # ------------------------------------------------------------- 구성

    @property
    def form(self):
        """지금 그리고 있는 폼. `None` 이면 아직 카탈로그를 못 받았다.

        🔴 공개한다. 예전에는 바깥에서 `getattr(page, "_form", None)` 으로
           private 를 뒤졌다 — 그런 접근은 리팩터링을 조용히 깨뜨린다.
        """
        return getattr(self, "_form", None)

    def set_form(self, form: SettingsForm) -> None:
        """카탈로그가 도착했다. 화면을 다시 그린다."""
        keep = self._pages.currentIndex()
        self._form = form
        self._rows.clear()
        _clear(self._tabs)
        self._tab_buttons.clear()
        while self._pages.count():
            widget = self._pages.widget(0)
            self._pages.removeWidget(widget)
            widget.deleteLater()

        for group in form.groups:
            self._pages.addWidget(self._page_for(group))
            self._tabs.addWidget(self._tab_button(group_label(group.name)))

        self._tabs.addStretch(1)
        # 되돌리기·초기화로 다시 그릴 때 보던 탭에 머문다 — 화면이 제멋대로
        # 첫 탭으로 튀면 방금 무엇을 고쳤는지 잃어버린다.
        self.select_tab(keep if 0 <= keep < self._pages.count() else 0)
        self._refresh_buttons()

    def _page_for(self, group) -> QWidget:
        """그룹 하나가 차지하는 화면. 카드가 하나일 수도 여럿일 수도 있다."""
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, Space.SM, 0)
        body.setSpacing(Space.MD)

        matrix = matrix_of(group)
        if matrix is not None:
            # 🔴 접히지 않은 항목이 먼저다. LED 의 `체인 LED 수` 처럼 표
            #    **전체**에 걸리는 값이라, 표 아래에 두면 개별 줄의 설정으로
            #    읽힌다.
            for card in self._form_cards(matrix.leftovers):
                body.addWidget(card)
            body.addWidget(MatrixCard(
                "", matrix,
                lambda r: self._make_cell(r, compact=True),
                self._make_range))
        else:
            for card in self._form_cards(group.rows):
                body.addWidget(card)
        body.addStretch(1)

        inner = QWidget()
        inner.setLayout(body)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _form_cards(self, rows) -> list[QFrame]:
        """보통 항목들은 카드 한 장. 🔴 필드 마스크만 따로 뗀다 — 체크박스
        열 개와 미리보기가 붙어서 한 줄에 담기지 않는다."""
        cards: list[QFrame] = []
        plain = [r for r in rows if r.key != KEY_FIELD_MASK]
        mask = next((r for r in rows if r.key == KEY_FIELD_MASK), None)

        if plain:
            card = QFrame()
            card.setObjectName("card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
            lay.setSpacing(Space.SM)
            # 제목 없음 — 탭이 그룹 이름을 말한다. 마스크 카드만 자기
            # 제목을 갖는다(같은 탭 안의 두 번째 카드라 구분이 필요하다).
            for row in plain:
                w = self._make_cell(row)
                # 🔴 한 줄의 폭을 묶는다. 창이 넓어져도 라벨에서 입력까지의
                #    거리가 늘어나지 않아야 눈이 따라간다.
                w.setMaximumWidth(FORM_WIDTH)
                lay.addWidget(w)
            cards.append(card)

        if mask is not None and self._form is not None:
            card = FieldMaskCard(mask, self._form.fields, self._baud,
                                 self._shape)
            card.changed.connect(self._on_changed)
            self._rows[mask.key] = card
            cards.append(card)
        return cards

    def _make_range(self, zero_row: Row, scale_row: Row) -> RangeFields:
        """🔴 한 위젯이 두 키를 맡는다. 거부·오류 표시가 어느 쪽으로 와도
        같은 자리에 뜬다 — 사용자는 `zero` 를 고친 적이 없기 때문이다."""
        w = RangeFields(zero_row, scale_row)
        w.changed.connect(self._on_changed)
        self._rows[zero_row.key] = w
        self._rows[scale_row.key] = w
        return w

    def _shape(self) -> TelemetryShape:
        if self._form is None:
            return TelemetryShape(channels=1, period_ms=100, float_digits=4)
        return telemetry_shape(self._form)

    def _make_cell(self, row: Row, *, compact: bool = False) -> RowWidget:
        """🔴 표 안이든 폼이든 **같은 위젯**을 쓴다.

        따로 만들면 검증·오류 표시·dirty 표시가 두 벌이 되고, 언젠가
        한쪽만 고쳐진다. `self._rows` 도 키 하나에 위젯 하나로 유지된다.
        """
        w = RowWidget(row, compact=compact)
        w.changed.connect(self._on_changed)
        self._rows[row.key] = w
        return w

    def _tab_button(self, title: str) -> QPushButton:
        index = len(self._tab_buttons)
        button = QPushButton(title)
        button.setObjectName("tab")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, i=index: self.select_tab(i))
        self._tab_buttons.append(button)
        return button

    def select_tab(self, index: int) -> None:
        if not (0 <= index < self._pages.count()):
            return
        self._pages.setCurrentIndex(index)
        for i, button in enumerate(self._tab_buttons):
            button.setChecked(i == index)

    # ------------------------------------------------------------- 편집

    def set_value(self, key: str, text: str) -> None:
        """대시보드 쪽에서 값을 바꿨다 (전원 레일).

        🔴 폼과 위젯을 **함께** 맞춘다. `screen.py` 의 `_rail_values()` 가
           설정 폼을 유일한 출처로 읽으므로, 폼만 고치고 위젯을 두면 두
           화면이 서로 다른 말을 한다.
        """
        if self._form is None or key not in self._rows:
            return
        self._form.edit(key, text)
        self._rows[key].set_value(text)
        self._rows[key].mark_dirty(self._form.is_dirty(key))
        self._refresh_buttons()

    def _on_changed(self, key: str, text: str) -> None:
        if self._form is None:
            return
        self._form.edit(key, text)

        w = self._rows[key]
        message = self._form.validate(key)
        if message:
            w.show_error(message)
        else:
            w.clear_error()
            w.mark_dirty(self._form.is_dirty(key))
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        if self._form is None:
            return
        pending = self._form.pending_changes()
        errors = self._form.errors()
        self._apply.setEnabled(bool(pending))
        self._revert.setEnabled(self._form.has_changes)
        self._save.setEnabled(self._unsaved)
        self._reset.setEnabled(True)

        # 🔴 대역폭은 필드만으로 정해지지 않는다 — 채널 수와 주기가 함께
        #    곱해진다. 그래서 **아무 항목이 바뀌어도** 다시 잰다. 필드를
        #    켤 때만 갱신하면, 채널을 하나 더 켜 놓고 필드 화면으로 왔을 때
        #    화면이 옛날 숫자를 말한다.
        mask_card = self._rows.get(KEY_FIELD_MASK)
        if isinstance(mask_card, FieldMaskCard):
            mask_card.refresh()

        if errors:
            self._status.setText(f"고칠 것 {len(errors)}개 — 보낼 수 없다")
            self._status.setStyleSheet(f"color: {Color.FAULT};")
        elif pending:
            self._status.setText(f"보내지 않은 변경 {len(pending)}개")
            self._status.setStyleSheet("")
        elif self._unsaved:
            # 🔴 이 문구가 이 화면에서 가장 중요하다. 적용은 RAM 이고
            #    저장은 Flash 다. 여기서 그만두고 보드를 떼어 가면 설정이
            #    사라지는데, 화면이 말해 주지 않으면 알 길이 없다.
            self._status.setText("보드에 적용됐지만 저장되지 않았다 — "
                                 "전원을 끄면 사라진다")
            self._status.setStyleSheet(f"color: {Color.WARN};")
        else:
            self._status.setText("바뀐 것이 없다")
            self._status.setStyleSheet("")

    def _on_apply(self) -> None:
        if self._form is None:
            return
        self.apply_requested.emit(self._form.pending_changes())

    def _on_revert(self) -> None:
        if self._form is None:
            return
        self._form.revert_all()
        unsaved = self._unsaved
        self.set_form(self._form)
        # 🔴 되돌리기는 **화면의 편집**을 되돌린다. 이미 보드에 보낸 것은
        #    되돌리지 않으므로 저장 여부도 그대로다. 여기서 지우면 사용자가
        #    저장하지 않은 채 화면을 떠난다.
        self._unsaved = unsaved
        self._refresh_buttons()

    def _ask_confirm(self, text: str) -> bool:
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("확인")
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_reset(self) -> None:
        """🔴 되돌릴 수 없는 동작이라 먼저 묻는다.

        전 항목이 기본값으로 간다. 채널 영점·스케일처럼 손으로 맞춘 값이
        섞여 있고, 그것을 다시 만들려면 센서를 다시 재야 한다.
        """
        if not self._confirm("설정을 전부 기본값으로 되돌린다.\n"
                             "채널 영점·스케일처럼 손으로 맞춘 값도 사라진다.\n\n"
                             "계속할까?"):
            return
        self.reset_requested.emit()

    # ------------------------------------------------------------- 저장 상태

    def mark_unsaved(self) -> None:
        """보드에 무언가를 적용했다 — 아직 Flash 에는 없다."""
        self._unsaved = True
        self._refresh_buttons()

    def mark_saved(self) -> None:
        """$CFG,SAVE 가 성공했다."""
        self._unsaved = False
        self._refresh_buttons()

    @property
    def has_unsaved(self) -> bool:
        return self._unsaved

    # ------------------------------------------------------------- 응답

    def on_accepted(self, key: str) -> None:
        if self._form is None:
            return
        self._form.accept(key)
        if key in self._rows:
            self._rows[key].mark_dirty(False)
            self._rows[key].clear_error()
        # 보드의 RAM 에 들어갔을 뿐이다. Flash 는 아직이다.
        self._unsaved = True
        self._refresh_buttons()

    def on_reset_done(self) -> None:
        """보드가 $CFG,RESET 을 받아들였다.

        보드의 값이 전부 바뀌었으므로 화면도 다시 읽어야 한다. 여기서는
        저장이 필요하다는 것만 세우고, 카탈로그 재요청은 호출 쪽이 한다.
        """
        self._unsaved = True
        self._refresh_buttons()

    def on_rejected(self, key: str, reason: str) -> None:
        """🔴 값을 되돌리지 않는다. 사용자가 방금 친 것을 지우면 무엇을
        고치려 했는지 사라진다. 사유를 보여 주고 고칠 기회를 준다."""
        if key in self._rows:
            self._rows[key].show_error(f"보드가 거부: {reason}")
        self._refresh_buttons()


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
