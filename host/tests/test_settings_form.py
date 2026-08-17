"""설정 화면 로직의 시험. Qt 없이 돈다."""

import pytest

from host.core.config_schema import ConfigItem, ConfigSchema, parse_catalog
from host.core.framing import build_command
from host.gui.settings_form import (
    SettingsForm,
    Widget,
    build_row,
    group_label,
    channel_ranges,
    channel_units,
    matrix_of,
    telemetry_shape,
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

    🔴 그래서 개수를 **여기에도 적지 않는다.** 예전에는 `== 45` 였는데,
       보드에 항목이 늘 때마다 이 시험이 깨졌다 — 하드코딩하지 말라는
       시험이 정작 목록 길이를 하드코딩하고 있었다. 카탈로그가 스스로
       말하는 수(`cfg_end` 의 `count`)와 대조한다.
    """
    # `parse_catalog` 이 `cfg_end` 의 count 와 실제 줄 수를 이미 대조한다
    # (config_schema §160) — 여기서는 폼이 그 항목들을 하나도 빠뜨리거나
    # 더하지 않았는지만 본다.
    schema = parse_catalog(_catalog_lines())
    assert len(form.keys()) == len(schema.items)
    assert sorted(form.keys()) == sorted(schema.items)
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


# ------------------------------------------------------------------ 표 접기

def _group(form: SettingsForm, name: str):
    return next(g for g in form.groups if g.name == name)


def test_repeated_items_fold_into_a_table(form):
    """🔴 45개 항목 중 35개가 7채널 × 5속성이다. 그건 폼이 아니라 **표**다.

    세로로 35줄을 쌓으면 스크롤이 길어지는 것도 문제지만, 더 큰 문제는
    "J5 의 영점이 J6 보다 큰가" 를 볼 수 없다는 것이다. 채널끼리 비교하는
    것이 이 화면의 주된 용도인데.
    """
    m = matrix_of(_group(form, "ain"))
    assert m is not None
    assert len(m.rows) == 7
    assert len(m.columns) == 5


def test_table_rows_and_columns_are_named_from_the_labels(form):
    """🔴 열 제목을 하드코딩하지 않는다.

    보드가 준 라벨이 `J3 영점` · `J4 영점` 처럼 생겼으므로, 한 채널의
    라벨들이 **공통으로 가진 앞부분**이 행 이름이고 나머지가 열 이름이다.
    보드가 라벨을 바꾸면 화면도 따라간다.
    """
    m = matrix_of(_group(form, "ain"))
    assert [r.label for r in m.rows] == [f"J{i}" for i in range(3, 10)]
    assert m.column_labels[0] == "사용"
    assert "영점" in m.column_labels


def test_table_cells_line_up_with_their_column(form):
    m = matrix_of(_group(form, "ain"))
    zero = m.column_labels.index("영점")
    for row in m.rows:
        assert row.cells[zero] is not None
        assert row.cells[zero].key.endswith(".zero")


def test_scalars_beside_a_repeating_set_do_not_block_the_table(form):
    """🔴 그룹에 반복되지 않는 항목이 섞여 있어도 반복분은 접는다.

    LED 그룹이 그렇다 — `체인 LED 수`·`밝기` 는 하나뿐이고 J21~J24 의
    R·G·B 는 반복이다. 예전 규칙("하나라도 어긋나면 표가 아니다")은 이
    그룹을 12줄로 늘어놓았다. 섞였다고 반복을 못 접을 이유가 없다.
    """
    group = _group(form, "led")
    m = matrix_of(group)
    assert m is not None
    assert [r.label for r in m.rows] == [f"J{i}" for i in range(21, 25)]
    assert len(m.columns) == 3                      # 빨강·초록·파랑

    # 🔴 남는 항목을 이름으로 적지 않는다. 카탈로그는 보드에서 오고 늘어난다 —
    #    실제로 `led.grb` 가 늘면서 이 시험이 깨졌다. 확인할 것은 "무엇이
    #    남았나" 가 아니라 **한 항목도 잃지 않았나** 다. 표에 접힌 것과 남은
    #    것을 합치면 그룹 전체가 나와야 한다.
    folded = {c.key for r in m.rows for c in r.cells if c is not None}
    left = {r.key for r in m.leftovers}
    assert folded | left == {r.key for r in group.rows}
    assert not (folded & left), "같은 항목이 표와 폼에 두 번 나오면 안 된다"
    assert left, "반복이 아닌 항목은 남아야 한다"


def test_a_group_that_does_not_repeat_stays_a_form(form):
    """🔴 전송·ADC 는 표로 접을 것이 없다. 억지로 접으면 한 줄짜리
       표가 되어 폼보다 읽기 나쁘다."""
    assert matrix_of(_group(form, "tx")) is None
    assert matrix_of(_group(form, "adc")) is None
    assert matrix_of(_group(form, "dev")) is None


def test_two_of_a_kind_is_not_a_table():
    """🔴 반복이 적으면 표가 이득이 아니다. 헤더 줄이 본문보다 길어진다."""
    from host.gui.settings_form import Group

    rows = [
        build_row(ConfigItem(key=f"x{i}.a", group="x", vtype="u8",
                             default=1, current=1, label=f"X{i} 가"))
        for i in range(2)
    ] + [
        build_row(ConfigItem(key=f"x{i}.b", group="x", vtype="u8",
                             default=1, current=1, label=f"X{i} 나"))
        for i in range(2)
    ]
    assert matrix_of(Group(name="x", rows=rows)) is None


def test_a_missing_cell_leaves_a_hole_not_a_shifted_row():
    """🔴 어떤 채널에만 있는 속성이 있어도 열이 밀리지 않는다.

    밀리면 J5 의 영점 자리에 J6 의 스케일이 들어가고, 그 표는 조용히
    거짓말을 한다. 없는 칸은 비워 둔다.
    """
    from host.gui.settings_form import Group

    rows = [
        build_row(ConfigItem(key="y0.a", group="y", vtype="u8",
                             default=1, current=1, label="Y0 가")),
        build_row(ConfigItem(key="y0.b", group="y", vtype="u8",
                             default=1, current=1, label="Y0 나")),
        build_row(ConfigItem(key="y1.a", group="y", vtype="u8",
                             default=1, current=1, label="Y1 가")),
        build_row(ConfigItem(key="y2.a", group="y", vtype="u8",
                             default=1, current=1, label="Y2 가")),
        build_row(ConfigItem(key="y2.b", group="y", vtype="u8",
                             default=1, current=1, label="Y2 나")),
    ]
    m = matrix_of(Group(name="y", rows=rows))
    assert m is not None
    assert [c.key if c else None for c in m.rows[1].cells] == ["y1.a", None]


# ------------------------------------------------------- 전송 모양

def test_telemetry_shape_counts_only_the_channels_that_are_on(form):
    """대역폭은 켜진 채널 수에 곱해진다 — 꺼진 것을 세면 겁만 준다."""
    shape = telemetry_shape(form)
    assert shape.channels == 1          # 시뮬레이터 기본값은 J3 하나
    form.edit("ain1.enabled", "true")
    assert telemetry_shape(form).channels == 2


def test_telemetry_shape_reads_the_spec_named_keys(form):
    """규격 §7.2 가 이름 지은 항목들 — `tx.period_ms` · `tx.float_digits`."""
    shape = telemetry_shape(form)
    assert shape.period_ms == 100
    assert shape.float_digits == 4


def test_telemetry_shape_survives_a_half_typed_number(form):
    """🔴 사용자가 주기를 지우는 동안 값은 빈 문자열이다.

    그때 예외가 나면 설정 화면이 통째로 죽는다 — 숫자 하나를 지웠을 뿐인데.
    """
    form.edit("tx.period_ms", "")
    shape = telemetry_shape(form)
    assert shape.period_ms > 0


def test_channel_ranges_come_back_as_physical_numbers(form):
    """🔴 게이지가 `0 – 150 bar` 를 그리려면 설정을 되짚어야 한다.

    보드는 영점·스케일로 들고 있고 사람은 범위로 생각한다. 그 환산은
    규격 §7.2.1 이 정했으므로 화면이 대신할 수 있다.
    """
    form.edit("ain0.zero", "4")
    form.edit("ain0.scale", "9.375")
    assert channel_ranges(form)[0] == (0.0, 150.0)


def test_a_channel_with_no_scale_has_no_range(form):
    """스케일이 0 이면 되짚을 수 없다 — 없는 것을 지어내지 않는다."""
    form.edit("ain0.scale", "0")
    assert 0 not in channel_ranges(form)


def test_channel_units_come_from_the_settings(form):
    """🔴 `unit` 필드는 기본 마스크에서 꺼져 있어 텔레메트리로 안 온다.

    그래도 사용자는 그것을 설정에 적어 두었다. 화면이 `150` 이 아니라
    `150 bar` 라고 말할 수 있는 근거가 거기 있다.
    """
    form.edit("ain0.unit", "bar")
    assert channel_units(form)[0] == "bar"


# 🔴 `test_imports_no_qt` 는 여기서 걷어냈다. 파일마다 손으로 복사한
#    문자열 검사였고, 그러다 보니 정작 `screen.py`·`theme.py` 에는
#    없었다. 지금은 `test_layer_boundaries.py` 가 층 전체를 AST 로 훑는다.
