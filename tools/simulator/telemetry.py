"""텔레메트리 레코드 생성과 필드 마스크 적용.

전기적 근거 (데이터시트 §5.3):
  4~20 mA 루프 전류 → 120 Ω 0.1% 션트 → 전압
  4 mA = 0.48 V, 20 mA = 2.40 V
  ADS1256 외부 기준 2.5 V (ADR4525), 단일단 측정, PGA=1

raw 가 원본이다. ma·value 는 편의용 파생값이며 tx.float_digits 자릿수로
반올림된다. Q2 serializer 가 소수점 2자리로 고정돼 24비트 분해능을 버렸던
문제(스펙 §5.7)를 되풀이하지 않기 위해 raw 는 절대 반올림하지 않는다.
"""

import json

from tools.simulator.config_store import FIELD_BITS, ConfigStore
from host.core.records import SCHEMA_VER

#: ADS1256 은 24비트 양방향. 단일단 양의 전 범위 코드.
ADS1256_FULL_SCALE = (1 << 23) - 1

SHUNT_OHMS = 120.0
VREF_V = 2.5

#: 🔴 만재 입력은 VREF 가 아니라 **2·VREF** 다 (PGA=1).
#:
#:     ADS1256.pdf p.11: "full-scale input range is ±2VREF (for PGA = 1)"
#:     ADS1256.pdf p.23: LSB = 2VREF/(PGA(2^23 − 1))
#:
#: VREF 로 두면 모든 값이 정확히 절반이 된다. 실기기에서 4 mA 신호가
#: 1.99 mA 로 보였다 [실증 2026-08-17]. 배수가 딱 2 라 눈치채기 어렵고,
#: 시뮬레이터도 같은 식이면 대조로도 안 걸린다 — 실제로 안 걸렸다.
FULL_SCALE_V = 2.0 * VREF_V

#: AIN0 은 J3 에 대응 (데이터시트 §5.3)
CONNECTOR_OFFSET = 3

_BIT_OF = {name: bit for bit, name, _d, _l in FIELD_BITS}


def raw_to_ma(raw: int) -> float:
    """ADS1256 원시 코드를 루프 전류(mA)로 환산한다."""
    volts = raw / ADS1256_FULL_SCALE * FULL_SCALE_V
    return volts / SHUNT_OHMS * 1000.0


def ma_to_value(ma: float, zero: float, scale: float) -> float:
    """전류를 물리량으로 환산한다."""
    return (ma - zero) * scale


def build_ain_record(
    store: ConfigStore,
    *,
    channel: int,
    seq: int,
    t_ms: int,
    raw: int,
    capture_counter: int,
) -> dict:
    """마스크에 따라 필드를 골라 담은 ain 레코드를 만든다."""
    mask = store.field_mask
    digits = int(store.get("tx.float_digits"))

    def on(name: str) -> bool:
        return bool(mask & (1 << _BIT_OF[name]))

    # 규격 §7.1 — 이 넷은 마스크와 무관하게 항상 들어간다.
    rec: dict = {
        "schema_ver": SCHEMA_VER,
        "seq": seq,
        "t": t_ms,
        "type": "ain",
    }

    if on("connector_id"):
        rec["connector_id"] = channel + CONNECTOR_OFFSET
    if on("raw"):
        rec["raw"] = int(raw)                      # 원본 — 반올림하지 않는다

    ma = raw_to_ma(raw)
    if on("ma"):
        rec["ma"] = round(ma, digits)
    if on("value"):
        zero = float(store.get(f"ain{channel}.zero"))
        scale = float(store.get(f"ain{channel}.scale"))
        rec["value"] = round(ma_to_value(ma, zero, scale), digits)
    if on("unit"):
        rec["unit"] = store.get(f"ain{channel}.unit")
    if on("status"):
        rec["status"] = 0
    if on("device_id"):
        rec["device_id"] = store.get("dev.id")
    if on("time_source"):
        rec["time_source"] = "device_clock"
    if on("time_quality"):
        rec["time_quality"] = 0
    if on("capture_counter"):
        rec["capture_counter"] = capture_counter

    return rec


def render(rec: dict) -> str:
    """레코드를 NDJSON 한 줄로 만든다 (줄바꿈 없음)."""
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


# ---- I2C 센서 (규격 §7.5) ---------------------------------------------------

#: 시뮬레이터가 포트마다 흉내 내는 센서 종류.
#:
#: 🔴 **데모 데이터다.** 실기기에서 무엇이 꽂혔는지는 펌웨어가 정하고,
#:    시뮬레이터는 화면을 확인할 수 있을 만큼만 지어낸다 — 아날로그 쪽
#:    `_synthetic_raw` 와 같은 성격이다.
#:
#: 짝 커넥터가 같은 버스라는 사실이 화면에서도 보이도록, 한 버스에 서로 다른
#: 종류를 물려 둔다(J10·J11 = I2C3).
SIM_I2C_SENSORS: dict[int, tuple[tuple[str, str], ...]] = {
    10: (("temp", "°C"), ("humidity", "%RH")),
    11: (("lux", "lx"),),
    12: (("temp_object", "°C"), ("temp_ambient", "°C")),
    13: (("temp", "°C"),),
    14: (("pressure", "hPa"),),
    15: (("lux", "lx"),),
}

#: 양마다 그럴듯한 중앙값과 진폭. (중앙, 진폭)
_I2C_SHAPE = {
    "temp": (23.0, 1.5),
    "humidity": (55.0, 8.0),
    "lux": (400.0, 350.0),
    "temp_object": (31.0, 3.0),
    "temp_ambient": (24.0, 1.0),
    "pressure": (1013.0, 4.0),
}


def synthetic_i2c_value(connector_id: int, quantity: str,
                        now_ms: int) -> float:
    """포트·양마다 위상이 다른 사인파."""
    import math

    mid, swing = _I2C_SHAPE.get(quantity, (1.0, 0.5))
    phase = now_ms / 7000.0 + connector_id * 0.9 + len(quantity) * 0.3
    return mid + swing * math.sin(phase)


def build_i2c_record(store: ConfigStore, *, connector_id: int, quantity: str,
                     unit: str, seq: int, t_ms: int,
                     value: float | None, status: int = 0) -> dict:
    """규격 §7.5 의 i2c 레코드. 마스크는 ain 과 **같은** `tx.fields` 다."""
    mask = store.field_mask
    digits = int(store.get("tx.float_digits"))

    rec: dict = {"schema_ver": SCHEMA_VER, "seq": seq, "t": t_ms,
                 "type": "i2c"}
    if mask & (1 << _BIT_OF["connector_id"]):
        rec["connector_id"] = connector_id
    # 🔴 quantity·value 는 마스크로 끌 수 없다 (규격 §7.5). 둘이 빠지면
    #    레코드가 아무 말도 안 한다.
    rec["quantity"] = quantity
    rec["value"] = None if value is None else round(value, digits)
    if mask & (1 << _BIT_OF["unit"]):
        rec["unit"] = unit
    if mask & (1 << _BIT_OF["status"]):
        rec["status"] = status
    if mask & (1 << _BIT_OF["device_id"]):
        rec["device_id"] = str(store.get("dev.id"))
    if mask & (1 << _BIT_OF["time_source"]):
        rec["time_source"] = "device_clock"
    if mask & (1 << _BIT_OF["time_quality"]):
        rec["time_quality"] = 0
    return rec
