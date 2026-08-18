"""스트림 화면 — NDJSON 이 실제로 오는지 보여주고, 분석 가능하게 만든다.

🔴 판정은 여기 없다. "지금 뭘 보여줄지" 는 전부 `host/gui/stream.py`
   (`StreamState`)가 정하고, 이 파일은 그것을 그리기만 한다 — `screen.py` /
   `qt/dashboard.py` 와 같은 관계다.

🔴 `render(state, now_s)` 는 `qt/view.py` 의 `View` 계약(=`render(state)`)과
   서명이 다르다. 그래서 `app.py` 의 `_views` 목록에 넣지 않는다 — 요약
   수치가 "지금이 언제인가" 를 알아야 하는데(`StreamState.summary`), 다른
   뷰들은 `ScreenState` 에 실려 오는 텔레메트리 값만 보고 시각을 스스로
   재지 않는다. `tare_panel`(설정 화면의 영점 패널)도 같은 이유로 목록
   밖에서 직접 불린다.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from host.gui.qt.parts import card_title, hairline
from host.gui.stream import StreamRow, StreamState, staleness_level
from host.gui.theme import Color, Font, Space
from host.gui.widgets.status_chip import Level, Verification, chip_style

#: 표에 그릴 최근 줄 수. `StreamState` 의 분석 버퍼(`DISPLAY_MAXLEN`)와는
#: 다른 상한이다 — 저건 창 계산·전체 저장을 위한 것이고, 이건 **화면에
#: 그리는** 상한이다. 워커가 100ms 마다 표를 통째로 다시 채우므로, 2000줄
#: 전부를 매번 그리면 눈에 띄게 느려진다(`screen.py` 의 `TRACE_LEN` 주석과
#: 같은 "화면에서 안 보이면 의미 없다" 판단).
DISPLAY_ROWS = 300

_HEADERS = ("원문", "seq", "t", "type", "커넥터", "value", "raw", "ma")


def _fmt(v) -> str:
    return "" if v is None else str(v)


class StreamView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: StreamState | None = None
        self._last_now_s = 0.0
        #: 타입별 필터 체크박스. 고정 목록을 미리 두지 않는다 — 어떤
        #: type 이 실리는지는 프로토콜이 정하지 규격 문자열을 여기서
        #: 다시 아는 척하지 않는다(CLAUDE.md 설계 원칙 1과 같은 결).
        #: 처음 본 타입을 순서대로 만든다.
        self._filter_checks: dict[str, QCheckBox] = {}

        # ---- 요약 ------------------------------------------------------
        self._type_label = QLabel("")
        self._type_label.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
        )
        self._seq_label = QLabel("")
        self._bytes_label = QLabel("")
        self._since_label = QLabel("")
        self._since_label.setStyleSheet(f"font-weight: 700; color: {Color.INK_DIM};")

        summary_row = QHBoxLayout()
        summary_row.setSpacing(Space.LG)
        summary_row.addWidget(self._seq_label)
        summary_row.addWidget(self._bytes_label)
        summary_row.addWidget(self._since_label)
        summary_row.addStretch(1)

        # ---- 필터 · 조작 -------------------------------------------------
        self._filter_row = QHBoxLayout()
        self._filter_row.setSpacing(Space.SM)
        filter_host = QWidget()
        filter_host.setLayout(self._filter_row)

        self._pause_btn = QPushButton("일시정지")
        self._pause_btn.setCheckable(True)
        self._pause_btn.clicked.connect(self._on_pause_clicked)

        self._save_btn = QPushButton("파일로 저장")
        self._save_btn.clicked.connect(self._on_save_clicked)

        control_row = QHBoxLayout()
        control_row.setSpacing(Space.SM)
        control_row.addWidget(filter_host, 1)
        control_row.addWidget(self._pause_btn)
        control_row.addWidget(self._save_btn)

        # ---- 표 ----------------------------------------------------------
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setStyleSheet(f"font-family: {Font.MONO};")
        self._table.horizontalHeader().setStretchLastSection(False)
        # 🔴 원문(0열)이 고정 420px 라 NDJSON 한 줄(보통 150~250B)이 "…" 로
        #    잘려 정작 원문을 못 읽었다. 나머지 열(seq·t·type·커넥터·value·
        #    raw·ma)은 내용에 맞춰 좁게, 원문 열만 남는 폭을 전부 가져가게
        #    한다 — 창을 넓히면 원문도 함께 넓어진다.
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(_HEADERS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        # 🔴 그래도 창이 좁으면 여전히 잘릴 수 있다 — 행을 고르면(호버해도)
        #    전체를 볼 수 있게 툴팁에 원문을 그대로 싣는다(`_update_table`).

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(Space.SM)
        col.addWidget(card_title("스트림 — NDJSON 원문"))
        col.addWidget(self._type_label)
        col.addLayout(summary_row)
        col.addWidget(hairline())
        col.addLayout(control_row)
        col.addWidget(self._table, 1)

    # ------------------------------------------------------------- 그리기
    def render(self, state: StreamState, now_s: float) -> None:
        self._state = state
        self._last_now_s = now_s
        self._ensure_filter_checks(state)

        summary = state.summary(now_s)
        self._type_label.setText(
            "   ".join(
                f"{t.type} {t.rate_per_s:.1f}/s (누적 {t.count_total})"
                for t in summary.types
            ) or "수신된 줄 없음"
        )
        self._seq_label.setText(f"seq 누락 {summary.seq_missing}")
        self._bytes_label.setText(
            f"{summary.bytes_per_s:.0f} B/s · 총 {summary.total_lines}줄 "
            f"/ {summary.total_bytes}B"
        )

        level = staleness_level(summary.since_last_s)
        # 🔴 `Verification.VERIFIED` 를 강제로 넘긴다 — chip_style 은
        #    "확인 여부" 와 "심각도" 를 함께 보는데, 여기 있는 심각도
        #    (`staleness_level`)는 이미 확정된 판정이라 확인 여부를 따로
        #    묻지 않는다. `.fill` 을 쓴다: VERIFIED 조합은 항상 채운
        #    칩(글자=SURFACE 흰색)을 돌려주므로, 배경이 없는 라벨에서는
        #    칩의 "색" 그 자체인 `.fill` 이 필요하지 `.text` 가 아니다.
        style = chip_style(level, Verification.VERIFIED)
        self._since_label.setStyleSheet(f"font-weight: 700; color: {style.fill};")
        if summary.since_last_s is None:
            self._since_label.setText("수신 없음")
        else:
            self._since_label.setText(f"마지막 수신 {summary.since_last_s:.1f}초 전")

        self._pause_btn.setChecked(state.paused)
        self._pause_btn.setText("재생" if state.paused else "일시정지")

        self._update_table(state.visible_rows())

    def _update_table(self, rows: tuple[StreamRow, ...]) -> None:
        shown = rows[-DISPLAY_ROWS:]
        self._table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            cells = (
                row.line, _fmt(row.seq), _fmt(row.t), row.type,
                _fmt(row.connector), _fmt(row.value), _fmt(row.raw_count),
                _fmt(row.ma),
            )
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    # 🔴 열이 좁아 "…" 로 잘려도 마우스를 올리면(또는
                    #    행을 골라도) 전체 원문을 볼 수 있게 한다.
                    item.setToolTip(row.line)
                self._table.setItem(r, c, item)

    # -------------------------------------------------------------- 필터
    def _ensure_filter_checks(self, state: StreamState) -> None:
        """새로 본 타입마다 체크박스를 만든다. 기본은 체크됨(=숨기지 않음)."""
        new_types = [t for t in sorted(state.type_counts)
                     if t not in self._filter_checks]
        if not new_types:
            return
        for t in new_types:
            cb = QCheckBox(t)
            cb.setChecked(True)
            cb.toggled.connect(self._on_filter_toggled)
            self._filter_row.addWidget(cb)
            self._filter_checks[t] = cb
        if state.filter_types is not None:
            # 🔴 이미 필터링 중이었다면 새 타입도 그 규칙을 따라야 한다.
            #    체크박스는 기본이 체크됨이므로 새로 추가된 타입은 그대로
            #    보여야 하고, 그러려면 필터 집합에 지금 즉시 넣어야 한다.
            state.set_filter(self._checked_types())

    def _checked_types(self) -> set[str]:
        return {t for t, cb in self._filter_checks.items() if cb.isChecked()}

    def _on_filter_toggled(self, _checked: bool) -> None:
        if self._state is None:
            return
        checked = self._checked_types()
        # 전부 체크 = 아무것도 거르지 않는다와 같다. `None` 으로 되돌려야
        # 나중에 나타날 타입도(아직 체크박스가 없는 것도) 계속 보인다.
        self._state.set_filter(
            None if len(checked) == len(self._filter_checks) else checked
        )
        self.render(self._state, self._last_now_s)

    # ------------------------------------------------------------- 조작
    def _on_pause_clicked(self) -> None:
        if self._state is None:
            return
        if self._pause_btn.isChecked():
            self._state.pause()
        else:
            self._state.resume()
        self.render(self._state, self._last_now_s)

    def save_to_path(self, path: str) -> None:
        """지금 버퍼에 있는 원문 줄을 그대로 `.ndjson` 으로 떨군다.

        🔴 대화상자와 분리해 둔다 — 시험이 파일 대화상자 없이 저장 동작을
        확인할 수 있어야 한다.
        """
        if self._state is None:
            return
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self._state.to_ndjson())

    def _on_save_clicked(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "스트림 저장", "stream.ndjson", "NDJSON (*.ndjson)"
        )
        if path:
            self.save_to_path(path)
