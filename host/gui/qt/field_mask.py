"""NDJSON 전송 필드를 고르는 카드.

🔴 예전에는 `698` 이라는 생짜 비트마스크 숫자였다. 손으로 비트를 계산하지
   않으면 못 바꾼다 — 바꿀 수 있는 항목인데 실질적으로 못 바꾸는 상태였다.
"""
from __future__ import annotations

import json

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from host.gui.field_budget import budget_message, compute_budget, sample_record
from host.gui.qt.parts import card_title, hairline
from host.gui.settings_form import LOCKED_FIELD_LABELS, LOCKED_FIELDS, Row
from host.gui.theme import Color, Font, Space


class FieldMaskCard(QFrame):
    """NDJSON 에 무엇을 실을지 고르는 자리 — 레코드 종류(`ain`·`i2c`·`din`)
    하나를 맡는다.

    🔴 예전에는 이 항목이 `698` 이라는 생짜 비트마스크 숫자였다. 손으로
       비트를 계산하지 않으면 못 바꾼다 — 바꿀 수 있는 항목인데 실질적으로
       못 바꾸는 상태였다.

       보드는 `$CFG,LIST` 로 필드마다 비트·이름·라벨·기본값을 이미 다 보내
       준다(규격 §7.3). 화면이 그것을 안 쓰고 있었을 뿐이다.

    🔴 **고르는 순간에 결과를 보여 준다.** 체크박스 열 개만 있으면 그것이
       대역폭에 무슨 뜻인지 보이지 않는다. Q2 에서 UART 직결에 2% 유실이
       실측됐고(HANDOFF 2026-06-17) 그 링크에서 필드를 몇 개 더 켜는 것은
       사소한 선택이 아니다. 켜기 전에 알아야 한다.

    🔴 [개정, 2026-08-19] `tx.fields` 하나였던 카드가 셋으로 나뉘었다 —
       `record_kind` 가 어느 카드인지 정하고, `fields` 에서 그 종류에
       해당하는 비트만 골라 그린다(`FieldBit.records`). 그리고 **잠긴
       필드도 보여 준다** — `quantity`·`connector_id`·`state`·
       `time_source` 처럼 마스크 비트가 아예 없어 항상 실리는 필드를
       목록에서 빼면, 사용자는 "왜 quantity 는 못 끄지" 가 아니라
       "quantity 라는 게 있긴 한가" 가 된다. 끌 수 없다는 것과 없다는
       것은 다르다 — 체크된 채 비활성화한 상자로 그 차이를 보여 준다.
    """

    changed = pyqtSignal(str, str)     # key, 마스크 문자열

    def __init__(self, row: Row, fields: dict, baud: int,
                 shape_of, record_kind: str = "ain",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._key = row.key
        self._note_text = row.reason if not row.editable else row.note
        self._baud = baud
        self._shape_of = shape_of
        self._record_kind = record_kind
        self._boxes: dict[int, QCheckBox] = {}

        # 이 카드(레코드 종류)에 해당하는 비트만 — 나머지는 마스크에
        # 서 있어도 이 카드가 안 켠다(mk_telem.c field_on() 의 kind 검사와
        # 같은 경계를 화면에서도 지킨다).
        own_bits = {bit: info for bit, info in fields.items()
                   if record_kind in getattr(info, "records", ())}

        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.XL)
        grid.setVerticalSpacing(Space.XS)
        # 비트 순서대로 — 보드가 정한 순서다. 두 열로 접어 세로를 아낀다.
        bits = sorted(own_bits)
        locked = LOCKED_FIELDS.get(record_kind, ())
        n_rows = len(bits) + len(locked)
        half = (n_rows + 1) // 2
        n = 0
        for bit in bits:
            info = own_bits[bit]
            box = QCheckBox(getattr(info, "label", "") or info.name)
            box.setToolTip(f"{info.name}  ·  bit {bit}")
            box.setEnabled(row.editable)
            box.toggled.connect(self._emit)
            self._boxes[bit] = box
            grid.addWidget(box, n % half, n // half)
            n += 1
        # 🔴 잠긴 필드 — 항상 켜진 채 비활성화한 상자로 그린다. 목록에서
        #    빼지 않는다(위 클래스 주석의 이유).
        for name in locked:
            box = QCheckBox(LOCKED_FIELD_LABELS.get(name, name))
            box.setChecked(True)
            box.setEnabled(False)
            box.setToolTip(f"{name}  ·  항상 실린다 (마스크로 끌 수 없음)")
            grid.addWidget(box, n % half, n // half)
            n += 1
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
        col.addWidget(card_title(row.label))
        col.addWidget(hairline())
        col.addLayout(grid)
        col.addSpacing(Space.XS)
        col.addWidget(card_title("나갈 줄"))
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
        record = sample_record(names, float_digits=shape.float_digits,
                               record_type=self._record_kind)
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

    @property
    def note_text(self) -> str:
        return self._note_text

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


