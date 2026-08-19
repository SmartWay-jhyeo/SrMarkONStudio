import json

from tools.simulator.config_store import default_store
from tools.simulator.telemetry import (
    ADS1256_FULL_SCALE,
    FULL_SCALE_V,
    build_ain_record,
    ma_to_value,
    raw_to_ma,
    render,
)

MANDATORY = {"schema_ver", "seq", "t", "type"}


def _rec(store, **kw):
    args = dict(channel=0, seq=1, t_ms=1772200855875, raw=ADS1256_FULL_SCALE // 2,
                capture_counter=123456789)
    args.update(kw)
    return build_ain_record(store, **args)


def test_mandatory_fields_always_present_even_with_zero_mask():
    store = default_store()
    store.set("tx.fields_ain", "0")
    rec = _rec(store)
    assert MANDATORY <= set(rec)


def test_default_mask_includes_raw_and_excludes_device_id():
    store = default_store()
    rec = _rec(store)
    assert "raw" in rec
    assert "device_id" not in rec


def test_enabling_device_id_bit_adds_field():
    store = default_store()
    store.set("tx.fields_ain", str(store.field_mask("ain") | (1 << 0)))
    assert "device_id" in _rec(store)


def test_connector_id_maps_channel_to_j_number():
    """AIN0 은 J3 이다 (데이터시트 §5.3)."""
    store = default_store()
    assert _rec(store, channel=0)["connector_id"] == 3
    assert _rec(store, channel=6)["connector_id"] == 9


def test_raw_to_ma_at_20ma_full_loop():
    """20 mA × 120 Ω = 2.40 V.

    🔴 만재는 VREF 가 아니라 **2·VREF** 다 — ADS1256.pdf p.11 "full-scale
       input range is ±2VREF (for PGA = 1)".

       이 시험은 잘못된 상수(2.5)를 **손으로 다시 적어** 계산하고 있었다.
       구현과 시험이 같은 오해를 나눠 가지면 둘이 늘 일치하므로 영원히
       통과한다. 실기기에서 4 mA 신호가 1.99 mA 로 나올 때까지 아무도 몰랐다.
       그래서 여기서는 구현이 쓰는 상수를 그대로 가져다 쓴다.
    """
    raw = round(2.40 / FULL_SCALE_V * ADS1256_FULL_SCALE)
    assert abs(raw_to_ma(raw) - 20.0) < 0.01


def test_raw_to_ma_at_4ma():
    raw = round(0.48 / FULL_SCALE_V * ADS1256_FULL_SCALE)
    assert abs(raw_to_ma(raw) - 4.0) < 0.01


def test_full_scale_is_twice_the_reference():
    """🔴 위 두 시험이 구현 상수를 빌려 쓰므로, 그 상수 자체는 여기서 못
       박는다. 안 그러면 상수가 틀려도 둘 다 통과한다."""
    assert FULL_SCALE_V == 5.0


def test_ma_to_value_applies_zero_and_scale():
    assert abs(ma_to_value(12.0, zero=4.0, scale=0.5) - 4.0) < 1e-9


def test_float_digits_setting_controls_rounding():
    store = default_store()
    store.set("tx.float_digits", "2")
    rec = _rec(store)
    text = json.dumps(rec["ma"])
    assert len(text.split(".")[-1]) <= 2


def test_raw_is_integer_and_never_rounded():
    """raw 가 원본이다. 자릿수 설정이 raw 를 건드리면 안 된다."""
    store = default_store()
    store.set("tx.float_digits", "2")
    rec = _rec(store, raw=8388607)
    assert rec["raw"] == 8388607
    assert isinstance(rec["raw"], int)


def test_render_produces_single_line():
    store = default_store()
    line = render(_rec(store))
    assert "\n" not in line
    assert json.loads(line)["type"] == "ain"


def test_smaller_mask_produces_shorter_line():
    """필드 마스크가 대역폭 대책이라는 전제를 지킨다."""
    store = default_store()
    full = len(render(_rec(store)))
    store.set("tx.fields_ain", "0")
    minimal = len(render(_rec(store)))
    assert minimal < full


# ---- i2c (규격 §7.5) --------------------------------------------------------


def test_i2c_quantity_and_value_survive_a_zero_mask():
    """🔴 [신규, 2026-08-19] 규격 §7.5 — quantity·value 는 끌 수 없다."""
    from tools.simulator.telemetry import build_i2c_record

    store = default_store()
    store.set("tx.fields_i2c", "0")
    rec = build_i2c_record(store, connector_id=10, quantity="lux",
                           seq=1, t_ms=1000, value=401.5)
    assert rec["quantity"] == "lux"
    assert rec["value"] == 401.5
    assert "connector_id" not in rec, "connector_id 는 i2c 마스크 비트라 꺼지면 빠진다"


def test_field_on_ignores_a_bit_not_applicable_to_the_kind():
    """🔴 [신규, 2026-08-19] `_field_on()` 을 직접 겨눈 단위 시험이다 —
    이름만 비교하고 kind 를 안 보면, 해당 없는 비트가 마스크에 서 있어도
    조용히 새는 방어선 없는 상태가 된다(mk_telem.c field_on() 과 같은
    계약). 지금 각 build_*_record 는 애초에 자기 kind 밖의 이름을 묻지
    않으므로 레코드 조립 경로로는 이 분기가 드러나지 않는다 — 그래서
    함수를 직접 불러 계약 자체를 겨눈다."""
    from tools.simulator.config_store import FIELD_BITS
    from tools.simulator.telemetry import _field_on

    raw_bit = next(bit for bit, name, _d, _l, _r in FIELD_BITS if name == "raw")
    mask = 1 << raw_bit
    assert _field_on(mask, "raw", "ain") is True
    assert _field_on(mask, "raw", "i2c") is False, \
        "raw 는 ain 전용이다 — i2c 마스크에 비트가 서 있어도 무시해야 한다"
    assert _field_on(mask, "raw", "din") is False


def test_i2c_field_mask_is_independent_of_ain_and_din():
    """🔴 [신규, 2026-08-19] `ain` 의 마스크를 다 꺼도 `i2c` 의 기본(status·
    connector_id 켜짐)은 그대로다 — 셋이 나뉜 핵심 계약."""
    from tools.simulator.telemetry import build_i2c_record

    store = default_store()
    store.set("tx.fields_ain", "0")
    store.set("tx.fields_din", "0")
    rec = build_i2c_record(store, connector_id=10, quantity="lux",
                           seq=1, t_ms=1000, value=401.5, status=0)
    assert rec["connector_id"] == 10, "i2c 마스크는 그대로 status·connector_id 를 켠 채다"
    assert rec["status"] == 0


# ---- din (규격 §7.6) --------------------------------------------------------


def test_din_record_has_mandatory_and_din_fields():
    from tools.simulator.telemetry import build_din_record

    store = default_store()
    rec = build_din_record(store, connector_id=18, state=1, seq=5, t_ms=1000)
    assert MANDATORY <= set(rec)
    assert rec["type"] == "din"
    assert rec["connector_id"] == 18
    assert rec["state"] == 1


def test_din_connector_id_and_state_survive_a_zero_mask():
    """🔴 규격 §7.6 — 둘은 끌 수 없다. 빠지면 레코드가 아무 말도 안 한다."""
    from tools.simulator.telemetry import build_din_record

    store = default_store()
    store.set("tx.fields_din", "0")
    rec = build_din_record(store, connector_id=19, state=0, seq=1, t_ms=1000)
    assert rec["connector_id"] == 19
    assert rec["state"] == 0


def test_din_field_mask_is_independent_of_ain_and_i2c():
    """🔴 [개정, 2026-08-19] 마스크는 `tx.fields_din` 으로 `ain`·`i2c` 와
    독립이다 — device_id 비트를 켜고 꺼 봐도 그 사이 `ain`·`i2c` 마스크는
    안 건드린다."""
    from tools.simulator.telemetry import build_din_record

    store = default_store()
    ain_before = store.field_mask("ain")
    i2c_before = store.field_mask("i2c")

    store.set("tx.fields_din", str(store.field_mask("din") | (1 << 0)))   # device_id
    rec = build_din_record(store, connector_id=18, state=1, seq=1, t_ms=1000)
    assert "device_id" in rec
    assert store.field_mask("ain") == ain_before
    assert store.field_mask("i2c") == i2c_before

    store.set("tx.fields_din", "0")
    rec = build_din_record(store, connector_id=18, state=1, seq=1, t_ms=1000)
    assert "device_id" not in rec


def test_din_render_is_a_single_compact_line():
    from tools.simulator.telemetry import build_din_record

    store = default_store()
    line = render(build_din_record(store, connector_id=20, state=1,
                                   seq=1, t_ms=1000))
    assert "\n" not in line
    assert ", " not in line and ": " not in line
    assert json.loads(line)["type"] == "din"
