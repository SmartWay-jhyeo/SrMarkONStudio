"""설정 화면. 폼 구성은 `settings_form.py` 가 정한다.

🔴 항목을 하드코딩하지 않는다. `$CFG,LIST` 응답만으로 그린다 — 어떤 항목이
   있는지, 무슨 위젯인지, 범위가 얼마인지 전부 보드가 알려 준다.
"""

from __future__ import annotations

from collections.abc import Callable

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
    QVBoxLayout,
    QWidget,
)

from host.gui.settings_form import Row, SettingsForm, Widget, group_label
from host.gui.theme import Color, Space


class RowWidget(QWidget):
    """설정 한 줄. 라벨 · 입력 · 단위 · 사유."""

    changed = pyqtSignal(str, str)   # key, text

    def __init__(self, row: Row, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = row.key
        self._editor: QWidget

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(Space.MD)

        label = QLabel(row.label)
        label.setFixedWidth(190)
        if not row.editable:
            label.setStyleSheet(f"color: {Color.INK_DIM};")
        lay.addWidget(label)

        self._editor = self._make_editor(row)
        self._editor.setEnabled(row.editable)
        self._editor.setFixedWidth(150)
        lay.addWidget(self._editor)

        unit = QLabel(row.unit)
        unit.setObjectName("dim")
        unit.setFixedWidth(44)
        lay.addWidget(unit)

        # 사유·오류가 들어가는 자리. 항상 같은 위치라 눈이 찾기 쉽다.
        self._note = QLabel()
        self._note.setObjectName("dim")
        self._note.setWordWrap(True)
        lay.addWidget(self._note, 1)

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
            self._note.setText(text)
            self.setToolTip(text)
            self._editor.setToolTip(text)

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

    def show_error(self, message: str) -> None:
        self._note.setText(message)
        self._note.setStyleSheet(f"color: {Color.FAULT};")
        self._editor.setStyleSheet(f"border: 2px solid {Color.FAULT};")

    def clear_error(self, fallback: str = "") -> None:
        self._note.setText(fallback)
        self._note.setStyleSheet("")
        self._editor.setStyleSheet("")

    def mark_dirty(self, dirty: bool) -> None:
        self._editor.setStyleSheet(
            f"border: 2px solid {Color.PROBING};" if dirty else ""
        )


class SettingsPage(QWidget):
    """카탈로그로 그린 설정 화면."""

    #: 보드에 보낼 (키, 값) 목록
    apply_requested = pyqtSignal(list)
    #: $CFG,SAVE — Flash 에 남긴다
    save_requested = pyqtSignal()
    #: $CFG,RESET — 전부 기본값으로
    reset_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._form: SettingsForm | None = None
        self._rows: dict[str, RowWidget] = {}
        #: 보드에 적용됐지만 아직 Flash 에 저장되지 않은 변경이 있는가.
        #: 🔴 보드의 dirty 플래그와 별개로 호스트가 자기가 보낸 것을 센다 —
        #:    보드는 그것을 알려 주지 않고, 물어볼 명령도 없다.
        self._unsaved = False
        #: 시험에서 확인 대화상자를 건너뛰기 위한 고리.
        self._confirm = self._ask_confirm

        self._body = QVBoxLayout()
        self._body.setSpacing(Space.MD)

        inner = QWidget()
        inner.setLayout(self._body)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

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
        #    이 앱이 "명령됨 / 확인됨" 을 구분하는 것과 같은 부류다.
        self._save = QPushButton("보드에 저장")
        self._save.setEnabled(False)
        self._save.clicked.connect(lambda: self.save_requested.emit())

        self._reset = QPushButton("기본값으로")
        self._reset.setEnabled(False)
        self._reset.clicked.connect(self._on_reset)

        bar = QHBoxLayout()
        bar.setSpacing(Space.SM)
        bar.addWidget(self._status, 1)
        bar.addWidget(self._reset)
        bar.addWidget(self._revert)
        bar.addWidget(self._apply)
        bar.addWidget(self._save)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        col.addWidget(scroll, 1)
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
        self._form = form
        self._rows.clear()
        while self._body.count():
            item = self._body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for group in form.groups:
            card = QFrame()
            card.setObjectName("card")
            grid = QVBoxLayout(card)
            grid.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
            grid.setSpacing(Space.SM)

            title = QLabel(group_label(group.name))
            title.setStyleSheet(
                f"color: {Color.INK_DIM}; font-size: 10pt; font-weight: 700;"
                f" letter-spacing: 1px;"
            )
            grid.addWidget(title)
            rule = QFrame()
            rule.setFrameShape(QFrame.Shape.HLine)
            rule.setStyleSheet(f"color: {Color.LINE};")
            rule.setFixedHeight(1)
            grid.addWidget(rule)
            grid.addSpacing(Space.XS)

            for row in group.rows:
                w = RowWidget(row)
                w.changed.connect(self._on_changed)
                self._rows[row.key] = w
                grid.addWidget(w)

            self._body.addWidget(card)

        self._body.addStretch(1)
        self._refresh_buttons()

    # ------------------------------------------------------------- 편집

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
