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
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.SM)

        label = QLabel(row.label)
        label.setMinimumWidth(200)
        lay.addWidget(label)

        self._editor = self._make_editor(row)
        self._editor.setEnabled(row.editable)
        lay.addWidget(self._editor, 1)

        if row.unit:
            unit = QLabel(row.unit)
            unit.setObjectName("dim")
            unit.setMinimumWidth(40)
            lay.addWidget(unit)

        self._note = QLabel()
        self._note.setObjectName("dim")
        self._note.setWordWrap(True)
        lay.addWidget(self._note, 1)

        if not row.editable and row.reason:
            # 🔴 보드가 준 이유를 그대로 띄운다. "읽기 전용" 이라고만 쓰면
            #    왜 못 바꾸는지가 사라진다 — 쿨링 팬이 5V 레일 직결이라는
            #    하드웨어 사실이 곧 사용자가 알아야 할 내용이다.
            self._note.setText(row.reason)
            self.setToolTip(row.reason)
            self._editor.setToolTip(row.reason)

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._form: SettingsForm | None = None
        self._rows: dict[str, RowWidget] = {}

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

        bar = QHBoxLayout()
        bar.addWidget(self._status, 1)
        bar.addWidget(self._revert)
        bar.addWidget(self._apply)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        col.addWidget(scroll, 1)
        col.addLayout(bar)

    # ------------------------------------------------------------- 구성

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
            title.setStyleSheet("font-weight: 600;")
            grid.addWidget(title)

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

        if errors:
            self._status.setText(f"고칠 것 {len(errors)}개 — 보낼 수 없다")
            self._status.setStyleSheet(f"color: {Color.FAULT};")
        elif pending:
            self._status.setText(f"바뀐 항목 {len(pending)}개")
            self._status.setStyleSheet("")
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
        self.set_form(self._form)

    # ------------------------------------------------------------- 응답

    def on_accepted(self, key: str) -> None:
        if self._form is None:
            return
        self._form.accept(key)
        if key in self._rows:
            self._rows[key].mark_dirty(False)
            self._rows[key].clear_error()
        self._refresh_buttons()

    def on_rejected(self, key: str, reason: str) -> None:
        """🔴 값을 되돌리지 않는다. 사용자가 방금 친 것을 지우면 무엇을
        고치려 했는지 사라진다. 사유를 보여 주고 고칠 기회를 준다."""
        if key in self._rows:
            self._rows[key].show_error(f"보드가 거부: {reason}")
        self._refresh_buttons()
