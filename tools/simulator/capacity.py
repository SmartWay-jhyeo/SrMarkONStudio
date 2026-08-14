"""ADC 스케줄 가능성 검증.

개별 설정이 전부 유효 범위 안이어도 조합이 물리적으로 달성 불가능할 수 있다.
그런 설정을 수락하면 큐가 영구히 넘치고 데이터가 계속 유실된다.

ADS1256 은 멀티플렉스된 8채널 ΔΣ ADC 다. 채널을 바꿀 때마다
MUX 갱신 → SYNC → WAKEUP → 변환 완료 대기가 필요하므로, 선택한 DRATE 와
채널 전환 절차가 실제 달성 가능한 총 수집률을 구속한다.

설계 §6.4
"""

from host.core.errors import ConfigError, Reason

#: 채널 전환 정착시간 (ms) — 데이터시트로 확정. 더는 잠정값이 아니다.
#:
#: ADS1256.pdf, p.20, Table 13 "Settling Time vs Data Rate":
#:     DATA RATE 60 SPS -> SETTLING TIME (t18) 16.84 ms
#:
#: 🔴 표 16행이 전부 `t18 = 1/DRATE + 0.18 ms` 에 맞는다. 확인:
#:     30000 -> 0.21   1000 -> 1.18    60 -> 16.84    10 -> 100.18
#:     15000 -> 0.25    500 -> 2.18    50 -> 20.18     5 -> 200.18
#:      7500 -> 0.31    100 -> 10.18   30 -> 33.51   2.5 -> 400.18
#:      3750 -> 0.44                   25 -> 40.18
#:      2000 -> 0.68                   15 -> 66.84
#: 따라서 아래 available_sps() 의 `1/DRATE + SETTLING_MS` 는 t18 그 자체다.
#:
#: 처음에 1.0 ms 로 어림잡았던 것을 0.18 로 내린다 — 5.5배 비관적이었다.
#: 같은 문서 p.20: "The ADS1255/6 settles in a single cycle - there is no
#: need to ignore or discard data after synchronization." 즉 MUX 를 바꾼
#: 뒤 버려야 하는 변환이 없고, 정착은 한 주기 안에 끝난다.
SETTLING_MS = 0.18

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


def margin_sps(store) -> float:
    """실현 가능성 여유 = 요구 − 가용(안전여유 반영). 양수면 초과다.

    🔴 수요와 공급을 **한 식에** 담는 것이 요점이다. 수요만 비교하면
    `adc.drate` 를 낮추는 변경(공급 감소)이 "부하가 안 늘었다"로 통과해
    용량 검사가 통째로 무력화된다. `ConfigStore._check_combination` 주석 참조.
    """
    return required_sps(store) - available_sps(int(store.get("adc.drate"))) * SAFETY_MARGIN


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
