"""메인 윈도우가 보드 없이 실제로 뜨는지 검사한다.

🔴 창이 뜨는 것과 뷰 계약이 지켜지는 것은 **호스트 코드의 성질**이라 보드가
   없어도 확인할 수 있어야 한다. 전선 반대편은 시험용 스텁
   (`host/tests/fake_board.py`)이 맡는다 — 예전에는 `--port sim` 이 그
   자리였지만 시뮬레이터는 없앴다(2026-08-20).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 가 없으면 건너뛴다")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from host.gui.app import MainWindow  # noqa: E402
from host.gui.qt.dashboard import AIN_COUNT, Dashboard  # noqa: E402
from host.gui.qt.rail import RailRow  # noqa: E402
from host.gui.qt.style import stylesheet  # noqa: E402
from host.gui.widgets.status_chip import Level, Verification  # noqa: E402
from host.gui.worker_loop import StepResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(stylesheet())
    yield a


@pytest.fixture
def window(app):
    from host.tests.fake_board import fake_service

    service = fake_service(clock=lambda: 0)
    w = MainWindow(service, "스텁")
    yield w
    w._worker.stop()


# ------------------------------------------------------------------ 창

def test_window_opens_without_a_board(window):
    assert window.windowTitle().startswith("MarkON Studio")
    # 🔴 탭 이름표와 실제 페이지 수가 어긋나면 상단에서 고른 탭이 엉뚱한
    #    화면을 연다. 숫자를 손으로 적지 않고 `PAGES` 와 대조한다 — 화면이
    #    늘 때마다 이 시험을 고치게 하면 그 대조가 사라진다.
    from host.gui.app import PAGES

    assert window._pages.count() == len(PAGES)


def test_every_view_keeps_the_contract(window):
    """🔴 뷰 계약(qt/view.py)이 실제로 강제되는지.

    예전에는 오리 타입이었다 — `render` 를 빠뜨린 뷰를 넣으면 창은 뜨고
    **다음 워커 주기에** 터진다. Protocol 을 적어 두고 아무도 확인하지
    않으면 그것은 계약이 아니라 주석이다.
    """
    from host.gui.qt.view import View

    assert window._views, "뷰가 하나는 있어야 한다"
    for v in window._views:
        assert isinstance(v, View), f"{type(v).__name__} 이 render 를 안 가졌다"


def test_a_view_without_render_is_refused(app):
    """계약을 어긴 뷰는 **만들 때** 걸린다 — 100 ms 뒤가 아니라."""
    from host.gui.app import _checked_views

    class 계약을어긴뷰:
        pass

    with pytest.raises(TypeError, match="뷰 계약"):
        _checked_views(계약을어긴뷰())


def test_catalog_is_loaded_from_the_board(window):
    """🔴 항목이 `$CFG,LIST` 만으로 그려진다.

    개수를 적지 않는다 — 보드에 항목이 늘 때마다 깨지는 시험은 하드코딩을
    금지하면서 스스로 하드코딩하는 셈이다. 화면이 카탈로그의 **모든** 키를
    빠짐없이 그렸는지만 본다.
    """
    form = window._settings._form
    assert form is not None
    assert form.keys()                              # 비어 있으면 실패다
    assert sorted(window._settings._rows) == sorted(form.keys())


def test_din_state_is_seeded_from_stat_on_connect(window):
    """🔴 사용자 확정 — "연결이 끊기면 마지막 상태가 지워지는게 아니라
    바로 읽을 수 있으니까 괜찮아". 그 전제는 창이 `$STAT` 을 실제로
    읽어야 성립한다. `din` 레코드(규격 §7.6)는 상태가 바뀔 때만 오므로,
    이 시험은 **레코드를 하나도 안 거치고**(어떤 `_on_step` 도 부르지
    않은 채) 창이 뜨자마자 세 칸이 `None`("확인 불가")이 아니라 실제
    값(스텁의 기본값 = 꺼짐)으로 채워져 있는지 본다."""
    assert {d.key for d in window._state.dins} == {18, 19, 20}
    for d in window._state.dins:
        assert d.state is False, f"J{d.key} 는 아직 상태를 모르는 채로 남았다"


def test_switching_pages(window):
    window._pages.setCurrentIndex(1)
    assert window._pages.currentIndex() == 1


# 🔴 전원 레일의 계약(명령됨·마지막 알던 값·순서)은 이제 Qt 없이
#    host/tests/test_screen.py 에서 본다. 배치를 바꿔도 그 시험은
#    살아남는다 — 레일을 아래에서 왼쪽으로 옮기면서 위젯 이름이 전부
#    바뀌었는데 계약 시험은 하나도 안 바뀌었다.

# ------------------------------------------------------------ 워커 결과

def test_step_result_updates_mode(window):
    window._on_step(StepResult(mode="CONFIG"))
    assert window._top._mode.text() == "CONFIG"


def test_communication_error_is_visible(window):
    window._on_step(StepResult(pump_error="포트 없음"))
    assert "통신 오류" in window._top._link.text()


def test_rejection_reaches_the_settings_page(window):
    from host.gui.command_queue import Result

    window._settings._rows["tx.period_ms"]._editor.setText("250")
    window._on_step(StepResult(results=[
        Result(tag="set:tx.period_ms", ok=False, reason="INTERLOCK")
    ]))
    note = window._settings._rows["tx.period_ms"]._note
    assert "INTERLOCK" in note.text()
    # 🔴 값은 그대로 남아 있어야 한다.
    assert window._settings._rows["tx.period_ms"]._editor.text() == "250"


def test_apply_queues_commands(window):
    window._settings._rows["tx.period_ms"]._editor.setText("250")
    window._settings._apply.click()
    assert window._queue.pending_tags == {"set:tx.period_ms"}


# ------------------------------------------------------------- 스트림 탭

def test_step_result_feeds_the_stream_view(window):
    """🔴 워커가 걷은 원문 줄이 실제로 스트림 화면까지 닿는지.

    판정 로직(창 기반 rate·필터·일시정지)은 test_stream.py 가 Qt 없이
    이미 본다. 여기서 보는 것은 배선뿐이다.
    """
    line = ('{"schema_ver":3,"seq":1,"t":0,"type":"ain","connector_id":3,'
            '"raw":0,"ma":0,"value":0,"status":0}')
    window._on_step(StepResult(raw_lines=[line]))
    assert line in window._stream_view._console.toPlainText()
