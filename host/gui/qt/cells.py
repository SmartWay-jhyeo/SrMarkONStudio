"""설정 화면의 칸들 — 계약은 `qt/cell.py` 가 정한다.

🔴 둘 다 `Row` 하나만 알고 서로를 모른다. 그래서 `settings_page` 에서
   떼어낼 수 있었다 — 984 줄짜리 한 파일에 성격이 다른 다섯 가지가 있었고,
   이미 분리돼 있는데 파일만 안 나뉜 상태였다.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from host.core.scaling import range_of, zero_scale_for
from host.gui.settings_form import Row, Widget
from host.gui.theme import Color, Font, Space

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
        self._notes = tuple(
            (r.reason if not r.editable else r.note)
            for r in (zero_row, scale_row)
        )
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

    @property
    def note_text(self) -> str:
        """🔴 영점·스케일 **둘의** 사유를 합쳐 낸다.

        한 칸이 두 항목을 맡으므로, 한쪽 사유만 내면 나머지가 사라진다.
        """
        return "  ".join(t for t in self._notes if t)

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


