"""메인 윈도우가 보드 없이 실제로 뜨는지 검사한다.

🔴 `python -m host.gui.app --port sim` 이 되어야 한다는 것이 계획서의 완료
   기준이다. 시뮬레이터와 실물이 같은 답을 낸다는 것은 C 와 Python 을
   바이트 단위로 대조해 확인해 두었으므로, 여기서 도는 것은 실물에서도 돈다.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 가 없으면 건너뛴다")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from host.gui.app import MainWindow, make_service  # noqa: E402
from host.gui.qt.dashboard import AIN_COUNT, Dashboard  # noqa: E402
from host.gui.qt.rail import RailRow  # noqa: E402
from host.gui.theme import stylesheet  # noqa: E402
from host.gui.widgets.status_chip import Level, Verification  # noqa: E402
from host.gui.worker_loop import StepResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(stylesheet())
    yield a


@pytest.fixture
def window(app):
    service = make_service("sim", 115200)
    w = MainWindow(service, "sim")
    yield w
    w._worker.stop()


# ------------------------------------------------------------------ 창

def test_window_opens_without_a_board(window):
    assert window.windowTitle().startswith("MarkON Studio")
    assert window._pages.count() == 2


def test_catalog_is_loaded_from_the_board(window):
    """🔴 45개 항목이 `$CFG,LIST` 만으로 그려진다."""
    form = window._settings._form
    assert form is not None
    assert len(form.keys()) == 45
    assert len(window._settings._rows) == 45


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
