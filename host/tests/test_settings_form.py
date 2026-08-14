"""설정 화면 로직의 시험. Qt 없이 돈다."""

import pytest

from host.core.config_schema import ConfigItem, ConfigSchema, parse_catalog
from host.core.framing import build_command
from host.gui.settings_form import (
    SettingsForm,
    Widget,
    build_row,
    group_label,
)
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


@pytest.fixture
def readonly_form(interlocked_items) -> SettingsForm:
    """읽기 전용 항목이 있는 카탈로그로 만든 폼.

    🔴 제품 카탈로그에서 빌리지 않는다. 5V 를 끌 수 있게 하면서(사용자 확정
       2026-08-14) 읽기 전용 항목이 하나도 남지 않았고, 그때 이 시험들이
       "읽기 전용 항목이 하나는 있어야 한다" 로 깨졌다.

       읽기 전용을 화면에 어떻게 그리고 사유를 어떻게 보여 주는지는 규격
       §7.3 이 정한 계약이다. 지금 그것을 쓰는 제품 항목이 없더라도 계약은
       유효하고, 나중에 필요한 항목이 생겼을 때 동작해야 한다.
    """
    from tools.simulator.config_store import ConfigStore

    sim = DeviceSim(ConfigStore(interlocked_items()))
    sim.feed(build_command("HB"))
    lines = [ln for ln in sim.feed(build_command("CFG", "LIST"))
             if ln.startswith("{")]
    return SettingsForm(parse_catalog(lines))


# --------------------------------------------------------- 하드코딩 금지

def test_form_is_built_only_from_the_catalog(form):
    """🔴 항목을 하드코딩하지 않는다.

    펌웨어를 단계로 올리므로 2단계에서 항목이 늘고 3단계에서 또 는다.
    호스트에 목록을 박아 두면 그때마다 GUI 를 다시 배포해야 하고, 보드마다
    펌웨어 버전이 다르면 맞출 방법이 없다.
    """
    assert len(form.keys()) == 45
    assert "tx.period_ms" in form.keys()


def test_unknown_group_does_not_break_the_screen():
    """보드가 모르는 그룹을 보내도 화면이 열려야 한다."""
    schema = ConfigSchema()
    schema.items["x.y"] = ConfigItem(key="x.y", group="미래그룹", vtype="u16",
                                     default=1, current=1)
    schema._group_order.append("미래그룹")
    f = SettingsForm(schema)
    assert [g.name for g in f.groups] == ["미래그룹"]
    assert group_label("미래그룹") == "미래그룹"


def test_unknown_vtype_falls_back_to_text():
    """🔴 모르는 vtype 하나 때문에 설정 화면이 통째로 안 열리면 안 된다.

    사용자는 나머지 44개 항목도 못 고치게 된다. 문자열로 그리는 편이 낫다 —
    값 검사는 보드가 하므로 잘못된 값은 거부된다.
    """
    item = ConfigItem(key="new.thing", group="new", vtype="i128",
                      default="0", current="0")
    assert build_row(item).widget is Widget.TEXT


# ------------------------------------------------------------- 위젯 선택

@pytest.mark.parametrize("key,want", [
    ("pwr.24v", Widget.TOGGLE),      # bool
    ("tx.period_ms", Widget.NUMBER),  # u16
    ("adc.drate", Widget.CHOICE),     # enum
    ("dev.id", Widget.TEXT),          # str
    ("ain0.zero", Widget.NUMBER),     # f32
])
def test_widget_follows_vtype(form, key, want):
    assert form.row(key).widget is want


def test_enum_row_carries_choices(form):
    row = form.row("adc.drate")
    assert row.choices
    assert row.value in [str(c) for c in row.choices]


def test_number_row_carries_range(form):
    row = form.row("tx.period_ms")
    assert row.minimum is not None and row.maximum is not None
    assert row.is_integer is True


def test_float_row_is_not_integer(form):
    assert form.row("ain0.zero").is_integer is False


# ------------------------------------------------------------- 읽기 전용

def test_readonly_row_is_disabled_with_the_boards_reason(readonly_form):
    """🔴 `note` 를 버리고 '읽기 전용' 이라고만 쓰면 이유가 사라진다.

    보드가 **왜** 못 바꾸는지 알려 준다. 사유 없이 비활성이면 사용자는
    고장인 줄 안다.
    """
    readonly = [k for k in readonly_form.keys()
                if not readonly_form.row(k).editable]
    assert readonly, "읽기 전용 항목이 하나는 있어야 한다"
    for key in readonly:
        assert readonly_form.row(key).reason, f"{key} 에 이유가 없다"


def test_pwr_5v_is_editable_but_carries_a_warning(form):
    """5V 는 끌 수 있다 (사용자 확정 2026-08-14).

    🔴 막지 않는 대신 무엇이 함께 멈추는지 화면이 말한다 — 쿨링 팬,
       아날로그 수집, WS2812. 그것이 인터록을 푼 대신 남은 안전장치다.
    """
    row = form.row("pwr.5v")
    assert row.editable, "5V 는 이제 바꿀 수 있다"
    assert "팬" in row.note, row.note


def test_validate_reports_the_reason_for_readonly(readonly_form):
    readonly = next(k for k in readonly_form.keys()
                    if not readonly_form.row(k).editable)
    assert readonly_form.validate(readonly)


# --------------------------------------------------------------- 편집

def test_edit_marks_dirty(form):
    assert not form.has_changes
    form.edit("tx.period_ms", "250")
    assert form.is_dirty("tx.period_ms")
    assert form.dirty_keys == ["tx.period_ms"]


def test_editing_back_to_original_is_not_dirty(form):
    """🔴 고쳤다가 되돌린 항목을 보내지 않는다.

    보낼 이유가 없는 줄로 링크를 채우지 않는다 — 115200 에서 여유가 초당
    600 바이트뿐이다 (docs/measurements/2026-08-14_link_budget.md).
    """
    original = form.row("tx.period_ms").value
    form.edit("tx.period_ms", "250")
    assert form.has_changes
    form.edit("tx.period_ms", original)
    assert not form.has_changes
    assert form.dirty_keys == []


def test_revert_and_revert_all(form):
    form.edit("tx.period_ms", "250")
    form.edit("dev.id", "9")
    form.revert("dev.id")
    assert form.dirty_keys == ["tx.period_ms"]
    form.revert_all()
    assert not form.has_changes


def test_reset_to_default(form):
    row = form.row("tx.period_ms")
    form.edit("tx.period_ms", "9999")
    form.reset_to_default("tx.period_ms")
    assert form.row("tx.period_ms").value == row.default


def test_edit_unknown_key_raises(form):
    with pytest.raises(KeyError):
        form.edit("없는키", "1")


def test_editing_does_not_validate_mid_typing(form):
    """🔴 한 글자 칠 때마다 빨간 줄을 긋지 않는다.

    `250` 을 치는 도중의 `2` 는 범위 밖이다. 그때마다 오류를 띄우면 읽을
    수 없다. edit 은 받아들이고, 검사는 validate 와 보낼 때 한다.
    """
    form.edit("tx.period_ms", "2")        # 최소값 미만이지만 예외가 안 난다
    assert form.row("tx.period_ms").value == "2"


# --------------------------------------------------------------- 검사

def test_validate_catches_out_of_range(form):
    form.edit("tx.period_ms", "999999")
    assert form.validate("tx.period_ms")
    assert "tx.period_ms" in form.errors()


def test_validate_passes_good_value(form):
    form.edit("tx.period_ms", "250")
    assert form.validate("tx.period_ms") == ""
    assert form.errors() == {}


def test_validate_catches_protocol_delimiters(form):
    """`$`·`,`·`*` 가 값에 들어가면 줄 구조가 깨진다."""
    form.edit("dev.id", "a,b")
    assert form.validate("dev.id")


def test_validate_catches_wire_length(form):
    """🔴 전선 상한을 넘기면 보드가 조용히 버린다 (규격 §3.1).

    응답이 없는 것을 사용자는 "먹통" 으로 읽는다. 보내기 전에 막는다.
    """
    form.edit("dev.id", "x" * 40)
    msg = form.validate("dev.id")
    assert msg
    assert "바이트" in msg


def test_pending_changes_excludes_bad_values(form):
    form.edit("tx.period_ms", "250")        # 좋다
    form.edit("dev.id", "a,b")              # 나쁘다
    pending = dict(form.pending_changes())
    assert "tx.period_ms" in pending
    assert "dev.id" not in pending


# ------------------------------------------------------------- 수락·거부

def test_accept_makes_the_value_the_new_original(form):
    form.edit("tx.period_ms", "250")
    form.accept("tx.period_ms")
    assert not form.is_dirty("tx.period_ms")
    assert form.row("tx.period_ms").value == "250"
    # 다시 원래 값으로 바꾸면 그때는 dirty 다
    form.edit("tx.period_ms", "100")
    assert form.is_dirty("tx.period_ms")


def test_reject_keeps_what_the_user_typed(form):
    """🔴 거부됐다고 값을 되돌리지 않는다.

    사용자가 방금 친 것을 화면에서 지우면 무엇을 고치려 했는지 사라진다.
    사유를 보여 주고 고칠 기회를 준다.
    """
    form.edit("pwr.24v", "true")
    form.reject("pwr.24v", "INTERLOCK")
    assert form.row("pwr.24v").value == "true"
    assert form.is_dirty("pwr.24v")


# --------------------------------------------------------------- 구성

def test_groups_follow_the_boards_order(form):
    names = [g.name for g in form.groups]
    assert names == [n for n in form._schema.groups() if n in names]
    assert len(names) >= 4


def test_every_item_lands_in_exactly_one_group(form):
    seen = [r.key for g in form.groups for r in g.rows]
    assert sorted(seen) == sorted(form.keys())


def test_group_labels_are_korean(form):
    assert group_label("pwr") == "전원"
    assert group_label("adc") == "ADC"


def test_bool_values_are_wire_text(form):
    """전선에 나갈 형태로 들고 있는다 — `True` 가 아니라 `true`."""
    row = form.row("pwr.24v")
    assert row.value in ("true", "false")
    assert row.default in ("true", "false")


def test_float_values_do_not_leak_repr_noise():
    """🔴 `0.30000000000000004` 를 전선에 내보내지 않는다."""
    item = ConfigItem(key="a.b", group="a", vtype="f32",
                      default=0.1 + 0.2, current=4.0)
    row = build_row(item)
    assert row.default == "0.3"
    assert row.value == "4"


def test_imports_no_qt():
    import inspect

    import host.gui.settings_form as mod

    src = inspect.getsource(mod)
    assert "PyQt" not in src
    assert "QtWidgets" not in src
