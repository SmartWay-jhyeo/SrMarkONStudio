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
    _field(bit=3, name="raw", default=True, label="원시 카운트"),
    _end(5),                    # cfg_item 4 + cfg_field 1
]


def test_parse_catalog_collects_items_and_fields():
    schema = parse_catalog(CATALOG)
    assert set(schema.items) == {"tx.period_ms", "pwr.5v", "adc.pga", "ain0.unit"}
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
