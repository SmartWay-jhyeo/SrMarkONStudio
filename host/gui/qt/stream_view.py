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

🔴 **표(`QTableWidget`)가 아니라 `QPlainTextEdit` 콘솔이다.**

   예전에는 워커가 100ms 마다 표를 통째로 다시 채웠다. 시뮬레이터가
   초당 수십 줄을 내는 것만으로 이미 무거웠고(줄마다 8열 × `QTableWidgetItem`
   을 새로 만든다), 실기기에서 아날로그 7채널을 다 켜면 더 빨라진다.

   오프스크린으로 재현해 실측했다 — 워커는 100ms 마다 착실히 도는데,
   GUI 스레드가 그 표 재구성을 못 따라가면서 두 스레드 사이의 신호 처리
   간격이 5초 → 8초 → 11초 → 23초 → 47초+ 로 계속 벌어졌다(발산). 표
   재구성 자체가 다음 프레임이 오기 전에 안 끝나니 큐가 영원히 밀리는
   것이다. 사용자가 본 "GUI 가 스스로 죽었다" 는 이 발산이 응답 없음으로
   이어진 것으로 보인다(자세한 재현·계측은 stream-console-report.md).

   콘솔은 줄을 추가할 때 위젯을 새로 안 만든다 — 문자열 append 라 비용이
   행 수와 무관하게 상수에 가깝고, `setMaximumBlockCount` 로 오래된 줄이
   위젯 내부에서 자동으로 잘려 나간다(별도의 링버퍼 관리가 필요 없다).

   그래도 **매 프레임 전체를 다시 그리면 같은 문제가 재발한다.** 그래서
   `_last_ordinal_shown` 로 "어디까지 그렸는지" 를 들고 새로 온 줄만
   append 한다. 이 경계를 `StreamState`(Qt 를 모르는 층) 에 두지 않은
   이유: "무엇이 진짜로 일어났는가" 와 "화면에 이미 무엇을 그렸는가" 는
   다른 질문이다. 후자는 순전히 이 위젯 인스턴스의 그리기 상태이지
   스트림의 사실이 아니다 — 창을 새로 띄우거나 필터를 바꿔 다시 그려야
   할 때도 스트림 자체는 달라진 게 없다. `StreamRow.ordinal`(Qt 를 모르는
   층에서 매기는, deque 가 밀려나도 흔들리지 않는 번호)만 빌려 오면
   충분하다.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from host.gui.qt.parts import card_title, hairline
from host.gui.stream import (
    DEFAULT_HIDDEN_TYPES,
    DISPLAY_MAXLEN,
    StreamRow,
    StreamState,
    format_header,
    format_row,
    staleness_level,
)
from host.gui.theme import Color, Font, Space
from host.gui.widgets.status_chip import Level, Verification, chip_style


class StreamView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: StreamState | None = None
        self._last_now_s = 0.0
        #: 콘솔에 이미 찍은 줄 중 가장 큰 `StreamRow.ordinal`. `-1` 은
        #: "아직 아무것도 안 찍었다" 는 뜻이다 — 모든 ordinal(0부터 시작)
        #: 보다 작아 첫 render() 에서 지금 보이는 줄이 전부 새 줄로 잡힌다.
        self._last_ordinal_shown = -1
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

        # ---- 콘솔 ----------------------------------------------------------
        #: 열 이름표. 스크롤되는 콘솔 **안에 찍지 않는다** — 안에 찍으면
        #: `setMaximumBlockCount` 가 오래된 줄을 잘라낼 때 헤더까지 함께
        #: 사라진다. 항상 보여야 하므로 고정 라벨로 따로 둔다.
        self._header_label = QLabel(format_header())
        self._header_label.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" color: {Color.INK_DIM};"
        )

        self._console = QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # 🔴 근거: `host/gui/stream.py` 의 `DISPLAY_MAXLEN` 을 그대로
        #    쓴다 — 그 값 자체가 "7채널 × 100Hz" 이론적 상한에서 나온
        #    수치(머리말 참조)라, 화면에 남겨 둘 줄 수도 같은 근거를 쓰는
        #    것이 새 매직 넘버를 만드는 것보다 낫다. `QPlainTextEdit` 은
        #    `QTableWidget` 과 달리 이 상한을 유지하는 비용이 줄 수와
        #    무관하게 낮다(내부적으로 링버퍼처럼 동작하도록 설계된
        #    위젯이다) — 표에서 겪은 문제가 콘솔 크기 자체 때문이 아니라
        #    "매 프레임 통째로 다시 만드는 것" 때문이었다는 뜻이다.
        self._console.setMaximumBlockCount(DISPLAY_MAXLEN)
        self._console.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" background: {Color.SURFACE}; color: {Color.INK};"
            f" border: 1px solid {Color.LINE};"
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        col.setSpacing(Space.SM)
        col.addWidget(card_title("스트림 — NDJSON 원문"))
        col.addWidget(self._type_label)
        col.addLayout(summary_row)
        col.addWidget(hairline())
        col.addLayout(control_row)
        col.addWidget(self._header_label)
        col.addWidget(self._console, 1)

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

        self._append_new_rows(state.visible_rows())

    def _append_new_rows(self, rows: tuple[StreamRow, ...]) -> None:
        """새로 온 줄만 콘솔에 이어붙인다.

        🔴 여기가 성능의 핵심이다 — `rows` 전체가 아니라
           `_last_ordinal_shown` 보다 큰 것만 골라 찍는다. `rows` 는 이미
           `StreamState.visible_rows()` 가 일시정지·필터를 반영해 돌려준
           것이라, 정지 중이면 새 줄이 없어 아무것도 안 찍히고(화면이
           얼어붙은 것처럼 보이는 게 맞다), 필터가 걸려 있으면 걸러진
           타입은 애초에 `rows` 에 없어 나타나지 않는다.
        """
        new_rows = [r for r in rows if r.ordinal > self._last_ordinal_shown]
        if not new_rows:
            return
        for row in new_rows:
            self._console.appendPlainText(format_row(row))
        self._last_ordinal_shown = new_rows[-1].ordinal

    def _redraw_all(self) -> None:
        """콘솔을 비우고 지금 보이는 줄을 처음부터 다시 찍는다.

        🔴 **필터가 바뀔 때만** 부른다. 새로 체크(또는 해제)하면 이미
           찍힌 줄 중 일부가 소급해서 보이거나 숨어야 하는데, append-only
           로는 이미 그린 줄을 지울 수 없다. 매 프레임 부르면 표 시절과
           같은 문제가 재발하므로, 필터 토글처럼 드문 사용자 동작에서만
           쓴다(`_on_filter_toggled`).
        """
        if self._state is None:
            return
        self._console.clear()
        self._last_ordinal_shown = -1
        self._append_new_rows(self._state.visible_rows())

    # -------------------------------------------------------------- 필터
    def _ensure_filter_checks(self, state: StreamState) -> None:
        """새로 본 타입마다 체크박스를 만든다.

        🔴 카탈로그 타입(`DEFAULT_HIDDEN_TYPES`)은 기본이 꺼짐이다 — 연결
           직후 `$CFG,LIST` 카탈로그 91줄이 텔레메트리를 파묻는다
           (`host/gui/stream.py` 머리말). 그 밖은 기본이 켜짐(=숨기지 않음).

           이 시점(같은 `render()` 안, `_append_new_rows` 보다 먼저)에
           필터를 정하므로 새 타입의 첫 줄은 이미 올바른 필터를 통과해
           그려진다 — 카탈로그 타입이 처음 나타난 바로 그 틱에서도 소급
           다시 그리기(`_redraw_all`)가 필요 없다.
        """
        new_types = [t for t in sorted(state.type_counts)
                     if t not in self._filter_checks]
        if not new_types:
            return
        for t in new_types:
            cb = QCheckBox(t)
            cb.setChecked(t not in DEFAULT_HIDDEN_TYPES)
            cb.toggled.connect(self._on_filter_toggled)
            self._filter_row.addWidget(cb)
            self._filter_checks[t] = cb
        self._apply_filter_from_checks(state)

    def _checked_types(self) -> set[str]:
        return {t for t, cb in self._filter_checks.items() if cb.isChecked()}

    def _apply_filter_from_checks(self, state: StreamState) -> None:
        checked = self._checked_types()
        # 전부 체크 = 아무것도 거르지 않는다와 같다. `None` 으로 되돌려야
        # 나중에 나타날 타입도(아직 체크박스가 없는 것도) 계속 보인다.
        state.set_filter(
            None if len(checked) == len(self._filter_checks) else checked
        )

    def _on_filter_toggled(self, _checked: bool) -> None:
        if self._state is None:
            return
        self._apply_filter_from_checks(self._state)
        self._redraw_all()
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
