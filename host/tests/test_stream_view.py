"""스트림 화면 위젯 — Qt 위젯이 실제로 만들어지고 값이 흐르는지 검사한다.

🔴 오프스크린 플랫폼으로 돌린다(`test_qt_widgets.py` 와 같은 이유). 판정
   로직(창 기반 rate·일시정지·필터)은 `host/tests/test_stream.py` 가 Qt
   없이 이미 본다. 여기서 보는 것은 **배선**이다 — 상태가 위젯에 실제로
   닿는가, 버튼·체크박스가 상태를 실제로 바꾸는가.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 가 없으면 이 시험은 건너뛴다")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from host.gui.qt.stream_view import StreamView  # noqa: E402
from host.gui.qt.style import stylesheet  # noqa: E402
from host.gui.stream import StreamState  # noqa: E402
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


def test_table_shows_the_raw_line_verbatim(app):
    state = StreamState()
    line = _ain_line(1)
    state.ingest([line], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == line


def test_table_exposes_raw_adc_count_separately_from_value():
    """🔴 raw(ADC 카운트)가 value 와 다른 열에 그대로 보여야 한다.

    실기기에서 raw 가 0 근처에서 65528/-65516 처럼 튀는 것을 값만 봐서는
    알 수 없다.
    """
    from PyQt6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    state = StreamState()
    state.ingest([_ain_line(1, raw=65528, ma=0.0001, value=0.0)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    header = [view._table.horizontalHeaderItem(c).text()
              for c in range(view._table.columnCount())]
    raw_col = header.index("raw")
    value_col = header.index("value")
    assert view._table.item(0, raw_col).text() == "65528"
    assert view._table.item(0, value_col).text() == "0.0"


def test_raw_line_cell_carries_the_full_text_as_a_tooltip(app):
    """🔴 원문 열이 좁으면 "…" 로 잘린다 — 열을 다 못 넓혀도 호버(또는 행을
    고르는 것)로 전체 원문을 볼 수 있어야 한다."""
    state = StreamState()
    line = _ain_line(1)
    state.ingest([line], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    assert view._table.item(0, 0).toolTip() == line


def test_raw_line_column_stretches_to_fill_the_table(app):
    """🔴 원문 열이 고정폭이면 나머지 좁은 열(seq·t·type 등)이 자리를 남기고도
    원문이 잘린다. 원문 열만 Stretch 여야 창을 넓히면 원문도 함께 넓어진다."""
    from PyQt6.QtWidgets import QHeaderView

    view = StreamView()
    header = view._table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    for c in range(1, view._table.columnCount()):
        assert header.sectionResizeMode(c) != QHeaderView.ResizeMode.Stretch


def test_pause_button_freezes_the_table(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._pause_btn.click()
    assert state.paused is True

    state.ingest([_ain_line(2)], now_s=1.0)          # 뒤에서는 계속 받는다
    view.render(state, now_s=1.0)
    assert view._table.rowCount() == 1               # 화면은 그대로


def test_resume_shows_what_arrived_while_paused(app):
    state = StreamState()
    state.ingest([_ain_line(1)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._pause_btn.click()
    state.ingest([_ain_line(2)], now_s=1.0)
    view.render(state, now_s=1.0)
    view._pause_btn.click()                          # 다시 재생
    view.render(state, now_s=1.0)
    assert view._table.rowCount() == 2


def test_unchecking_a_type_filters_the_table(app):
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    assert view._table.rowCount() == 2

    view._filter_checks["i2c"].setChecked(False)
    view.render(state, now_s=0.0)
    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == _ain_line(1)


def test_rechecking_every_box_shows_everything_again(app):
    """🔴 되돌림 검사 — 필터를 무력화하면 다시 전부 보여야 한다."""
    state = StreamState()
    state.ingest([_ain_line(1), _i2c_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)
    view._filter_checks["i2c"].setChecked(False)
    view.render(state, now_s=0.0)
    assert view._table.rowCount() == 1

    view._filter_checks["i2c"].setChecked(True)
    view.render(state, now_s=0.0)
    assert view._table.rowCount() == 2


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


def test_save_writes_the_buffer_as_ndjson(app, tmp_path):
    state = StreamState()
    state.ingest([_ain_line(1), _ain_line(2)], now_s=0.0)

    view = StreamView()
    view.render(state, now_s=0.0)

    out = tmp_path / "capture.ndjson"
    view.save_to_path(str(out))
    assert out.read_text(encoding="utf-8") == state.to_ndjson()
