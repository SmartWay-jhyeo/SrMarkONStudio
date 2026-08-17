"""설정 화면 — 그룹 탭과 화면 전체의 배선.

🔴 항목을 하드코딩하지 않는다. `$CFG,LIST` 응답만으로 그린다 — 어떤 항목이
   있는지, 무슨 위젯인지, 범위가 얼마인지 전부 보드가 알려 준다.

🔴 칸을 그리는 일은 여기서 하지 않는다. `qt/cells.py`(보통 줄·물리량 범위) ·
   `qt/matrix_card.py`(반복 그룹을 접은 표) · `qt/field_mask.py`(전송 필드)가
   각자 맡고, 이 파일은 **무엇을 어디에 놓을지**만 정한다.

   예전에는 넷이 한 파일에 있어 984 줄이었다. 서로를 모르고 `Row` 만 아는
   구조였으니 이미 분리돼 있었고, 파일만 안 나뉜 상태였다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.limits import DEFAULT_BAUD
from host.gui.qt.cell import Cell
from host.gui.qt.cells import RangeFields, RowWidget
from host.gui.qt.field_mask import FieldMaskCard
from host.gui.qt.matrix_card import MatrixCard
from host.gui.qt.parts import card_title, hairline
from host.gui.settings_form import (
    KEY_FIELD_MASK,
    Row,
    SettingsForm,
    TelemetryShape,
    group_label,
    matrix_of,
    telemetry_shape,
)
from host.gui.theme import Color, Space

#: 폼 한 줄이 차지할 최대 폭. 🔴 예전에는 사유 칸이 stretch 라 창을 넓힐수록
#: 오른쪽이 비었다 — 1130 px 캔버스에서 700 px 가 빈 채였다.
FORM_WIDTH = 620


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
        self._rows: dict[str, Cell] = {}
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
