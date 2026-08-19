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
from tools.simulator.config_store import default_store  # noqa: E402
from tools.simulator.device_sim import DeviceSim  # noqa: E402


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
    page.set_form(_form_from(default_store()))

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
    sim = DeviceSim(store)
    sim.feed(build_command("HB"))
    lines = [ln for ln in sim.feed(build_command("CFG", "LIST"))
             if ln.startswith("{")]
    return SettingsForm(parse_catalog(lines))


@pytest.fixture
def form():
    return _form_from(default_store())


@pytest.fixture
def readonly_form(interlocked_items):
    """읽기 전용 항목이 있는 카탈로그로 만든 폼.

    🔴 제품 카탈로그에는 지금 읽기 전용 항목이 없다 — 5V 를 끌 수 있게
       하면서(사용자 확정 2026-08-14) 마지막 하나가 사라졌다. 읽기 전용을
       어떻게 그리는지는 규격 §7.3 이 정한 계약이므로, 쓰는 항목이 없어도
       계속 확인한다.
    """
    from tools.simulator.config_store import ConfigStore

    return _form_from(ConfigStore(interlocked_items()))


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
