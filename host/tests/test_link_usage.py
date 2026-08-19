"""링크 사용량 표의 시험. Qt 없이 돈다.

🔴 **왜 이 시험이 따로 생겼나**

   `field_budget` 은 **레코드 한 종류**가 얼마를 쓰는지만 안다. 그런데 이
   보드는 한 선으로 `ain`·`i2c`·`din` 을 함께 내보낸다 — 사용자가 알고 싶은
   것은 "지금 이 선을 몇 % 쓰는가" 이지 "아날로그만 따지면 몇 %인가" 가
   아니다.

   실제로 그것이 어긋나 있었다. I2C 포트를 켜도 화면의 대역폭 숫자는 꿈쩍도
   하지 않았다 — 켜는 사람은 공짜인 줄 안다.
"""

import pytest

from host.core.config_schema import parse_catalog
from host.core.framing import build_command
from host.gui.field_budget import budget_message
from host.gui.link_usage import (
    EVENT_TEXT,
    UNKNOWN_TEXT,
    compute_usage,
    field_names,
    link_baud,
    record_budget,
    record_line,
)
from host.gui.settings_form import SettingsForm
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim


def _catalog_lines() -> list[str]:
    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))
    return [ln for ln in sim.feed(build_command("CFG", "LIST"))
            if ln.startswith("{")]


@pytest.fixture
def form() -> SettingsForm:
    return SettingsForm(parse_catalog(_catalog_lines()))


def _row(usage, kind):
    return next(r for r in usage.rows if r.kind == kind)


# ------------------------------------------------------------ I2C 가 세어진다

def test_turning_on_an_i2c_port_raises_the_total(form):
    """🔴 이것이 이 모듈이 생긴 이유다.

    예전에는 켜진 토글 수 하나로 대역폭을 쟀고, 그 수는 `ain` 그룹만
    보았다. I2C 포트를 켜도 총합이 그대로였다 — 켜는 사람은 공짜인 줄
    안다.
    """
    before = compute_usage(form).bytes_per_s
    form.edit("i2c10.kind", "1")          # 조도 — 양 하나
    form.edit("i2c10.enabled", "true")
    assert compute_usage(form).bytes_per_s > before


def test_a_two_quantity_sensor_costs_more_than_a_one_quantity_sensor(form):
    """🔴 포트 하나가 레코드 하나가 아니다.

    온습도(kind 2)는 `temp` 와 `humidity` 를 따로 낸다 — 조도(kind 1)의
    두 배다. 포트 수로 세면 둘이 같아 보인다.
    """
    form.edit("i2c10.kind", "1")
    form.edit("i2c10.enabled", "true")
    one = compute_usage(form).bytes_per_s

    form.edit("i2c10.kind", "2")          # 온습도 — 양 둘
    two = compute_usage(form).bytes_per_s

    assert two > one
    # 같은 줄이 두 배로 나가는 것이므로 I2C 줄의 초당 바이트도 정확히 두 배다.
    assert _row(compute_usage(form), "i2c").lines_per_s == pytest.approx(
        2 * 1000.0 / 100)


def test_a_port_without_a_kind_sends_nothing(form):
    """종류를 안 고른 포트는 켜도 낼 것이 없다 (`i2cN.kind` = 없음).

    🔴 켰다는 이유로 세면 화면이 있지도 않은 트래픽을 경고한다.
    """
    before = compute_usage(form).bytes_per_s
    form.edit("i2c10.enabled", "true")     # kind 는 기본값(없음) 그대로
    assert compute_usage(form).bytes_per_s == before


# ------------------------------------------------------- 종류마다 제 마스크

def test_each_kind_measures_its_own_line(form):
    """🔴 마스크가 셋으로 갈렸다(규격 §7.2). `ain` 의 필드를 꺼도 `i2c`
    줄은 한 바이트도 변하지 않아야 한다 — 한 덩어리로 재던 시절의 계산이
    남아 있으면 여기서 걸린다."""
    form.edit("i2c10.kind", "2")
    form.edit("i2c10.enabled", "true")

    before = _row(compute_usage(form), "i2c").line_bytes
    form.edit("tx.fields_ain", "0")        # 아날로그 마스크를 통째로 끈다
    after = compute_usage(form)
    assert _row(after, "i2c").line_bytes == before
    # 그리고 아날로그 줄은 실제로 짧아졌다 — 시험이 아무것도 안 본 것이 아니다.
    assert _row(after, "ain").line_bytes < _row(
        compute_usage(SettingsForm(parse_catalog(_catalog_lines()))),
        "ain").line_bytes


def test_field_names_only_lists_bits_of_that_record(form):
    """비트 하나가 세 마스크에 같은 번호로 있지만 해당 레코드는 다르다."""
    names_ain = field_names(form, "ain")
    names_i2c = field_names(form, "i2c")
    assert "ma" in names_ain
    assert "ma" not in names_i2c          # 전류는 아날로그에만 있다


def test_i2c_line_carries_the_locked_fields(form):
    """`quantity`·`value` 는 마스크에 없어도 항상 실린다 (규격 §7.5)."""
    line = record_line(form, "i2c")
    assert line["type"] == "i2c"
    assert "quantity" in line and "value" in line


# ------------------------------------------------------------------ baud

def test_raising_the_link_baud_lowers_the_percentage(form):
    """🔴 baud 는 고정이 아니다 — `link.baud` 가 설정 항목이다(규격 §4.2).

    같은 트래픽이라도 선이 빨라지면 여유가 는다. 화면이 옛 속도로 계산하면
    사용자는 바꿔도 숫자가 그대로인 것을 본다.
    """
    form.edit("link.baud", "115200")
    slow = compute_usage(form)
    form.edit("link.baud", "2000000")
    fast = compute_usage(form)

    assert fast.baud == 2000000
    assert fast.bytes_per_s == slow.bytes_per_s      # 트래픽은 그대로
    assert fast.ratio < slow.ratio


def test_link_baud_falls_back_when_the_catalog_has_none(form):
    """카탈로그에 `link.baud` 가 없는 보드(옛 펌웨어)도 있다."""
    assert link_baud(form, 460800) == 921600         # 있으면 그것이 이긴다
    form.edit("link.baud", "")                       # 지우는 중
    assert link_baud(form, 460800) == 460800


# ------------------------------------------------------------ 디지털 입력

def test_digital_input_is_an_event_not_a_zero(form):
    """🔴 이벤트성 레코드(규격 §7.6)라 초당 몇 줄인지 **알 수 없다.**

    0 이라고 적으면 거짓말이고, 아무 수나 적으면 더 나쁜 거짓말이다.
    모른다고 말하고, 총합이 그만큼 낙관적이라는 것을 함께 알린다.
    """
    usage = compute_usage(form)
    din = _row(usage, "din")
    assert din.event_driven
    assert din.bytes_per_s is None
    assert din.lines_per_s is None
    assert din.detail == EVENT_TEXT
    assert din.cells()[-1] == UNKNOWN_TEXT
    # 줄 길이는 안다 — 재 봤기 때문이다. 모르는 것은 몇 번 나가느냐다.
    assert din.line_bytes > 0
    assert "이벤트" in usage.message
    assert usage.unmeasured == (din.label,)


# ------------------------------------------------------------------ 합계

def test_nothing_enabled_is_zero_percent():
    """채널이 하나도 안 켜져 있으면 0 % 다 — 겁을 주지 않는다."""
    form = SettingsForm(parse_catalog(_catalog_lines()))
    form.edit("ain0.enabled", "false")
    usage = compute_usage(form)
    assert usage.bytes_per_s == 0.0
    assert usage.ratio == 0.0
    assert usage.level == "ok"


def test_the_total_is_the_sum_of_the_measurable_rows(form):
    form.edit("i2c10.kind", "2")
    form.edit("i2c10.enabled", "true")
    usage = compute_usage(form)
    parts = sum(r.bytes_per_s for r in usage.rows if r.bytes_per_s is not None)
    assert usage.bytes_per_s == pytest.approx(parts)


def test_seven_channels_at_10ms_overflow_the_slowest_link(form):
    """🔴 사용자가 알고 싶어 한 바로 그 수 — 7 채널 × 10 ms.

    115200 에서는 물리적으로 못 보낸다. 그것을 켜기 **전에** 알아야 한다.
    """
    for ch in range(7):
        form.edit(f"ain{ch}.enabled", "true")
    form.edit("tx.period_ms", "10")

    form.edit("link.baud", "115200")
    assert compute_usage(form).level == "fault"
    form.edit("link.baud", "2000000")
    assert compute_usage(form).ratio < 1.0


def test_the_message_says_what_to_reduce(form):
    """경고 문구는 무엇을 줄여야 하는지 말한다 — `budget_message` 와 같은 결."""
    for ch in range(7):
        form.edit(f"ain{ch}.enabled", "true")
    form.edit("tx.period_ms", "10")
    form.edit("link.baud", "115200")
    usage = compute_usage(form)
    assert usage.level == "fault"
    assert "주기" in usage.message


# --------------------------------------------- 두 곳이 같은 숫자를 말한다

@pytest.mark.parametrize("kind", ["ain", "i2c", "din"])
def test_the_summary_and_the_field_mask_card_agree(form, kind):
    """🔴 요약 표와 필드 마스크 카드가 다른 숫자를 말하면 안 된다.

    둘 다 **같은 함수**(`record_budget`)를 쓴다. 각자 계산하면 언젠가
    한쪽만 고쳐지고, 그때 사용자는 어느 쪽을 믿어야 할지 알 수 없다.
    """
    form.edit("i2c10.kind", "2")
    form.edit("i2c10.enabled", "true")
    usage = compute_usage(form)
    row = _row(usage, kind)
    budget = record_budget(form, kind, usage.baud)

    assert row.line_bytes == budget.line_bytes
    if row.bytes_per_s is not None:
        assert row.bytes_per_s == budget.bytes_per_s
        # 카드가 띄우는 문구도 같은 예산에서 나온다.
        assert budget_message(budget)[0] in ("ok", "warn", "fault")


def test_the_card_and_the_summary_share_the_same_baud(form):
    """카드도 `link.baud` 를 따라간다 — 요약만 따라가면 두 숫자가 갈린다."""
    form.edit("link.baud", "115200")
    usage = compute_usage(form)
    assert usage.baud == 115200
    assert record_budget(form, "ain", usage.baud).capacity_bytes_per_s == 11520.0


# ------------------------------------------------------------------ 표 모양

def test_the_row_names_the_connectors_it_counted(form):
    """🔴 "몇 %" 만으로는 무엇을 끄면 되는지 모른다. 어느 커넥터가 켜져
    있어서 그 수가 나왔는지 함께 말한다."""
    form.edit("ain1.enabled", "true")
    form.edit("i2c12.kind", "2")
    form.edit("i2c12.enabled", "true")
    usage = compute_usage(form)
    assert "J3" in _row(usage, "ain").detail
    assert "J4" in _row(usage, "ain").detail
    assert "J12" in _row(usage, "i2c").detail
    assert "2 값" in _row(usage, "i2c").detail


def test_an_empty_row_says_so_instead_of_naming_nothing(form):
    """켜진 것이 없는 줄은 빈 칸이 아니라 "없음" 이다."""
    row = _row(compute_usage(form), "i2c")
    assert row.detail
    assert row.bytes_per_s == 0.0


def test_cells_are_ready_to_draw(form):
    """위젯은 문자열을 만들지 않는다 — 판정도 서식도 여기서 끝낸다."""
    cells = _row(compute_usage(form), "ain").cells()
    assert len(cells) == 5
    assert cells[0] == "아날로그"
    assert "ms" in cells[2]
    assert "%" in cells[4]
