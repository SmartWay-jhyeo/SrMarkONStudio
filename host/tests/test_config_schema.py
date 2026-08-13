import json

import pytest

from host.core.config_schema import ConfigSchema, parse_catalog
from host.core.errors import ConfigError, Reason


def _item(**fields) -> str:
    base = {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_item"}
    base.update(fields)
    return json.dumps(base)


def _field(**fields) -> str:
    base = {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_field"}
    base.update(fields)
    return json.dumps(base)


def _end(count: int) -> str:
    return json.dumps(
        {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_end", "count": count}
    )


CATALOG = [
    _item(key="tx.period_ms", grp="tx", vtype="u16", min=10, max=10000,
          default=100, cur=100, unit="ms", ro=False, label="전송 주기"),
    _item(key="pwr.5v", grp="pwr", vtype="bool", default=True, cur=True,
          ro=True, label="5V 레일", note="쿨링 팬 상시 동작 요구사항"),
    _item(key="adc.pga", grp="adc", vtype="enum",
          choices=[1, 2, 4, 8, 16, 32, 64], default=1, cur=1, ro=False,
          label="PGA"),
    _item(key="ain0.unit", grp="ain", vtype="str", max=7, default="",
          cur="bar", ro=False, label="단위"),
    _item(key="ain0.scale", grp="ain", vtype="f32", default=1.0, cur=1.0,
          ro=False, label="스케일"),
    _field(bit=3, name="raw", default=True, label="원시 카운트"),
    _end(6),                    # cfg_item 5 + cfg_field 1
]


def test_parse_catalog_collects_items_and_fields():
    schema = parse_catalog(CATALOG)
    assert set(schema.items) == {
        "tx.period_ms", "pwr.5v", "adc.pga", "ain0.unit", "ain0.scale",
    }
    assert schema.fields[3].name == "raw"


def test_parse_catalog_checks_declared_count():
    """cfg_end 의 count 가 실제 항목 수와 다르면 전송이 잘린 것이다."""
    with pytest.raises(ConfigError):
        parse_catalog([CATALOG[0], _end(99)])


def test_groups_are_ordered_by_first_appearance():
    schema = parse_catalog(CATALOG)
    assert schema.groups() == ["tx", "pwr", "adc", "ain"]


def test_validate_u16_in_range():
    schema = parse_catalog(CATALOG)
    assert schema.validate("tx.period_ms", "250") == 250


def test_validate_u16_below_min_raises_range():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("tx.period_ms", "5")
    assert exc.value.reason == Reason.RANGE


def test_validate_u16_non_numeric_raises_range():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("tx.period_ms", "abc")
    assert exc.value.reason == Reason.RANGE


def test_validate_unknown_key():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("nope.nope", "1")
    assert exc.value.reason == Reason.UNKNOWN_KEY


def test_validate_readonly_key():
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("pwr.5v", "false")
    assert exc.value.reason == Reason.READONLY


def test_validate_enum_rejects_value_outside_choices():
    schema = parse_catalog(CATALOG)
    assert schema.validate("adc.pga", "8") == 8
    with pytest.raises(ConfigError) as exc:
        schema.validate("adc.pga", "3")
    assert exc.value.reason == Reason.RANGE


def test_validate_str_respects_max_length():
    schema = parse_catalog(CATALOG)
    assert schema.validate("ain0.unit", "kPa") == "kPa"
    with pytest.raises(ConfigError) as exc:
        schema.validate("ain0.unit", "12345678")
    assert exc.value.reason == Reason.RANGE


def test_validate_accepts_zero_padded_integer():
    """🔴 '08' 은 유효한 십진수다.

    int(raw, 0) 을 쓰면 파이썬이 앞의 0 을 8진수 접두사로 읽어 ValueError 가
    난다. 고정폭 습관으로 0 을 채워 넣는 건 지극히 자연스러운데, 그게
    '정수가 아님' 으로 거부되면 사용자가 원인을 짐작할 수도 없다.
    """
    schema = parse_catalog(CATALOG)
    assert schema.validate("tx.period_ms", "0100") == 100
    assert schema.validate("tx.period_ms", "0250") == 250


def test_zero_padded_value_fails_on_range_not_on_parsing():
    """거부되더라도 사유가 '범위' 여야 한다. 파싱 실패가 아니다."""
    schema = parse_catalog(CATALOG)
    with pytest.raises(ConfigError) as exc:
        schema.validate("tx.period_ms", "08")
    assert exc.value.reason == Reason.RANGE
    assert "최소" in exc.value.detail          # 파싱 오류가 아니라 범위 오류


def test_validate_rejects_nan_and_inf():
    """🔴 NaN 은 범위 검사를 그대로 뚫는다.

    nan < min 도 nan > max 도 False 다. 범위가 없는 항목이면 inf 도 통과한다.
    보드 저장소(Task 6)는 이미 막고 있으므로 호스트도 같은 규칙이어야 한다.
    """
    schema = parse_catalog(CATALOG)
    for bad in ("nan", "inf", "-inf", "Infinity", "NaN"):
        with pytest.raises(ConfigError) as exc:
            schema.validate("ain0.scale", bad)
        assert exc.value.reason == Reason.RANGE, bad


def test_validate_accepts_normal_float():
    schema = parse_catalog(CATALOG)
    assert schema.validate("ain0.scale", "2.5") == 2.5
    assert schema.validate("ain0.scale", "-1.25") == -1.25


def test_parse_catalog_requires_cfg_end():
    """🔴 종료 줄이 아예 없는 것도 절단이다.

    cfg_end 는 마지막에 보내므로 전송이 끊기면 '개수가 틀린 cfg_end' 가
    아니라 'cfg_end 자체가 없음' 이 된다. 이쪽이 더 흔한 형태다.
    통과시키면 GUI 가 설정이 빠진 화면을 경고 없이 정상처럼 그린다.
    """
    with pytest.raises(ConfigError) as exc:
        parse_catalog(CATALOG[:-1])            # cfg_end 만 뺀다
    assert exc.value.reason == Reason.RANGE
    assert "cfg_end" in exc.value.detail


def test_validate_rejects_non_ascii_string():
    """🔴 사용자가 넣는 값은 ASCII 만 통과한다.

    단위는 'degC'·'kPa' 처럼 쓴다. 보드 쪽 저장소(Task 6)와 같은 규칙이라
    호스트에서 미리 걸러 왕복을 아낀다. 보드가 최종 권위인 것은 변함없다.
    """
    schema = parse_catalog(CATALOG)
    for bad in ("℃", "바", "Ω"):
        with pytest.raises(ConfigError) as exc:
            schema.validate("ain0.unit", bad)
        assert exc.value.reason == Reason.RANGE, bad


def test_validate_accepts_ascii_string():
    schema = parse_catalog(CATALOG)
    assert schema.validate("ain0.unit", "kPa") == "kPa"


def test_validate_bool_accepts_true_false_words():
    schema = parse_catalog([
        _item(key="pwr.24v", grp="pwr", vtype="bool", default=False,
              cur=False, ro=False, label="24V"),
        _end(1),
    ])
    assert schema.validate("pwr.24v", "true") is True
    assert schema.validate("pwr.24v", "false") is False
    assert schema.validate("pwr.24v", "1") is True
    assert schema.validate("pwr.24v", "0") is False
