"""Qt 위젯이 실제로 만들어지고 값이 흐르는지 검사한다.

🔴 오프스크린 플랫폼으로 돌린다. 디스플레이 없이 위젯을 만들고 신호를
   주고받을 수 있다 — CI 에서도 돌고, 사람이 화면을 보고 있지 않아도 된다.

여기서 보는 것은 **배선**이다. 색이 맞는지, 심각도 판정이 옳은지는
`test_theme.py` · `test_status_chip.py` 가 Qt 없이 본다. 그 경계를 지켜야
화면을 띄우지 않고도 시각 언어를 검증할 수 있다.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6", reason="PyQt6 가 없으면 이 시험은 건너뛴다")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from host.core.config_schema import parse_catalog  # noqa: E402
from host.core.framing import build_command  # noqa: E402
from host.gui.last_known import ChipState, StateHistory, build_chip_state  # noqa: E402
from host.gui.qt.chip import ChipCard, LabeledValue, StatusChip  # noqa: E402
from host.gui.qt.settings_page import RowWidget, SettingsPage  # noqa: E402
from host.gui.settings_form import SettingsForm  # noqa: E402
from host.gui.qt.style import stylesheet  # noqa: E402
from host.gui.widgets.status_chip import Level, Verification  # noqa: E402
from host.tests.fake_board import (  # noqa: E402
    FakeBoard,
    FakeStore,
    fake_store,
)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(stylesheet())
    yield a


# ------------------------------------------------------------ 공유 커서

def _gauge(trace, trace_t):
    from host.gui.qt.gauge import LoopGauge

    g = LoopGauge("J3")
    g.set_reading(trace[-1], level=Level.OK,
                  verification=Verification.VERIFIED,
                  trace=trace, trace_t=trace_t)
    return g


def test_cursor_lands_on_the_sample_nearest_that_moment(app):
    g = _gauge((10.0, 11.0, 12.0), (100, 200, 300))
    g.set_cursor(210)
    assert g.cursor_index() == 1


def test_two_gauges_at_different_rates_share_one_moment(app):
    """🔴 이것이 공유 크로스헤어가 시각을 기준으로 도는 이유다.

    채널마다 수집 주기가 따로다(`ainN.period_ms`). 인덱스로 맞추면 느린
    채널이 빠른 채널의 절반 시각을 가리키게 되고, 화면은 "같은 순간의
    일곱 값" 이라고 말하면서 서로 다른 순간을 보여 준다.
    """
    fast = _gauge((1.0, 2.0, 3.0, 4.0), (100, 200, 300, 400))
    slow = _gauge((1.0, 2.0), (100, 300))

    fast.set_cursor(310)
    slow.set_cursor(310)

    assert fast.cursor_index() == 2      # t=300
    assert slow.cursor_index() == 1      # t=300


def test_dropping_the_cursor_returns_to_now(app):
    g = _gauge((10.0, 11.0), (100, 200))
    g.set_cursor(110)
    g.set_cursor(None)
    assert g.cursor_index() is None


# ------------------------------------------------------------ 전원 레일

class _Clock:
    """시험이 시간을 쥔다. 진짜로 0.7초를 기다리지 않는다."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_a_brief_press_does_not_command_the_rail(app):
    """🔴 24 V 는 잘못 누르면 곤란한 출력이다.

    산업 HMI 의 답은 확인 대화상자가 아니라 hold-to-run 이다 — 손을 대고
    있는 동안만 동작하고 떼면 멈춘다. 의도가 **계속** 확인된다는 것이
    한 번의 확인 클릭과 다른 점이다.
    """
    from host.gui.qt.rail import RailRow

    clock = _Clock()
    row = RailRow("24V", "pwr.24v", hold_s=0.7, monotonic=clock)
    row.set_commanded(False)
    fired = []
    row.toggled.connect(lambda key, on: fired.append((key, on)))

    row.begin_hold()
    clock.advance(0.3)
    row.end_hold()

    assert fired == []


def test_holding_long_enough_commands_the_rail(app):
    from host.gui.qt.rail import RailRow

    clock = _Clock()
    row = RailRow("24V", "pwr.24v", hold_s=0.7, monotonic=clock)
    row.set_commanded(False)
    fired = []
    row.toggled.connect(lambda key, on: fired.append((key, on)))

    row.begin_hold()
    clock.advance(0.8)
    row.end_hold()

    assert fired == [("pwr.24v", True)]


def test_holding_an_on_rail_turns_it_off(app):
    """같은 동작이 반대 방향으로도 돈다 — 끄는 것도 누르고 있어야 한다."""
    from host.gui.qt.rail import RailRow

    clock = _Clock()
    row = RailRow("24V", "pwr.24v", hold_s=0.7, monotonic=clock)
    row.set_commanded(True)
    fired = []
    row.toggled.connect(lambda key, on: fired.append((key, on)))

    row.begin_hold()
    clock.advance(0.8)
    row.end_hold()

    assert fired == [("pwr.24v", False)]


def test_an_unknown_rail_cannot_be_commanded(app):
    """🔴 연결이 끊긴 동안에는 명령을 보내지 않는다.

    `commanded is None` 은 "모른다" 다. 모르는 상태에서 토글하면 무엇의
    반대를 보내는지 알 수 없다 — 24 V 를 켜려다 끄게 될 수 있다.
    """
    from host.gui.qt.rail import RailRow

    clock = _Clock()
    row = RailRow("24V", "pwr.24v", hold_s=0.7, monotonic=clock)
    row.set_commanded(None)
    fired = []
    row.toggled.connect(lambda key, on: fired.append((key, on)))

    row.begin_hold()
    clock.advance(0.8)
    row.end_hold()

    assert fired == []


def test_setting_a_value_from_outside_moves_the_widget_too(app):
    """🔴 대시보드에서 레일을 켜면 설정 화면도 그렇게 보여야 한다.

    `_rail_values()` 가 설정 폼을 유일한 출처로 읽는다(screen.py). 폼만
    고치고 위젯을 두면 두 화면이 서로 다른 말을 하게 되고, 그때 사용자는
    어느 쪽이 보드의 상태인지 알 수 없다.
    """
    page = SettingsPage()
    page.set_form(_form_from(fake_store()))

    page.set_value("pwr.24v", "true")

    assert page.form.row("pwr.24v").value == "true"
    assert page.form.pending_changes() == [("pwr.24v", "true")]


# ------------------------------------------------------------------ 구획선

def test_hairline_is_exactly_one_pixel(app):
    """🔴 `QFrame.HLine` 을 쓰지 않는 이유.

    그것은 새김 효과가 있는 두 줄짜리 테두리라 **두꺼운 띠**로 렌더링된다.
    어두운 면에서는 제목보다 눈에 띄고(qt/rail.py 가 이미 겪고 직접 칠하는
    쪽으로 바꿨다), 밝은 면에서도 구획선이 아니라 입력칸처럼 보인다.

    한 곳에서 만들어 네 화면이 같은 것을 쓴다.
    """
    from host.gui.qt.parts import hairline

    line = hairline()
    assert line.minimumHeight() == 1
    assert line.maximumHeight() == 1


def test_hairline_takes_the_colour_it_is_given(app):
    """어두운 면과 밝은 면이 같은 부품을 쓰되 색만 갈아 낀다."""
    from host.gui.qt.parts import hairline
    from host.gui.theme import Color

    assert Color.SHELL_LINE in hairline(Color.SHELL_LINE).styleSheet()
    assert Color.LINE in hairline().styleSheet()


def _form_from(store) -> SettingsForm:
    board = FakeBoard(store)
    board.feed(build_command("HB"))
    lines = [ln for ln in board.feed(build_command("CFG", "LIST"))
             if ln.startswith("{")]
    return SettingsForm(parse_catalog(lines))


@pytest.fixture
def form():
    return _form_from(fake_store())


@pytest.fixture
def readonly_form(interlocked_items):
    """읽기 전용 항목이 있는 카탈로그로 만든 폼.

    🔴 제품 카탈로그에는 지금 읽기 전용 항목이 없다 — 5V 를 끌 수 있게
       하면서(사용자 확정 2026-08-14) 마지막 하나가 사라졌다. 읽기 전용을
       어떻게 그리는지는 규격 §7.3 이 정한 계약이므로, 쓰는 항목이 없어도
       계속 확인한다.
    """
    return _form_from(FakeStore(interlocked_items()))


# ------------------------------------------------------------------ 칩

def test_chip_builds(app):
    chip = StatusChip()
    chip.apply(ChipState("k", "24V", Level.OK, Verification.COMMANDED))
    assert chip is not None


def test_chip_card_shows_last_known_when_contact_is_lost(app):
    """🔴 확인이 끊겼을 때 마지막으로 알던 것이 화면에 뜨는지.

    이것이 없으면 "한 번도 모름" 과 "고장이었는데 연락이 끊김" 이 같은
    회색으로 보인다.
    """
    h = StateHistory()
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    state = build_chip_state(h, "ain0", "J3", Level.IDLE,
                             Verification.UNKNOWN, now_s=12.0)
    card = ChipCard()
    card.apply(state)
    assert card._last.isVisible() or card._last.text()
    assert "마지막" in card._last.text()
    assert state.needs_attention is True


def test_chip_card_hides_last_known_when_verified(app):
    h = StateHistory()
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    state = build_chip_state(h, "ain0", "J3", Level.OK,
                             Verification.VERIFIED, now_s=12.0)
    card = ChipCard()
    card.apply(state)
    assert card._last.text() == ""


def test_labeled_value(app):
    lv = LabeledValue("전류")
    lv.set_value("12.0041 mA")
    assert lv._value.text() == "12.0041 mA"


# ------------------------------------------------------------ 설정 화면

def test_every_cell_keeps_the_contract(app, form):
    """🔴 설정 칸 셋이 같은 계약을 지키는지 (qt/cell.py).

    예전에는 어디에도 안 적혀 있었고, 그래서 `RangeFields` 와
    `FieldMaskCard` 에 `note_text` 가 없었다. 그것을 모으는 쪽이
    `getattr(w, "note_text", "")` 로 덮고 있어서 아무도 몰랐다 — 보드가
    영점에 사유를 붙이는 순간 그 문구는 화면 어디에도 안 뜬다.
    """
    from host.gui.qt.cell import Cell

    page = SettingsPage()
    page.set_form(form)
    assert page._rows
    kinds = {type(w).__name__ for w in page._rows.values()}
    assert len(kinds) >= 3, f"칸 종류가 셋은 나와야 한다: {kinds}"
    for key, w in page._rows.items():
        assert isinstance(w, Cell), f"{key} 의 {type(w).__name__} 이 계약을 어긴다"


def test_a_range_cell_reports_both_reasons(app):
    """🔴 한 칸이 두 항목을 맡으므로 사유도 둘을 합쳐 내야 한다.

    한쪽만 내면 나머지가 사라지는데, 화면에는 칸이 하나뿐이라 그 손실이
    눈에 띄지 않는다.
    """
    from host.core.config_schema import ConfigItem
    from host.gui.qt.settings_page import RangeFields
    from host.gui.settings_form import build_row

    zero = build_row(ConfigItem(key="ain0.zero", group="ain", vtype="f32",
                                default=4.0, current=4.0, note="영점 사유"))
    scale = build_row(ConfigItem(key="ain0.scale", group="ain", vtype="f32",
                                 default=1.0, current=1.0, note="스케일 사유"))
    w = RangeFields(zero, scale)
    assert "영점 사유" in w.note_text
    assert "스케일 사유" in w.note_text


def test_settings_page_draws_every_catalog_item(app, form):
    """🔴 카탈로그의 모든 항목이 그려지는지. 개수는 적지 않는다 —
    보드가 항목을 늘릴 때마다 깨지는 숫자를 시험에 박아 두지 않는다."""
    page = SettingsPage()
    page.set_form(form)
    assert form.keys()
    assert sorted(page._rows) == sorted(form.keys())


def test_readonly_row_is_disabled_and_explains_why(app, readonly_form):
    page = SettingsPage()
    page.set_form(readonly_form)
    readonly = [k for k in readonly_form.keys()
                if not readonly_form.row(k).editable]
    assert readonly
    for key in readonly:
        w = page._rows[key]
        assert not w._editor.isEnabled(), f"{key} 가 편집 가능하다"
        assert w.toolTip(), f"{key} 에 이유 툴팁이 없다"


def test_editable_row_still_shows_the_boards_warning(app, form):
    """🔴 편집 가능해도 보드가 붙인 사유는 화면에 뜬다.

    `pwr.5v` 를 끌 수 있게 하면서(사용자 확정 2026-08-14) "끄면 쿨링 팬·
    아날로그 수집·WS2812 가 함께 멈춘다" 는 경고가 인터록을 푼 대신 남긴
    유일한 안전장치가 됐다. 그런데 예전 코드는 편집 가능하다는 이유로 그
    문구를 버렸다 — 안전장치가 화면에 없었던 것이다.
    """
    page = SettingsPage()
    page.set_form(form)
    w = page._rows["pwr.5v"]
    assert w._editor.isEnabled(), "5V 는 이제 바꿀 수 있다"
    assert "팬" in w.toolTip(), f"경고가 안 뜬다: {w.toolTip()!r}"


def test_editing_flows_into_the_form(app, form):
    page = SettingsPage()
    page.set_form(form)
    page._rows["tx.period_ms"]._editor.setText("250")
    assert form.row("tx.period_ms").value == "250"
    assert form.is_dirty("tx.period_ms")
    assert page._apply.isEnabled()


def test_bad_value_blocks_apply(app, form):
    page = SettingsPage()
    page.set_form(form)
    page._rows["dev.id"]._editor.setText("a,b")      # 구분자가 들어갔다
    assert not page._apply.isEnabled()
    assert "고칠 것" in page._status.text()


def test_apply_emits_only_good_changes(app, form):
    page = SettingsPage()
    page.set_form(form)
    got: list = []
    page.apply_requested.connect(got.append)

    page._rows["tx.period_ms"]._editor.setText("250")
    page._apply.click()
    assert got and dict(got[0]) == {"tx.period_ms": "250"}


def test_rejection_keeps_the_typed_value(app, form):
    """🔴 거부돼도 사용자가 친 값을 지우지 않는다."""
    page = SettingsPage()
    page.set_form(form)
    page._rows["tx.period_ms"]._editor.setText("250")
    page.on_rejected("tx.period_ms", "INTERLOCK")
    assert page._rows["tx.period_ms"]._editor.text() == "250"
    assert "INTERLOCK" in page._rows["tx.period_ms"]._note.text()


def test_number_editor_is_not_a_spinbox(app, form):
    """🔴 QSpinBox 는 범위 밖 값을 스스로 잘라 넣는다.

    그러면 사용자가 친 값과 보내는 값이 달라지고 아무도 그것을 모른다.
    규격이 잘라 담지 말라고 정한 것과 같은 이유다.
    """
    from PyQt6.QtWidgets import QLineEdit, QSpinBox

    page = SettingsPage()
    page.set_form(form)
    editor = page._rows["tx.period_ms"]._editor
    assert isinstance(editor, QLineEdit)
    assert not isinstance(editor, QSpinBox)

    editor.setText("999999")                     # 범위 밖
    assert editor.text() == "999999"             # 잘리지 않았다
    assert form.validate("tx.period_ms")         # 대신 오류로 알린다


def test_toggle_and_choice_widgets(app, form):
    from PyQt6.QtWidgets import QCheckBox, QComboBox

    page = SettingsPage()
    page.set_form(form)
    assert isinstance(page._rows["pwr.24v"]._editor, QCheckBox)
    assert isinstance(page._rows["adc.drate"]._editor, QComboBox)


def test_revert_restores_the_screen(app, form):
    page = SettingsPage()
    page.set_form(form)
    original = form.row("tx.period_ms").value
    page._rows["tx.period_ms"]._editor.setText("250")
    page._revert.click()
    assert page._rows["tx.period_ms"]._editor.text() == original
    assert not page._apply.isEnabled()


# ------------------------------------------------------ 적용 vs 저장

def test_applying_marks_unsaved(app, form):
    """🔴 적용은 보드의 RAM 이고 저장은 Flash 다.

    적용만 하고 전원을 끄면 사라진다. 화면이 둘을 같은 것으로 보이면
    사용자는 설정이 남은 줄 알고 보드를 떼어 간다.
    """
    page = SettingsPage()
    page.set_form(form)
    assert page.has_unsaved is False
    assert not page._save.isEnabled()

    page._rows["tx.period_ms"]._editor.setText("250")
    page._apply.click()
    page.on_accepted("tx.period_ms")

    assert page.has_unsaved is True
    assert page._save.isEnabled()
    assert "저장되지 않았다" in page._status.text()


def test_saving_clears_the_warning(app, form):
    page = SettingsPage()
    page.set_form(form)
    page._rows["tx.period_ms"]._editor.setText("250")
    page.on_accepted("tx.period_ms")

    got = []
    page.save_requested.connect(lambda: got.append(True))
    page._save.click()
    assert got

    page.mark_saved()
    assert page.has_unsaved is False
    assert not page._save.isEnabled()
    assert "저장되지 않았다" not in page._status.text()


def test_reverting_does_not_clear_unsaved(app, form):
    """🔴 되돌리기는 **화면의 편집**을 되돌린다.

    이미 보드에 보낸 것은 되돌리지 않으므로 저장 여부도 그대로다. 여기서
    지우면 사용자가 저장하지 않은 채 화면을 떠난다.
    """
    page = SettingsPage()
    page.set_form(form)
    page._rows["tx.period_ms"]._editor.setText("250")
    page.on_accepted("tx.period_ms")
    page._rows["dev.id"]._editor.setText("x")
    page._revert.click()

    assert page.has_unsaved is True
    assert page._save.isEnabled()


def test_reset_asks_first(app, form):
    """🔴 되돌릴 수 없는 동작이라 먼저 묻는다.

    채널 영점·스케일처럼 손으로 맞춘 값도 사라지고, 다시 만들려면 센서를
    다시 재야 한다.
    """
    page = SettingsPage()
    page.set_form(form)
    got = []
    page.reset_requested.connect(lambda: got.append(True))

    asked = []
    page._confirm = lambda text: (asked.append(text), False)[1]
    page._reset.click()
    assert asked, "묻지 않았다"
    assert "기본값" in asked[0]
    assert not got, "거절했는데 보냈다"

    page._confirm = lambda text: True
    page._reset.click()
    assert got, "수락했는데 안 보냈다"


def test_save_and_reset_are_separate_commands(app):
    """저장과 초기화가 값 설정과 같은 태그로 합쳐지면 안 된다."""
    from host.gui.command_queue import CommandQueue

    q = CommandQueue()
    q.submit("CFG", "SET", "tx.period_ms", "250", tag="set:tx.period_ms")
    q.submit("CFG", "SAVE", tag="cfg:save")
    q.submit("CFG", "RESET", tag="cfg:reset")
    sent = q.drain_pending()
    assert len(sent) == 3
    assert {c.args[0] for c in sent} == {"SET", "SAVE", "RESET"}


# ------------------------------------------------------------ 루프 게이지

def test_gauge_never_draws_a_bar_for_a_fault(app):
    """🔴 고장을 정상처럼 그릴 수 없어야 한다.

    `read_loop` 이 단선·비유한값에 `fraction=None`, `bar_fraction=0` 을
    주므로, 위젯이 순진하게 그려도 바가 생기지 않는다. 그 구조를 여기서
    못박는다 — 나중에 누가 `ma` 를 직접 그리도록 바꾸면 실패한다.
    """
    from host.gui.qt.gauge import LoopGauge
    from host.gui.widgets.loop_gauge import read_loop

    for ma in (0.0, 3.99, float("nan"), float("-inf")):
        r = read_loop(ma)
        assert r.fraction is None, ma
        assert r.bar_fraction == 0.0, ma

    g = LoopGauge("J3")
    g.set_reading(0.2, level=Level.FAULT, verification=Verification.VERIFIED)
    g.resize(140, 170)
    g.grab()                     # 그리다 죽지 않는다


def test_gauge_shows_words_not_numbers_when_broken(app):
    """단선이면 숫자 대신 말로 쓴다 — 숫자를 보여주면 측정값으로 읽힌다."""
    from host.gui.widgets.loop_gauge import read_loop

    assert read_loop(0.3).label == "루프 단선"
    assert read_loop(25.0).label == "과입력"
    assert read_loop(float("nan")).label == "값 없음"
    assert read_loop(12.0).label == "12.00 mA"


def test_gauge_over_range_fills_but_has_no_fraction(app):
    """과입력은 바를 채우되 `fraction` 은 비운다.

    정상 만재(20.00 mA)와 데이터 형태가 같으면 안 된다.
    """
    from host.gui.widgets.loop_gauge import read_loop

    over = read_loop(22.0)
    full = read_loop(20.0)
    assert over.bar_fraction == 1.0 and over.fraction is None
    assert full.bar_fraction == 1.0 and full.fraction == 1.0


def test_gauge_with_no_reading_draws_nothing(app):
    from host.gui.qt.gauge import LoopGauge

    g = LoopGauge("J3")
    g.resize(140, 170)
    g.grab()                     # ma 가 None 이어도 죽지 않는다


# ------------------------------------------------------------ 대시보드

def test_dashboard_has_one_gauge_per_connector(app):
    """보드 위 J3~J9 순서 그대로 늘어놓는다.

    사용자가 보드를 보면서 화면을 보기 때문이다.
    """
    from host.gui.qt.dashboard import AIN_COUNT, CONNECTOR_OFFSET, Dashboard

    d = Dashboard()
    assert len(d._cards) == AIN_COUNT
    assert d._cards[0].gauge._connector == f"J{CONNECTOR_OFFSET}"
    assert d._cards[-1].gauge._connector == f"J{CONNECTOR_OFFSET + AIN_COUNT - 1}"


def test_dashboard_renders_screen_state(app):
    """뷰는 `ScreenState` 만 받아 그린다 — 뷰 계약(qt/view.py).

    🔴 채널 장애 격리·범위 밖 커넥터 같은 **판정**은 이제 Qt 없이
       host/gui/screen.py 에서 시험한다(test_screen.py). 여기서 보는 것은
       배선뿐이다 — 상태가 위젯에 실제로 닿는가.
    """
    from host.gui.qt.dashboard import Dashboard
    from host.gui.screen import ChannelState, ScreenState

    d = Dashboard()
    d.render(ScreenState(
        reachable=True,
        channels=(
            ChannelState(0, "J3", ma=12.0, level=Level.OK,
                         verification=Verification.VERIFIED),
            ChannelState(3, "J6", ma=0.1, level=Level.FAULT,
                         verification=Verification.VERIFIED),
            # 범위 밖 — 조용히 무시돼야 한다
            ChannelState(99, "J102", ma=8.0),
        ),
    ))
    assert d._cards[0].gauge._ma == 12.0
    assert d._cards[3].gauge._ma == 0.1
    assert d._cards[1].gauge._ma is None


def test_dashboard_has_three_digital_input_pills(app):
    """J18~J20 은 카탈로그와 무관하게 늘 세 자리다 (규격 §7.6)."""
    from host.gui.qt.dashboard import Dashboard

    d = Dashboard()
    assert len(d._din_pills) == 3
    assert set(d._din_pills) == {18, 19, 20}


def test_dashboard_renders_din_state(app):
    from host.gui.qt.dashboard import Dashboard
    from host.gui.screen import DinState, ScreenState

    d = Dashboard()
    d.render(ScreenState(
        reachable=True,
        dins=(
            DinState(connector="J18", key=18, state=True, changed_at=5000),
            DinState(connector="J19", key=19, state=False, changed_at=None),
            DinState(connector="J20", key=20, state=None, changed_at=None),
        ),
    ))
    assert d._din_pills[18]._state.text() != ""
    # 렌더가 예외 없이 세 자리 모두를 훑었는지가 이 시험의 요점이다 —
    # 문구 자체의 옳음은 host/tests/test_dins.py 가 Qt 없이 본다.


def test_stylesheet_applies(app):
    assert "background" in stylesheet()
    assert app.styleSheet()


# ------------------------------------------------------------ 진단 화면

def _diag_stat(**over):
    base = {
        "type": "stat", "mode": "CONFIG", "ctl_mode": "ACTIVE",
        "time_source": "device_clock", "time_quality": 0, "uptime_ms": 1000,
        "clock": {"src": "hsi", "sysclk_hz": 64_000_000},
        "gnss": {"pps_age_ms": None, "pps_raw_age_ms": None,
                 "pps_raw_count": 0, "pps_unpaired_reason": None,
                 "sats": None, "init_sent": False, "init_exhausted": False,
                 "sentence_seen": False},
        "rails": {"v24": False, "v14v9": False, "v5": True},
        "din": [{"connector_id": c, "state": 0} for c in (18, 19, 20)],
        "queues": [{"ch": 0, "depth": 0, "peak": 0, "drops": 0}],
        "lcd": {"epoch": 0, "reinit": 0, "redraw": 0, "verify_ok": 0,
                "verify_fail": 0, "rejected": 0, "readback": None},
    }
    base.update(over)
    return base


def test_diagnostics_page_draws_every_reading(app):
    """🔴 판정은 `host/gui/diagnostics.py` 가 Qt 없이 한다. 여기서 보는 것은
    **배선** 이다 — 모든 항목이 실제로 위젯 한 줄씩을 얻는가."""
    from host.gui.diagnostics import build_diagnostics
    from host.gui.qt.diagnostics_page import DiagnosticsPage

    state = build_diagnostics(_diag_stat(), age_s=0.1)
    page = DiagnosticsPage()
    page.render(state)

    drawn = {k for card in page._cards.values() for k in card._rows}
    assert drawn == {r.key for r in state.readings}
    assert page._headline.text() == state.headline


def test_diagnostics_page_survives_a_changing_queue_list(app):
    """채널을 켜고 끄면 큐 항목 수가 바뀐다 — 그때 줄을 다시 세운다."""
    from host.gui.diagnostics import build_diagnostics
    from host.gui.qt.diagnostics_page import DiagnosticsPage

    page = DiagnosticsPage()
    page.render(build_diagnostics(_diag_stat()))
    page.render(build_diagnostics(_diag_stat(queues=[
        {"ch": 0, "depth": 0, "peak": 0, "drops": 0},
        {"ch": 3, "depth": 1, "peak": 2, "drops": 0},
    ])))
    assert "queue.3" in page._cards["queues"]._rows


def test_diagnostics_page_says_when_it_knows_nothing(app):
    """보드가 없으면 항목 자리는 남기되 전부 "모름" 이다."""
    from host.gui.diagnostics import UNKNOWN_TEXT, build_diagnostics
    from host.gui.qt.diagnostics_page import DiagnosticsPage

    page = DiagnosticsPage()
    page.render(build_diagnostics(None))
    row = page._cards["clock"]._rows["clock.src"]
    assert row._value.text() == UNKNOWN_TEXT


# ---- 링크 속도 (규격 §4.2) ---------------------------------------------------
#
# 🔴 이 항목만 `apply_requested` 를 타지 않는다. 명령 하나가 아니라 절차이고,
#    사람이 "그래도 하겠다" 고 말해야 나간다.


def test_link_baud_is_not_sent_with_the_other_settings(app, form):
    """🔴 같이 보내면 순서가 깨진다 — 링크가 바뀌는 중에 뒤따라 나가는
    `$CFG,SET` 은 옛 속도로 허공에 나간다."""
    page = SettingsPage()
    page.set_form(form)
    page._confirm = lambda _text: True

    applied: list = []
    bauds: list = []
    page.apply_requested.connect(applied.append)
    page.baud_change_requested.connect(bauds.append)

    page._rows["tx.period_ms"]._editor.setText("250")
    page.set_value("link.baud", "1500000")
    page._apply.click()

    assert bauds == [1500000]
    assert applied and dict(applied[0]) == {"tx.period_ms": "250"}
    assert "link.baud" not in dict(applied[0])


def test_link_baud_asks_before_breaking_the_link(app, form):
    """🔴 확인 대화상자가 이 항목의 안전장치 절반이다.

    나머지 절반은 보드의 10초 되돌림이고, 그것은 이미 끊긴 뒤에 작동한다.
    """
    page = SettingsPage()
    page.set_form(form)
    asked: list[str] = []
    page._confirm = lambda text: (asked.append(text), False)[1]

    bauds: list = []
    page.baud_change_requested.connect(bauds.append)
    page.set_value("link.baud", "2000000")
    page._apply.click()

    assert asked, "묻지 않고 보냈다"
    assert "2,000,000" in asked[0]
    assert "확인된 적이 없다" in asked[0]
    assert bauds == [], "거절했는데 보냈다"


def test_declining_puts_the_old_value_back(app, form):
    """🔴 안 하기로 한 값이 화면에 남으면, 다음 `적용` 에 묻지도 않고
    딸려 나간다 — 사용자는 이미 바뀐 줄 안다."""
    page = SettingsPage()
    page.set_form(form)
    page._confirm = lambda _text: False

    page.set_value("link.baud", "2000000")
    page._apply.click()

    assert form.row("link.baud").value == "921600"
    assert not form.is_dirty("link.baud")


def test_a_failed_change_puts_the_real_speed_back_and_says_why(app, form):
    """🔴 실패했는데 화면에 새 값이 남아 있으면 사용자는 바뀐 줄 알고
    다음 `적용` 을 누른다 — 그리고 그것은 아무 일도 안 한다."""
    page = SettingsPage()
    page.set_form(form)
    page.set_value("link.baud", "2000000")

    page.on_baud_changed(921600, "새 속도로는 보드와 말이 되지 않았다", True)

    assert form.row("link.baud").value == "921600"
    assert "말이 되지 않았다" in page._status.text()
    assert not page.has_unsaved, "실패했으면 저장할 것도 없다"


def test_a_successful_change_marks_the_settings_unsaved(app, form):
    """🔴 확정과 저장은 다른 일이다 (규격 §4.2.2 규칙 3)."""
    page = SettingsPage()
    page.set_form(form)
    page.on_baud_changed(1500000, "링크 속도 1,500,000 bps 로 확정됐다", False)

    assert form.row("link.baud").value == "1500000"
    assert page.has_unsaved


# ------------------------------------------------------- 링크 사용량 요약

def _usage_cards(page):
    from host.gui.qt.link_usage_card import LinkUsageCard

    return page.findChildren(LinkUsageCard)


def test_the_usage_summary_sits_where_the_channels_are_toggled(app, form):
    """🔴 켜는 자리에서 보여야 한다.

    예산은 원래 필드 마스크 카드 안에만 있었다. 그런데 사용자가 켜고 끄는
    것은 **채널·포트 표**다 — 다른 탭에 있으면 그때그때 못 보고, 못 보면
    여유가 없어진 것을 유실이 난 뒤에야 안다.
    """
    page = SettingsPage()
    page.set_form(form)
    assert len(_usage_cards(page)) >= 2, "아날로그·I2C 탭 둘 다에 있어야 한다"


def test_turning_on_an_i2c_port_moves_the_summary_immediately(app, form):
    """🔴 이것이 안 되던 것이다 — I2C 를 켜도 화면의 숫자가 안 움직였다."""
    from host.gui.link_usage import compute_usage

    page = SettingsPage()
    page.set_form(form)
    before = compute_usage(form, 921600).bytes_per_s

    page.set_value("i2c10.kind", "2")           # 온습도 — 양 둘
    page.set_value("i2c10.enabled", "true")

    after = compute_usage(form, 921600)
    assert after.bytes_per_s > before
    # 카드가 실제로 그 수를 그리고 있다.
    text = " ".join(c._message.text() for c in _usage_cards(page))
    # 카드는 소수 1자리로 쓴다(_verdict 의 pct 서식) — 같은 자릿수로 대조.
    assert f"{after.ratio * 100:.1f}%" in text


def test_the_two_places_show_the_same_number(app, form):
    """🔴 요약과 필드 마스크 카드가 다른 숫자를 말하면 안 된다."""
    from host.gui.field_budget import format_bytes_per_s
    from host.gui.link_usage import compute_usage, record_budget

    page = SettingsPage()
    page.set_form(form)
    card = page._rows["tx.fields_ain"]
    budget = record_budget(form, "ain", 921600)

    assert f"{budget.line_bytes} B" in card._budget.text()
    assert format_bytes_per_s(budget.bytes_per_s) in card._budget.text()
    ain_row = next(r for r in compute_usage(form, 921600).rows
                   if r.kind == "ain")
    assert format_bytes_per_s(ain_row.bytes_per_s) in card._budget.text()


def test_the_mask_card_follows_the_link_speed(app, form):
    """🔴 baud 가 고정이 아니다 — `link.baud` 를 올리면 카드의 % 도 준다."""
    page = SettingsPage()
    page.set_form(form)
    card = page._rows["tx.fields_ain"]

    page.set_value("link.baud", "115200")
    slow = card._budget.text()
    page.set_value("link.baud", "2000000")
    fast = card._budget.text()

    assert slow != fast


# ------------------------------------------------------------------ 그룹 탭
#
# 🔴 회귀 시험. 그룹이 열한 개로 늘자 탭이 한 줄 `QHBoxLayout` 에 다 안 들어가
#    뒤쪽(`i2c`·`ain`)이 눌러 찌그러졌고, 사용자는 "아날로그·I2C 설정이 없다"
#    고 봤다. 없어진 것이 아니라 닿을 수가 없었다.

def _form_with_groups(names) -> SettingsForm:
    """그룹 이름만 다른 카탈로그를 만든다 — 개수를 마음대로 늘려 보기 위해."""
    from host.core.config_schema import ConfigItem, ConfigSchema

    items = {}
    for name in names:
        key = f"{name}.value"
        items[key] = ConfigItem(key=key, group=name, vtype="u16",
                                default=1, current=1, minimum=0, maximum=100,
                                label=f"{name} 값")
    return SettingsForm(ConfigSchema(items=items, _group_order=list(names)))


def _laid_out(app, page, width=1130, height=700):
    """실제 창 크기 안에 넣고 배치를 확정시킨다 (오프스크린)."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget

    box = QWidget()
    box.setFixedSize(width, height)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(page)
    box.show()
    app.processEvents()
    return box


def test_twenty_groups_all_get_a_tab_that_can_be_opened(app):
    """🔴 지금 열한 개에서만 되는 것으로는 같은 일이 또 난다."""
    names = [f"grp{i:02d}" for i in range(20)]
    page = SettingsPage()
    page.set_form(_form_with_groups(names))

    assert len(page._tab_buttons) == len(names)
    for i in range(len(names)):
        page.select_tab(i)
        assert page._pages.currentIndex() == i


def test_a_tab_name_is_never_squeezed_until_it_is_unreadable(app):
    """🔴 되돌림 검사 — 탭을 한 줄 `QHBoxLayout` 으로 되돌리면 깨진다.

    한 줄에 밀어 넣으면 Qt 가 버튼을 최소 폭(약 50 px)까지 눌러 이름표를
    `아날...` 로 잘라 버린다. 목록에는 있는데 무엇인지 읽을 수 없으니,
    사용자에게는 없는 것과 같다.
    """
    page = SettingsPage()
    page.set_form(_form_with_groups([f"그룹이름{i:02d}" for i in range(20)]))
    box = _laid_out(app, page)

    squeezed = [b.text() for b in page._tab_buttons
                if b.width() < b.sizeHint().width()]
    assert not squeezed, f"이름표가 잘린 탭: {squeezed}"
    assert box.width() == 1130


def test_more_groups_do_not_make_the_screen_demand_more_width(app):
    """🔴 이것이 회귀의 정체다 — 탭 줄의 최소 폭이 그룹 수에 비례해 자라면
    설정 화면 전체가 창보다 넓어지고, 넘친 만큼이 잘려 나간다."""
    few = SettingsPage()
    few.set_form(_form_with_groups([f"grp{i:02d}" for i in range(3)]))
    many = SettingsPage()
    many.set_form(_form_with_groups([f"grp{i:02d}" for i in range(20)]))

    assert many.minimumSizeHint().width() <= few.minimumSizeHint().width()


# ------------------------------------------------------------- 탭 순서

def test_frequently_used_groups_come_first(app):
    """🔴 카탈로그 순서를 그대로 따르면 가장 많이 쓰는 `ain`·`i2c` 가 맨
    끝이다. 규칙은 **알려진 것을 앞으로, 모르는 것은 원래 순서대로 뒤에** 다.
    """
    from host.gui.qt.settings_page import TAB_PRIORITY, ordered_groups

    # 🔴 비면 아래가 조용히 통과한다 — 우선순위를 지우는 것도 회귀다.
    assert TAB_PRIORITY, "앞자리를 받는 그룹이 하나도 없다"
    names = ["zeta", "alpha"] + list(TAB_PRIORITY)
    groups = _form_with_groups(names).groups
    got = [g.name for g in ordered_groups(groups)]

    assert got[:len(TAB_PRIORITY)] == list(TAB_PRIORITY)
    assert got[len(TAB_PRIORITY):] == ["zeta", "alpha"]


def test_groups_nobody_knows_keep_the_catalog_order(app):
    """🔴 보드가 그룹을 늘리거나 이름을 바꿔도 화면이 순서를 지어내지
    않는다 — 모르는 그룹은 보드가 보낸 차례 그대로다."""
    from host.gui.qt.settings_page import ordered_groups

    names = ["quux", "bravo", "zulu", "alfa"]
    got = [g.name for g in ordered_groups(_form_with_groups(names).groups)]
    assert got == names


def test_the_tab_strip_follows_that_order(app, form):
    """실제 카탈로그에서도 화면이 그 순서로 그려지는가 — 함수만 맞고 화면이
    안 따르면 아무 소용이 없다."""
    from host.gui.qt.settings_page import TAB_PRIORITY, ordered_groups
    from host.gui.settings_form import group_label

    page = SettingsPage()
    page.set_form(form)
    order = [g.name for g in ordered_groups(form.groups)]

    assert [b.text() for b in page._tab_buttons] == [group_label(n)
                                                     for n in order]
    assert order[0] in TAB_PRIORITY


def test_dashboard_shows_the_gnss_position(app):
    """🔴 사용자 지적(2026-08-20): "모든 센서 데이터가 다 보여야 한다니까?"

    상태만 만들고 화면에 안 꽂으면 사용자가 보는 것은 그대로다 — 이
    시험이 보는 것은 배선이다. 좌표 문구 자체의 옳음은 Qt 없이
    test_screen.py 가 본다.
    """
    from host.gui.qt.dashboard import Dashboard
    from host.gui.screen import GnssState, ScreenState

    d = Dashboard()
    d.render(ScreenState(
        reachable=True,
        gnss=GnssState(seen=True, lat=37.3190694, lon=127.3405907,
                       fix_t=1787193075000, t=1787193075120,
                       alt=100.852, sats=20, fix=1,
                       time_source="gnss_pps"),
    ))
    # 🔴 소수 7자리가 화면까지 살아 있어야 한다(규격 §7.8.2) — 4자리로
    #    줄면 11 m 가 사라지는데 화면에는 아무 이상이 없어 보인다.
    assert d._gnss._pos.text() == "37.3190694, 127.3405907"
    assert "20" in d._gnss._quality.text()
    # `t - fix_t` = 문장이 얼마나 늦게 도착했나 (규격 §7.8.3).
    assert "120 ms" in d._gnss._detail.text()


def test_dashboard_gnss_panel_stays_when_gnss_is_off(app):
    """🔴 설계 원칙 3 — 안 꽂힌 것은 정상 상태다. 자리가 사라지면 사용자는
    이 장비에 GNSS 가 있다는 것조차 화면에서 알 수 없다."""
    from host.gui.qt.dashboard import Dashboard
    from host.gui.screen import ScreenState

    d = Dashboard()
    d.render(ScreenState(reachable=True))
    assert d._gnss._pos.text() == "위치 없음"
    assert "꺼짐" in d._gnss._quality.text()


def test_i2c_field_card_groups_sensor_specific_bits(app, form):
    """🔴 [2026-08-22 사용자 요청] I2C 필드 카드는 센서 전용 비트(적외의
    주변 온도, 온습도의 이슬점)를 그 센서 이름 소구획으로 나눠 보여준다.
    마스크는 여전히 하나다 — 표시만 나눈다(FIELD_SUBSECTIONS 주석)."""
    from host.gui.qt.field_mask import FieldMaskCard

    from host.gui.field_budget import compute_budget
    budget = compute_budget({"schema_ver": 3}, channels_enabled=1,
                            period_ms=100, baud=921600)
    card = FieldMaskCard(form.row("tx.fields_i2c"), form.fields,
                         lambda: ({"schema_ver": 3}, budget),
                         record_kind="i2c")
    titles = [label.text() for label, _ in card._section_rows]
    assert "적외 온도" in titles
    assert "온습도" in titles
    # 구획으로 갔어도 마스크 상자에는 들어 있다 — 값 계산은 하나다.
    names = {box.toolTip().split()[0] for box in card._boxes.values()}
    assert "temp_ambient" in names and "dewpoint" in names
