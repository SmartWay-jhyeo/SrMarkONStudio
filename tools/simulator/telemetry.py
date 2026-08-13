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

#: AIN0 은 J3 에 대응 (데이터시트 §5.3)
CONNECTOR_OFFSET = 3

_BIT_OF = {name: bit for bit, name, _d, _l in FIELD_BITS}


def raw_to_ma(raw: int) -> float:
    """ADS1256 원시 코드를 루프 전류(mA)로 환산한다."""
    volts = raw / ADS1256_FULL_SCALE * VREF_V
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
