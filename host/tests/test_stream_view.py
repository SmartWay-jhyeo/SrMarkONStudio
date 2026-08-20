"""스트림 화면 위젯 — Qt 위젯이 실제로 만들어지고 값이 흐르는지 검사한다.

🔴 오프스크린 플랫폼으로 돌린다(`test_qt_widgets.py` 와 같은 이유). 판정
   로직(창 기반 rate·일시정지·필터·서식)은 `host/tests/test_stream.py` 가
   Qt 없이 이미 본다. 여기서 보는 것은 **배선**이다 — 상태가 위젯에 실제로
   닿는가, 버튼·체크박스가 상태를 실제로 바꾸는가.

🔴 표(`QTableWidget`)를 `QPlainTextEdit` 콘솔로 바꿨다 — 매 프레임(워커
   틱마다, 100ms) 행 수백 개를 위젯으로 다시 만드는 비용이 도착 속도
   (초당 수십 줄)를 못 따라가 GUI 스레드 이벤트 큐가 무한히 밀리는
   것을 오프스크린 계측으로 실측했다(자세한 것은 stream-console-report.md).
   그래서 여기서 보는 것 중 하나가 "새 줄만 이어붙이고 통째로 다시
   그리지 않는가" 다 — 성능 회귀를 막는 시험이다.
"""

import os

# ---- 트리 필터 도우미 (2026-08-20, 칩 → 트리 전환) --------------------------
from PyQt6.QtCore import Qt as _Qt


def _is_on(item) -> bool:
    return item.checkState(0) != _Qt.CheckState.Unchecked


def _set_on(item, on: bool) -> None:
    item.setCheckState(0, _Qt.CheckState.Checked if on
                       else _Qt.CheckState.Unchecked)


import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 가 없으면 이 시험은 건너뛴다")

from PyQt6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from host.gui.qt.stream_view import StreamView  # noqa: E402
from host.gui.qt.style import stylesheet  # noqa: E402
from host.gui.stream import (  # noqa: E402
    DISPLAY_MAXLEN,
    KNOWN_CONNECTORS,
    StreamState,
)
from host.gui.theme import Color  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(stylesheet())
    yield a


def _ain_line(seq: int, *, raw: int = 8388608, ma: float = 12.0,
              value: float = 3.4) -> str:
    return (
        f'{{"schema_ver":3,"seq":{seq},"t":{seq * 100},"type":"ain",'
        f'"connector_id":3,"raw":{raw},"ma":{ma},"value":{value},'
        f'"unit":"bar","status":0}}'
    )


def _i2c_line(seq: int) -> str:
    return (
        f'{{"schema_ver":3,"seq":{seq},"t":{seq * 100},"type":"i2c",'
        f'"connector_id":10,"quantity":"lux","value":123.4,"status":0}}'
    )


def _cfg_item_line(seq: int) -> str:
    return (
        f'{{"schema_ver":3,"seq":{seq},"t":{seq * 100},"type":"cfg_item",'
        f'"key":"rail_24v","label":"24V 전원"}}'
    )


# --------------------------------------------------------------- 위젯 구성

def test_console_is_a_read_only_plain_text_edit(app):
    """🔴 표가 아니라 텍스트 콘솔이어야 한다 — 줄마다 위젯을 만들지 않는다."""
    view = StreamView()
    assert isinstance(view._console, QPlainTextEdit)
    assert view._console.isReadOnly()


def test_console_caps_scrollback_so_memory_cannot_grow_without_bound(app):
    """🔴 `maximumBlockCount` 로 오래된 줄이 위젯 안에서 자동으로 잘려야
    한다 — 근거는 `host/gui/stream.py` 의 `DISPLAY_MAXLEN` 과 같다(분석
    버퍼와 같은 상한을 쓴다: 이론적 최대 처리량 기준으로 이미 확정된
    수치를 재사용한다)."""
    view = StreamView()
    assert view._console.maximumBlockCount() == DISPLAY_MAXLEN


# ------------------------------------------------------------- 그리기·서식

def test_console_shows_the_raw_line_verbatim(app):
    state = StreamState()
    line = _ain_line(1)
    state.ingest([line], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다

    assert line in view._console.toPlainText()


def test_console_shows_raw_and_ma_separately_from_value(app):
    state = StreamState()
    state.ingest([_ain_line(1, raw=65528, ma=0.0001, value=0.0)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다

    text = view._console.toPlainText()
    assert "65528" in text
    assert "0.0001" in text


def test_header_label_names_the_columns(app):
    """🔴 열 이름표는 스크롤되는 콘솔 안이 아니라 고정 라벨이어야 한다 —
    안 그러면 `maximumBlockCount` 가 오래된 줄을 밀어낼 때 헤더까지
    같이 잘려 나간다."""
    view = StreamView()
    header_text = view._header_label.text()
    for col in ("seq", "t", "type", "value", "raw", "ma"):
        assert col in header_text


# --------------------------------------------------------------- 이어붙이기

def test_second_render_only_appends_new_lines_not_a_full_redraw(app):
    """🔴 성능 회귀 시험 — 매 프레임 통째로 다시 그리면 이전에 찍은 줄의
    콘솔 내부 블록이 전부 새로 만들어진다. 여기서는 대신 "이미 그린 줄
    수"(`_last_ordinal_shown`)가 딱 새로 온 만큼만 늘어나는지 본다."""
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    assert view._last_ordinal_shown == 0

    state.ingest([_ain_line(2), _ain_line(3)], now_s=0.1)
    view.render(state, now_s=0.1)
    assert view._last_ordinal_shown == 2

    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _ain_line(2) in text
    assert _ain_line(3) in text


def test_rendering_with_no_new_rows_does_not_touch_the_console(app):
    """새로 온 줄이 없으면 콘솔에 아무것도 더 찍지 않는다(중복 방지)."""
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    before = view._console.toPlainText()

    view.render(state, now_s=0.1)          # 새로 온 줄 없음
    after = view._console.toPlainText()
    assert before == after


# ------------------------------------------------------------------ 일시정지

def test_pause_freezes_the_console(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    view._pause_btn.click()
    assert state.paused is True

    state.ingest([_ain_line(2)], now_s=1.0)          # 뒤에서는 계속 받는다
    view.render(state, now_s=1.0)
    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _ain_line(2) not in text


def test_resume_appends_what_arrived_while_paused(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    view._pause_btn.click()
    state.ingest([_ain_line(2)], now_s=1.0)
    view.render(state, now_s=1.0)
    view._pause_btn.click()                          # 다시 재생
    view.render(state, now_s=1.0)

    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _ain_line(2) in text


# --------------------------------------------------------------------- 필터

def test_catalog_types_also_start_unchecked(app):
    """🔴 기본은 전부 꺼짐 (사용자 결정 2026-08-20). 다 켜 두면 연결하자마자
    초당 천 줄이 쏟아져 아무것도 못 읽는다 — 보고 싶은 것을 골라 켠다."""
    state = StreamState()
    state.ingest([_cfg_item_line(1), _ain_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    assert view._console.toPlainText() == ""
    assert _is_on(view._type_items["cfg_item"]) is False
    assert _is_on(view._type_items["ain"]) is False


def test_checking_a_hidden_catalog_type_reveals_it_retroactively(app):
    """🔴 체크박스를 켜면 그 전에 이미 온(그리고 숨겨졌던) 줄도 보여야
    한다 — 그러려면 필터가 바뀔 때만 예외적으로 통째로 다시 그린다."""
    state = StreamState()
    state.ingest([_cfg_item_line(1), _ain_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    _set_on(view._type_items["ain"], True)      # 기본 전체 해제 — ain 만 켠 상태에서
    assert "rail_24v" not in view._console.toPlainText()

    _set_on(view._type_items["cfg_item"], True)
    text = view._console.toPlainText()
    assert "rail_24v" in text
    assert _ain_line(2) in text          # 기존에 보이던 것도 그대로


def test_unchecking_a_shown_type_hides_it_retroactively(app):
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    text = view._console.toPlainText()
    assert _ain_line(1) in text and _i2c_line(2) in text

    _set_on(view._type_items["i2c"], False)
    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _i2c_line(2) not in text


def test_rechecking_every_box_shows_everything_again(app):
    """🔴 되돌림 검사 — 필터를 무력화하면 다시 전부 보여야 한다."""
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    _set_on(view._type_items["i2c"], False)
    assert _i2c_line(2) not in view._console.toPlainText()

    _set_on(view._type_items["i2c"], True)
    text = view._console.toPlainText()
    assert _ain_line(1) in text and _i2c_line(2) in text


# --------------------------------------------------------------- 멈춤 표시

def test_since_last_label_turns_fault_colour_when_stale(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=100.0)                  # 한참 지났다
    assert Color.FAULT in view._since_label.styleSheet()


def test_since_last_label_stays_calm_when_fresh(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.05)
    assert Color.FAULT not in view._since_label.styleSheet()


# ----------------------------------------------------------------- 파일 저장

def test_save_writes_the_buffer_as_ndjson(app, tmp_path):
    state = StreamState()
    state.ingest([_ain_line(1), _ain_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    out = tmp_path / "capture.ndjson"
    view.save_to_path(str(out))
    assert out.read_text(encoding="utf-8") == state.to_ndjson()


# ------------------------------------------------------------ 도착 간격 분포

def test_interval_label_shows_arrival_and_board_for_each_channel(app):
    state = StreamState()
    for i in range(3):
        state.ingest([_ain_line(i)], now_s=i * 0.1)

    view = StreamView()
    view.render(state, now_s=0.3)

    text = view._interval_label.text()
    assert "ain" in text
    assert "도착" in text
    assert "보드" in text


def test_interval_label_updates_as_more_channels_appear(app):
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    text = view._interval_label.text()
    assert "ain" in text
    assert "i2c" in text


# --------------------------------------------------------------- seq 누락 위치

def test_gaps_label_shows_recent_missing_ranges(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)
    state.ingest([_ain_line(5)], now_s=0.5)          # 2,3,4 누락

    view = StreamView()
    view.render(state, now_s=1.0)

    text = view._gaps_label.text()
    assert "2" in text and "4" in text


def test_gaps_label_says_none_when_nothing_is_missing(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    assert view._gaps_label.text() != ""


# ------------------------------------------------------------- 초당 줄 수 흐름

def test_spark_label_shows_a_non_empty_string_once_data_arrives(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    assert view._spark_label.text().strip() != ""


# ----------------------------------------------------------------- 커넥터 필터

def test_every_board_connector_gets_a_checkbox(app):
    """🔴 되돌림 검사 — 커넥터 목록을 **도착한 레코드에서만** 만들면 깨진다.

    사용자가 그것을 그대로 지적했다("스트림에 커넥터는 전부 있어야지 특정
    항목만 있으면 안돼지"). 대시보드가 값 없는 자리도 까는 것과 같은
    판단이다(설계 원칙 3).
    """
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)   # connector 3, 10

    view = StreamView()
    view.render(state, now_s=0.0)

    assert set(view._conn_items) == set(KNOWN_CONNECTORS)


def test_a_connector_that_never_sent_anything_says_so(app):
    """0 건이라는 것 자체가 정보다 — "저기서 값이 안 온다"."""
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)                 # connector 3 만

    view = StreamView()
    view.render(state, now_s=0.0)

    quiet = next(c for c in KNOWN_CONNECTORS if c != 3)
    assert view._conn_items[3].text(0) == "J3"
    assert view._conn_items[quiet].text(0) != f"J{quiet}"
    assert "0" in view._conn_items[quiet].text(0)


def test_the_label_drops_the_zero_mark_once_a_value_arrives(app):
    state = StreamState()
    view = StreamView()
    view.render(state, now_s=0.0)
    assert "0" in view._conn_items[3].text(0)

    state.ingest([_ain_line(1)], now_s=0.1)
    view.render(state, now_s=0.1)
    assert view._conn_items[3].text(0) == "J3"


# ------------------------------------------------------------- 전체 선택/해제

def test_select_all_and_clear_all_cover_types_and_connectors(app):
    """트리 전환(2026-08-20) 뒤 버튼은 둘이고 각각 고정된 뜻이다 —
    `전체 선택` 은 모든 타입·커넥터를 켜고, `전체 해제` 는 다 끈다."""
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)
    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    assert all(_is_on(cb) for cb in view._type_items.values()
               if cb.text(0) not in ("cfg_item", "cfg_field", "cfg_end"))

    view._conn_all_btn.click()          # 전체 해제
    assert not any(_is_on(cb) for cb in view._type_items.values())
    assert not any(_is_on(cb) for cb in view._conn_items.values())
    assert view._console.toPlainText() == ""

    view._type_all_btn.click()          # 전체 선택
    assert all(_is_on(cb) for cb in view._type_items.values())
    assert all(_is_on(cb) for cb in view._conn_items.values())
    text = view._console.toPlainText()
    assert _ain_line(1) in text and _i2c_line(2) in text


def test_a_parent_type_cascades_to_its_connectors(app):
    """트리의 요점 — 부모(ain)를 끄면 그 커넥터가 다 꺼진다."""
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)
    view = StreamView()
    view.render(state, now_s=0.0)
    _set_on(view._type_items["ain"], False)
    assert not any(_is_on(view._conn_items[c]) for c in range(3, 10))
    assert view._console.toPlainText() == ""


def test_an_empty_console_says_why_it_is_empty(app):
    """🔴 아무것도 안 보이는 화면은 고장으로 읽힌다. 필터를 몰래 되돌리는
    대신 이유를 적는다(`filter_note` 머리말)."""
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)
    view = StreamView()
    view.render(state, now_s=0.0)

    view._conn_all_btn.click()          # 전체 해제
    assert view._console.toPlainText() == ""
    assert "전체 선택" in view._console.placeholderText()

    view._type_all_btn.click()          # 전체 선택
    assert view._console.placeholderText() == ""


def test_unchecking_a_connector_hides_it_retroactively(app):
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    text = view._console.toPlainText()
    assert _ain_line(1) in text and _i2c_line(2) in text

    _set_on(view._conn_items[10], False)
    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _i2c_line(2) not in text


def test_rechecking_every_connector_shows_everything_again(app):
    """🔴 되돌림 검사 — 커넥터 필터를 무력화하면 다시 전부 보여야 한다."""
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._type_all_btn.click()   # 기본은 전체 해제(2026-08-20) — 이 시험의 주제가 아니라 켠다
    _set_on(view._conn_items[10], False)
    assert _i2c_line(2) not in view._console.toPlainText()

    _set_on(view._conn_items[10], True)
    text = view._console.toPlainText()
    assert _ain_line(1) in text and _i2c_line(2) in text


def test_everything_starts_unchecked_by_user_decision(app):
    """🔴 기본은 전부 꺼짐 (사용자 결정 2026-08-20). 다만 **목록에는 있어야
    한다** — '꺼짐(0건)' 과 '그런 것 없음' 은 다른 말이다. 켜면 이미 온
    줄도 소급해서 보인다."""
    line = ('{"schema_ver":3,"seq":1,"t":100,"type":"gnss",'
            '"lat":37.5,"lon":127.0,"status":0}')
    state = StreamState()
    state.ingest([line], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    assert "gnss" in view._type_items          # 목록에는 있다
    assert _is_on(view._type_items["gnss"]) is False
    assert view._console.toPlainText() == ""
    _set_on(view._type_items["gnss"], True)    # 켜면
    assert line in view._console.toPlainText()  # 소급해서 보인다
