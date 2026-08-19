"""스트림 화면 위젯 — Qt 위젯이 실제로 만들어지고 값이 흐르는지 검사한다.

🔴 오프스크린 플랫폼으로 돌린다(`test_qt_widgets.py` 와 같은 이유). 판정
   로직(창 기반 rate·일시정지·필터·서식)은 `host/tests/test_stream.py` 가
   Qt 없이 이미 본다. 여기서 보는 것은 **배선**이다 — 상태가 위젯에 실제로
   닿는가, 버튼·체크박스가 상태를 실제로 바꾸는가.

🔴 표(`QTableWidget`)를 `QPlainTextEdit` 콘솔로 바꿨다 — 매 프레임(워커
   틱마다, 100ms) 행 수백 개를 위젯으로 다시 만드는 비용이 시뮬레이터
   속도(초당 수십 줄)를 못 따라가 GUI 스레드 이벤트 큐가 무한히 밀리는
   것을 오프스크린 계측으로 실측했다(자세한 것은 stream-console-report.md).
   그래서 여기서 보는 것 중 하나가 "새 줄만 이어붙이고 통째로 다시
   그리지 않는가" 다 — 성능 회귀를 막는 시험이다.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 가 없으면 이 시험은 건너뛴다")

from PyQt6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from host.gui.qt.stream_view import StreamView  # noqa: E402
from host.gui.qt.style import stylesheet  # noqa: E402
from host.gui.stream import DISPLAY_MAXLEN, StreamState  # noqa: E402
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

    assert line in view._console.toPlainText()


def test_console_shows_raw_and_ma_separately_from_value(app):
    state = StreamState()
    state.ingest([_ain_line(1, raw=65528, ma=0.0001, value=0.0)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

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
    view._pause_btn.click()
    state.ingest([_ain_line(2)], now_s=1.0)
    view.render(state, now_s=1.0)
    view._pause_btn.click()                          # 다시 재생
    view.render(state, now_s=1.0)

    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _ain_line(2) in text


# --------------------------------------------------------------------- 필터

def test_catalog_types_are_hidden_by_default(app):
    """🔴 카탈로그 91줄이 텔레메트리를 파묻으면 안 된다."""
    state = StreamState()
    state.ingest([_cfg_item_line(1), _ain_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    text = view._console.toPlainText()
    assert _ain_line(2) in text
    assert "rail_24v" not in text
    assert view._filter_checks["cfg_item"].isChecked() is False
    assert view._filter_checks["ain"].isChecked() is True


def test_checking_a_hidden_catalog_type_reveals_it_retroactively(app):
    """🔴 체크박스를 켜면 그 전에 이미 온(그리고 숨겨졌던) 줄도 보여야
    한다 — 그러려면 필터가 바뀔 때만 예외적으로 통째로 다시 그린다."""
    state = StreamState()
    state.ingest([_cfg_item_line(1), _ain_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    assert "rail_24v" not in view._console.toPlainText()

    view._filter_checks["cfg_item"].setChecked(True)
    text = view._console.toPlainText()
    assert "rail_24v" in text
    assert _ain_line(2) in text          # 기존에 보이던 것도 그대로


def test_unchecking_a_shown_type_hides_it_retroactively(app):
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    text = view._console.toPlainText()
    assert _ain_line(1) in text and _i2c_line(2) in text

    view._filter_checks["i2c"].setChecked(False)
    text = view._console.toPlainText()
    assert _ain_line(1) in text
    assert _i2c_line(2) not in text


def test_rechecking_every_box_shows_everything_again(app):
    """🔴 되돌림 검사 — 필터를 무력화하면 다시 전부 보여야 한다."""
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._filter_checks["i2c"].setChecked(False)
    assert _i2c_line(2) not in view._console.toPlainText()

    view._filter_checks["i2c"].setChecked(True)
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
