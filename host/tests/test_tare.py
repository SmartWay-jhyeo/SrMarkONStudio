"""영점 맞추기 로직의 시험. Qt 없이 돈다.

🔴 여기서 확인하는 것은 **보정이 무엇을 건드리고 무엇을 안 건드리는가** 다.
   영점 보정이 스팬까지 흔들면 화면에는 아무 표시가 없고, 몇 달 뒤 측정값이
   조금씩 틀린 것을 누군가 발견하게 된다. 그때는 언제부터 틀렸는지 알 수 없다.
"""

import pytest

from host.core.config_schema import parse_catalog
from host.core.framing import build_command
from host.gui.settings_form import SettingsForm
from host.gui.tare import NOMINAL_ZERO_MA, tare_rows, tared_zero
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim


@pytest.fixture
def form() -> SettingsForm:
    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))
    lines = [ln for ln in sim.feed(build_command("CFG", "LIST"))
             if ln.startswith("{")]
    return SettingsForm(parse_catalog(lines))


# ---- 보정이 무엇을 바꾸는가 ------------------------------------------------


def test_tare_makes_the_measured_current_the_new_zero():
    """🔴 지금 흐르는 전류가 곧 새 영점이다.

    센서를 0 상태로 두고 잡는 것이므로, 그 순간의 전류에서 물리량이 0 이
    되어야 한다. `value = (ma - zero) * scale` 이므로 zero 를 그 전류로
    옮기면 된다.
    """
    assert tared_zero(3.982) == 3.982


def test_tare_does_not_touch_the_span():
    """🔴 스팬(mA 당 물리량)은 건드리지 않는다.

    영점 잡기는 **한 점** 보정이다. 한 점으로는 기울기를 알 수 없으므로,
    기울기를 함께 고치면 그것은 근거 없이 지어낸 값이다. 계기 보정의
    표준 동작이기도 하다 — 0 점만 옮기고 스팬은 두 점 보정으로 잡는다.

    이 시험이 지키는 것은 `tared_zero` 가 **스케일을 인자로 받지 않는다**는
    사실이다. 받지 않으면 바꿀 수도 없다.
    """
    import inspect

    params = inspect.signature(tared_zero).parameters
    assert list(params) == ["measured_ma"], (
        "스케일을 인자로 받으면 언젠가 그것을 고치게 된다")


def test_nominal_zero_is_the_loop_bottom():
    """되돌리기는 4 mA 다 — 루프의 살아 있는 0 점(데이터시트 §5.3)."""
    assert NOMINAL_ZERO_MA == 4.0


# ---- 목록 만들기 -----------------------------------------------------------


def test_rows_cover_every_analog_channel(form):
    rows = tare_rows(form, live_ma={})
    assert [r.label for r in rows] == [f"J{i}" for i in range(3, 10)]
    assert all(r.zero_key.endswith(".zero") for r in rows)


def test_row_carries_the_live_reading_and_the_gap(form):
    """🔴 `live_ma` 의 키는 **채널 번호**다 — J4 는 채널 1.

    `channel_ranges` 와 같은 규약이다. 사람이 보는 것은 커넥터(J4)지만
    화면 안쪽은 채널로 다닌다. 여기서만 커넥터로 받으면 규약이 둘이 되고,
    한쪽을 고칠 때 다른 쪽이 조용히 어긋난다.
    """
    form.edit("ain1.enabled", "true")
    rows = tare_rows(form, live_ma={1: 3.982})
    j4 = next(r for r in rows if r.label == "J4")
    assert j4.live_ma == 3.982
    assert j4.zero_ma == pytest.approx(4.0)
    assert j4.offset == pytest.approx(-0.018)
    assert j4.can_tare


def test_a_channel_without_a_reading_cannot_be_tared(form):
    """🔴 값이 없으면 잡지 않는다.

    없는 값을 0 으로 치고 잡으면 영점이 0 mA 로 내려앉아, 그 채널의 모든
    측정값이 통째로 틀어진다. 그런데 화면은 아무 일도 없었던 것처럼 보인다.
    """
    form.edit("ain1.enabled", "true")     # 켜 두어야 "값이 없어서" 임이 분명하다
    rows = tare_rows(form, live_ma={})
    j4 = next(r for r in rows if r.label == "J4")
    assert j4.live_ma is None
    assert j4.offset is None
    assert not j4.can_tare


def test_channels_that_are_off_are_listed_but_not_tarable(form):
    """🔴 목록에서 빼지 않는다. 빠지면 "왜 J6 이 없지?" 가 되고, 답이
       화면 어디에도 없다. 남겨 두고 못 잡는 이유를 보여 준다."""
    form.edit("ain3.enabled", "false")
    rows = tare_rows(form, live_ma={3: 4.0})
    j6 = next(r for r in rows if r.label == "J6")
    assert not j6.enabled
    assert not j6.can_tare


def test_reading_without_a_matching_channel_is_ignored(form):
    """카탈로그에 없는 커넥터 번호가 와도 줄이 생기지 않는다."""
    rows = tare_rows(form, live_ma={99: 12.0})
    assert all(r.label != "J99" for r in rows)


def test_offset_is_measured_against_the_stored_zero_not_4ma(form):
    """🔴 차이는 **지금 설정된 영점** 기준이다.

    이미 한 번 잡아 둔 채널을 다시 보면 차이가 0 이어야 한다. 4 mA 기준으로
    재면 이미 맞춘 채널이 계속 어긋나 보이고, 사용자가 다시 잡게 된다.
    """
    form.edit("ain1.zero", "3.982")
    rows = tare_rows(form, live_ma={1: 3.982})
    j4 = next(r for r in rows if r.label == "J4")
    assert j4.zero_ma == pytest.approx(3.982)
    assert j4.offset == pytest.approx(0.0)


def test_blocked_reason_tells_which_wall_you_hit(form):
    """🔴 "못 잡는다" 만으로는 무엇을 해야 할지 모른다.

    채널이 꺼진 것과 값이 안 오는 것은 사용자가 할 일이 다르다 — 앞은
    켜면 되고, 뒤는 배선이나 센서를 봐야 한다.
    """
    form.edit("ain3.enabled", "false")
    form.edit("ain1.enabled", "true")
    rows = tare_rows(form, live_ma={3: 4.0, 1: None})
    by = {r.label: r for r in rows}
    assert by["J6"].blocked_reason == "채널 꺼짐"
    assert by["J4"].blocked_reason == "값 없음"
    ok = {r.label: r for r in tare_rows(form, live_ma={1: 4.0})}["J4"]
    assert ok.blocked_reason == ""
