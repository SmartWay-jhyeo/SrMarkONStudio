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

   예전에는 워커가 100ms 마다 표를 통째로 다시 채웠다. 초당 수십 줄만
   들어와도 이미 무거웠고(줄마다 8열 × `QTableWidgetItem`
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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QSizePolicy,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from host.gui.qt.parts import card_title, hairline
from host.gui.stream import (
    DEFAULT_HIDDEN_TYPES,
    DISPLAY_MAXLEN,
    StreamRow,
    StreamState,
    KNOWN_TYPES,
    TYPE_FAMILIES,
    type_sort_key,
    connector_label,
    filter_note,
    format_gap,
    format_header,
    format_interval,
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
        self._type_items: dict[str, QTreeWidgetItem] = {}
        #: 커넥터별 필터 체크박스. `ain` 7채널이 한 덩어리라 J4 만
        #: 이상해도 타입 필터만으로는 못 가려낸다 — 축 하나를 더 둔다.
        #:
        #: 🔴 타입과 달리 **보드에 있는 커넥터가 처음부터 전부** 들어온다
        #:    (`StreamState.connector_counts` 가 0 으로 미리 깔아 준다).
        #:    값이 안 오는 커넥터가 목록에 없으면 화면이 "그런 커넥터는
        #:    없다" 고 말하는 셈이다.
        self._conn_items: dict[int, QTreeWidgetItem] = {}

        # ---- 요약 ------------------------------------------------------
        self._type_label = QLabel("")
        self._type_label.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
        )
        self._seq_label = QLabel("")
        self._bytes_label = QLabel("")
        self._since_label = QLabel("")
        self._since_label.setStyleSheet(f"font-weight: 700; color: {Color.INK_DIM};")
        #: 초당 줄 수의 최근 흐름 — 문자 스파크라인(`host/gui/stream.py`
        #: `_rate_sparkline` 머리말: 그래프 라이브러리를 새로 안 쓰는
        #: 이유가 거기 있다).
        self._spark_label = QLabel("")
        self._spark_label.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_MD}pt;"
            f" color: {Color.INK_DIM};"
        )

        summary_row = QHBoxLayout()
        summary_row.setSpacing(Space.LG)
        summary_row.addWidget(self._seq_label)
        summary_row.addWidget(self._bytes_label)
        summary_row.addWidget(self._since_label)
        summary_row.addWidget(self._spark_label)
        summary_row.addStretch(1)

        # ---- 간격 분석 -------------------------------------------------
        #
        # 🔴 HANDOFF.md §3 미해결 문제("표본 간격 202ms")를 갈라 보는 곳.
        #    도착과 보드 간격을 나란히 찍는 이유는 `format_interval` 의
        #    머리말과 같다 — 판정은 전부 `stream.py` 에서 끝났고 여기는
        #    그 문자열을 그대로 라벨에 앉히기만 한다.
        self._interval_label = QLabel("")
        self._interval_label.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" color: {Color.INK};"
        )

        #: 최근 seq 누락 구간. 총계(`_seq_label`)는 위에 이미 있다 — 여기는
        #: "어디서" 를 보여준다.
        self._gaps_label = QLabel("")
        self._gaps_label.setStyleSheet(
            f"font-family: {Font.MONO}; font-size: {Font.SIZE_SM}pt;"
            f" color: {Color.INK_DIM};"
        )

        analysis_row = QHBoxLayout()
        analysis_row.setSpacing(Space.LG)
        analysis_row.addWidget(self._interval_label, 2)
        analysis_row.addWidget(self._gaps_label, 1)

        # ---- 필터 · 조작 -------------------------------------------------
        # 🔴 트리 (사용자 요청 2026-08-20 — "일자로 쭉 늘어트리지 말고 트리
        #    구조로"). 커넥터는 정확히 한 타입에 속하므로(TYPE_FAMILIES)
        #    "타입 아래에 커넥터" 가 자연스럽고, 납작한 칩 두 줄이 요구하던
        #    가로 폭 문제도 세로로 풀린다 — 트리는 제 스크롤을 가진다.
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setFixedHeight(210)
        self._tree.itemChanged.connect(self._on_tree_changed)
        #: itemChanged 를 프로그램이 낸 것인지 사람이 낸 것인지 가른다.
        self._tree_updating = False

        self._type_all_btn = QPushButton("전체 선택")
        self._type_all_btn.clicked.connect(lambda: self._set_all(True))
        self._conn_all_btn = QPushButton("전체 해제")
        self._conn_all_btn.clicked.connect(lambda: self._set_all(False))

        filters_col = QVBoxLayout()
        filters_col.setSpacing(2)
        head_row = QHBoxLayout()
        head_row.setSpacing(Space.SM)
        head_row.addWidget(card_title("필터"))
        head_row.addWidget(self._type_all_btn)
        head_row.addWidget(self._conn_all_btn)
        head_row.addStretch(1)
        filters_col.addLayout(head_row)
        filters_col.addWidget(self._tree)

        filters_host = QWidget()
        filters_host.setLayout(filters_col)

        self._pause_btn = QPushButton("일시정지")
        self._pause_btn.setCheckable(True)
        self._pause_btn.clicked.connect(self._on_pause_clicked)

        self._save_btn = QPushButton("파일로 저장")
        self._save_btn.clicked.connect(self._on_save_clicked)

        control_row = QHBoxLayout()
        control_row.setSpacing(Space.SM)
        control_row.addWidget(filters_host, 1)
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
        # 🔴 고정폭 헤더가 창의 최소폭이 되면 안 된다(923px). 이 최소폭이
        #    QStackedWidget 을 타고 대시보드까지 넓혀 카드가 창 밖으로
        #    나갔다(2026-08-20). 좁으면 헤더 뒤가 잘리는 쪽이 맞다 —
        #    콘솔 본문도 같은 폭에서 어차피 잘린다.
        self._header_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Preferred)

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
        col.addLayout(analysis_row)
        col.addWidget(hairline())
        col.addLayout(control_row)
        col.addWidget(self._header_label)
        col.addWidget(self._console, 1)

    # ------------------------------------------------------------- 그리기
    def render(self, state: StreamState, now_s: float) -> None:
        self._state = state
        self._last_now_s = now_s
        self._ensure_tree_items(state)

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

        # 🔴 판정은 전부 `StreamState.summary()` 안에서 끝났다(간격 창·
        #    seq 구간·스파크라인 버킷 모두 고정 크기라 여기서도 상수
        #    비용이다) — 여기는 그 결과를 라벨 텍스트로 앉히기만 한다.
        self._interval_label.setText(
            "\n".join(format_interval(ci) for ci in summary.intervals)
            or "표본 없음"
        )
        self._gaps_label.setText(
            "\n".join(format_gap(g, now_s) for g in reversed(summary.recent_gaps))
            or "seq 누락 없음"
        )
        self._spark_label.setText(summary.rate_sparkline)
        self._refresh_tree_labels(state)

        rows = state.visible_rows()
        # 🔴 빈 콘솔은 고장으로 읽힌다. 왜 비었는지를 같은 자리에 적는다 —
        #    판정은 `stream.filter_note` 가 한다(그 머리말에 근거가 있다).
        #    `QPlainTextEdit` 의 placeholder 는 문서가 비었을 때만 뜨므로,
        #    줄이 하나라도 그려지면 저절로 사라진다.
        self._console.setPlaceholderText(filter_note(state, rows))
        self._append_new_rows(rows)

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
    def _ensure_tree_items(self, state: StreamState) -> None:
        """새로 본 타입마다 트리 항목을 만든다. 가족 타입(ain·i2c·din)은
        자식으로 커넥터를 미리 다 깐다 — 값이 안 온 커넥터도 목록에 있어야
        한다(0 건 자체가 정보다).

        🔴 카탈로그 타입(`DEFAULT_HIDDEN_TYPES`)은 기본이 꺼짐이다 — 연결
           직후 `$CFG,LIST` 가 쏟아내는 91줄이 텔레메트리를 파묻는다.
        """
        new_types = [t for t in sorted(set(state.type_counts)
                                       | set(TYPE_FAMILIES)
                                       | set(KNOWN_TYPES))
                     if t not in self._type_items]
        if not new_types:
            return
        self._tree_updating = True
        try:
            for t in new_types:
                # 우선순위 자리에 끼운다 — 도착 순서가 아니라 중요도순.
                item = QTreeWidgetItem([t])
                keys = [type_sort_key(x) for x in self._type_items]
                pos = sum(1 for k in keys if k <= type_sort_key(t))
                self._tree.insertTopLevelItem(pos, item)
                on = t not in DEFAULT_HIDDEN_TYPES
                kids = TYPE_FAMILIES.get(t, ())
                if kids:
                    # 🔴 AutoTristate — 부모를 누르면 자식이 다 따라가고,
                    #    자식 일부만 켜면 부모가 반쯤 찬 표시가 된다.
                    item.setFlags(item.flags()
                                  | Qt.ItemFlag.ItemIsUserCheckable
                                  | Qt.ItemFlag.ItemIsAutoTristate)
                    for c in kids:
                        kid = QTreeWidgetItem(item, [
                            connector_label(
                                c, state.connector_counts.get(c, 0),
                                state.connector_names.get(c, ""))])
                        kid.setFlags(kid.flags()
                                     | Qt.ItemFlag.ItemIsUserCheckable)
                        kid.setCheckState(
                            0, Qt.CheckState.Checked if on
                            else Qt.CheckState.Unchecked)
                        self._conn_items[c] = kid
                    item.setExpanded(False)
                else:
                    item.setFlags(item.flags()
                                  | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0, Qt.CheckState.Checked if on
                    else Qt.CheckState.Unchecked)
                self._type_items[t] = item
        finally:
            self._tree_updating = False
        self._apply_filters(state)

    def _refresh_tree_labels(self, state: StreamState) -> None:
        """건수 표시를 갱신한다 — 글자가 바뀔 때만 위젯을 만진다."""
        for t, item in self._type_items.items():
            n = state.type_counts.get(t, 0)
            text = f"{t}  {n}" if n else t
            if item.text(0) != text:
                item.setText(0, text)
        for c, kid in self._conn_items.items():
            text = connector_label(c, state.connector_counts.get(c, 0),
                                   state.connector_names.get(c, ""))
            if kid.text(0) != text:
                kid.setText(0, text)

    def _checked_types(self) -> set[str]:
        # 🔴 반쯤 찬(PartiallyChecked) 부모도 "켜짐"이다 — 자식 일부가
        #    보이는 중이니 그 타입의 줄은 통과해야 한다.
        return {t for t, it in self._type_items.items()
                if it.checkState(0) != Qt.CheckState.Unchecked}

    def _checked_connectors(self) -> set[int]:
        return {c for c, it in self._conn_items.items()
                if it.checkState(0) == Qt.CheckState.Checked}

    def _apply_filters(self, state: StreamState) -> None:
        checked_t = self._checked_types()
        # 전부 체크 = 아무것도 거르지 않는다. None 으로 되돌려야 나중에
        # 나타날 타입(아직 항목이 없는 것)도 계속 보인다.
        state.set_filter(
            None if len(checked_t) == len(self._type_items) else checked_t)
        checked_c = self._checked_connectors()
        state.set_connector_filter(
            None if len(checked_c) == len(self._conn_items) else checked_c)

    def _on_tree_changed(self, _item, _column) -> None:
        # AutoTristate 의 연쇄 갱신(부모→자식)도 이 신호로 오므로,
        # 프로그램이 만드는 중이면 무시한다.
        if self._tree_updating or self._state is None:
            return
        self._apply_filters(self._state)
        self._redraw_all()
        self.render(self._state, self._last_now_s)

    def _set_all(self, on: bool) -> None:
        """전체 선택/해제 — 타입과 커넥터를 한꺼번에."""
        self._tree_updating = True
        try:
            st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            for it in self._type_items.values():
                it.setCheckState(0, st)
            for it in self._conn_items.values():
                it.setCheckState(0, st)
        finally:
            self._tree_updating = False
        if self._state is None:
            return
        self._apply_filters(self._state)
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
