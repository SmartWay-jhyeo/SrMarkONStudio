"""ADC 스케줄 가능성 검증.

개별 설정이 전부 유효 범위 안이어도 조합이 물리적으로 달성 불가능할 수 있다.
그런 설정을 수락하면 큐가 영구히 넘치고 데이터가 계속 유실된다.

ADS1256 은 멀티플렉스된 8채널 ΔΣ ADC 다. 채널을 바꿀 때마다
MUX 갱신 → SYNC → WAKEUP → 변환 완료 대기가 필요하므로, 선택한 DRATE 와
채널 전환 절차가 실제 달성 가능한 총 수집률을 구속한다.

설계 §6.4
"""

from host.core.errors import ConfigError, Reason

#: 채널 전환 정착시간 (ms). 🔴 잠정값 — 실기기 실측으로 확정해야 한다.
#: 설계 §14 열린 항목 5번.
SETTLING_MS = 1.0

#: 계산값과 실제 사이의 여유. 1.0 = 여유 없음.
SAFETY_MARGIN = 0.8

AIN_CHANNELS = 7


def required_sps(store) -> float:
    """활성 채널들이 요구하는 총 샘플률 (SPS)."""
    total = 0.0
    for ch in range(AIN_CHANNELS):
        if not store.get(f"ain{ch}.enabled"):
            continue
        period_ms = float(store.get(f"ain{ch}.period_ms"))
        total += 1000.0 / period_ms
    return total


def available_sps(drate: int) -> float:
    """DRATE 와 채널 전환 정착시간으로 계산한 달성 가능 총 샘플률 (SPS)."""
    conversion_ms = 1000.0 / float(drate)
    per_sample_ms = conversion_ms + SETTLING_MS
    return 1000.0 / per_sample_ms


def check_capacity(store) -> None:
    """설정 조합이 달성 가능한지 검사한다.

    Raises:
        ConfigError: reason=CAPACITY. detail 에 요구·가용 수치를 담아
            사용자가 무엇을 줄여야 하는지 알 수 있게 한다.
    """
    required = required_sps(store)
    available = available_sps(int(store.get("adc.drate"))) * SAFETY_MARGIN

    if required > available:
        raise ConfigError(
            Reason.CAPACITY,
            f"요구 {required:.1f} SPS > 가용 {available:.1f} SPS "
            f"(DRATE {store.get('adc.drate')} SPS, 정착 {SETTLING_MS} ms). "
            f"채널을 줄이거나 주기를 늘리거나 DRATE 를 올려야 한다",
        )
