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
    store.set("tx.fields", "0")
    rec = _rec(store)
    assert MANDATORY <= set(rec)


def test_default_mask_includes_raw_and_excludes_device_id():
    store = default_store()
    rec = _rec(store)
    assert "raw" in rec
    assert "device_id" not in rec


def test_enabling_device_id_bit_adds_field():
    store = default_store()
    store.set("tx.fields", str(store.field_mask | (1 << 0)))
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
    store.set("tx.fields", "0")
    minimal = len(render(_rec(store)))
    assert minimal < full
