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
from host.gui.theme import stylesheet  # noqa: E402
from host.gui.widgets.status_chip import Level, Verification  # noqa: E402
from tools.simulator.config_store import default_store  # noqa: E402
from tools.simulator.device_sim import DeviceSim  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(stylesheet())
    yield a


@pytest.fixture
def form():
    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))
    lines = [ln for ln in sim.feed(build_command("CFG", "LIST"))
             if ln.startswith("{")]
    return SettingsForm(parse_catalog(lines))


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

def test_settings_page_draws_every_catalog_item(app, form):
    """🔴 45개 항목이 카탈로그만으로 그려지는지."""
    page = SettingsPage()
    page.set_form(form)
    assert len(page._rows) == len(form.keys()) == 45


def test_readonly_row_is_disabled_and_explains_why(app, form):
    page = SettingsPage()
    page.set_form(form)
    readonly = [k for k in form.keys() if not form.row(k).editable]
    assert readonly
    for key in readonly:
        w = page._rows[key]
        assert not w._editor.isEnabled(), f"{key} 가 편집 가능하다"
        assert w.toolTip(), f"{key} 에 이유 툴팁이 없다"


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


def test_stylesheet_applies(app):
    assert "background" in stylesheet()
    assert app.styleSheet()
