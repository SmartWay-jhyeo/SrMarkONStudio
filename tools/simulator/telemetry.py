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

_BIT_OF = {name: bit for bit, name, _d, _l, _r in FIELD_BITS}
#: 비트 이름 → 이 비트가 속한 레코드 종류들. field_on() 의 방어선이다.
_RECORDS_OF = {name: records for _b, name, _d, _l, records in FIELD_BITS}


def _field_on(mask: int, name: str, kind: str) -> bool:
    """이 필드가 `kind` 레코드의 마스크에서 켜져 있는가.

    🔴 [개정, 2026-08-19] 이름만 비교하지 않고 **이 비트가 이 레코드에
       해당하는지도 함께 본다** — 펌웨어(mk_telem.c field_on())와 같은
       방어선이다. `tx.fields` 를 셋으로 나누며 마스크만 갈아 끼우고
       이름 비교만 남겨 두면, 해당 없는 비트가 마스크에 서 있어도(예:
       손상된 저장값) 조용히 새는 자리가 남는다."""
    if kind not in _RECORDS_OF.get(name, ()):
        return False
    bit = _BIT_OF.get(name)
    if bit is None:
        return False
    return bool(mask & (1 << bit))


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
    time_source: str = "device_clock",
    time_quality: int = 0,
) -> dict:
    """마스크에 따라 필드를 골라 담은 ain 레코드를 만든다.

    `time_source`·`time_quality` 는 Phase 3 GNSS/PPS 시간축의 등급이다.
    기본값(device_clock/0)은 GNSS 가 없던 예전 동작 그대로다 —
    `DeviceSim._gnss_time_state()` 가 실제 등급을 계산해 넘긴다."""
    mask = store.field_mask("ain")
    digits = int(store.get("tx.float_digits"))

    def on(name: str) -> bool:
        return _field_on(mask, name, "ain")

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
    # 🔴 [판단, 2026-08-19] time_source 는 마스크로 못 끈다 — config_store.py
    #    FIELD_BITS 주석과 같은 근거.
    rec["time_source"] = time_source
    if on("time_quality"):
        rec["time_quality"] = time_quality
    if on("capture_counter"):
        rec["capture_counter"] = capture_counter

    return rec


def render(rec: dict) -> str:
    """레코드를 NDJSON 한 줄로 만든다 (줄바꿈 없음)."""
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


# ---- I2C 센서 (규격 §7.5) ---------------------------------------------------

#: 양마다 그럴듯한 중앙값과 진폭. (중앙, 진폭)
#:
#: 🔴 값 자체는 지어낸 것이다 — 아날로그 쪽 `_synthetic_raw` 와 같은 성격이고,
#:    화면을 확인할 수 있을 만큼만 그럴듯하면 된다. **무엇을 내는지**는
#:    지어내지 않는다: 사용자가 `i2cN.kind` 로 고른 종류가 정한다.
_I2C_SHAPE = {
    "temp": (23.0, 1.5),
    "humidity": (55.0, 8.0),
    "lux": (400.0, 350.0),
    "temp_object": (31.0, 3.0),
}


def synthetic_i2c_value(connector_id: int, quantity: str,
                        now_ms: int) -> float:
    """포트·양마다 위상이 다른 사인파."""
    import math

    mid, swing = _I2C_SHAPE.get(quantity, (1.0, 0.5))
    phase = now_ms / 7000.0 + connector_id * 0.9 + len(quantity) * 0.3
    return mid + swing * math.sin(phase)


def build_i2c_record(store: ConfigStore, *, connector_id: int, quantity: str,
                     seq: int, t_ms: int,
                     value: float | None, status: int = 0,
                     time_source: str = "device_clock",
                     time_quality: int = 0) -> dict:
    """규격 §7.5 의 i2c 레코드.

    🔴 [개정, 2026-08-19] 마스크는 `tx.fields_i2c` 로 `ain`·`din` 과 독립이다.

    status: 0=정상 · 1=응답 없음 · 2=데이터 오류 · 3=지원하지 않는 종류
    """
    mask = store.field_mask("i2c")
    digits = int(store.get("tx.float_digits"))

    rec: dict = {"schema_ver": SCHEMA_VER, "seq": seq, "t": t_ms,
                 "type": "i2c"}
    if _field_on(mask, "connector_id", "i2c"):
        rec["connector_id"] = connector_id
    # 🔴 quantity·value 는 마스크로 끌 수 없다 (규격 §7.5). 둘이 빠지면
    #    레코드가 아무 말도 안 한다.
    rec["quantity"] = quantity
    rec["value"] = None if value is None else round(value, digits)
    # 🔴 `unit` 을 싣지 않는다 (규격 §7.5) — quantity 가 이미 정한다.
    if _field_on(mask, "status", "i2c"):
        rec["status"] = status
    if _field_on(mask, "device_id", "i2c"):
        rec["device_id"] = str(store.get("dev.id"))
    # 🔴 time_source 는 마스크로 못 끈다 — build_ain_record 와 같은 근거.
    rec["time_source"] = time_source
    if _field_on(mask, "time_quality", "i2c"):
        rec["time_quality"] = time_quality
    return rec


# ---- 디지털 입력 J18~J20 (규격 §7.6) ----------------------------------------


def build_din_record(store: ConfigStore, *, connector_id: int, state: int,
                     seq: int, t_ms: int,
                     time_source: str = "device_clock",
                     time_quality: int = 0) -> dict:
    """규격 §7.6 의 din 레코드.

    🔴 [개정, 2026-08-19] 마스크는 `tx.fields_din` 으로 `ain`·`i2c` 와 독립이다.

    🔴 `connector_id`·`state` 는 마스크로 끌 수 없다 — 둘이 빠지면 레코드가
       아무 말도 안 한다(i2c 의 quantity·value 와 같은 이유).

    🔴 극성 반전은 여기서 하지 않는다. 이 함수를 부르는 쪽(`DeviceSim`)이
       옵토의 로우 액티브를 이미 뒤집어 `state` 를 건넨다 — "1 = 켜짐" 이
       전선에 나가는 유일한 뜻이다.
    """
    mask = store.field_mask("din")
    rec: dict = {"schema_ver": SCHEMA_VER, "seq": seq, "t": t_ms, "type": "din"}
    rec["connector_id"] = connector_id
    rec["state"] = state
    if _field_on(mask, "device_id", "din"):
        rec["device_id"] = str(store.get("dev.id"))
    # 🔴 time_source 는 마스크로 못 끈다 — build_ain_record 와 같은 근거.
    rec["time_source"] = time_source
    if _field_on(mask, "time_quality", "din"):
        rec["time_quality"] = time_quality
    return rec
