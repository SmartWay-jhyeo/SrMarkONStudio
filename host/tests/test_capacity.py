import pytest

from host.core.errors import ConfigError, Reason
from tools.simulator.capacity import (
    CHANNEL_SPI_MS,
    SAFETY_MARGIN,
    available_sps,
    check_capacity,
    per_sample_ms,
    required_sps,
    settling_ms,
)
from tools.simulator.config_store import default_store


def _force(store, key, value):
    """검증을 건너뛰고 값을 직접 밀어 넣는다.

    Step 4 이후 ConfigStore.set() 은 스스로 용량을 검사하므로, 초과 상태를
    set() 으로는 만들 수 없다. check_capacity() 자체를 시험하려면 저장소를
    우회해 초과 상태를 구성해야 한다.
    """
    store.items[key].current = value


#: 경계 시험이 쓸 DRATE. 여기서 7채널×10ms(=700 SPS)가 가용을 넘고
#: 한 채널 적으면 들어간다 — 경계가 채널 하나 폭으로 걸린다.
_EDGE_DRATE = 1000


def _channels_that_fit(drate, period_ms):
    """이 조건에서 가용 안에 들어가는 최대 채널 수.

    🔴 숫자를 시험에 박지 않는다. 처음에는 "DRATE 2000 기준 가용 약 533
       SPS" 를 주석과 조건에 박아 두었는데, 그 533 은 정착시간을 1.0 ms 로
       **어림잡았을 때**의 값이었다. 데이터시트(ADS1256.pdf p.20 Table 13)로
       0.18 ms 를 확정하자 가용이 1176 SPS 로 뛰었고, 초과를 기대한 시험
       네 개가 한꺼번에 통과해 버렸다.

       시험이 확인해야 할 것은 "700 이 533 보다 크다" 가 아니라 **넘으면
       거부하고 안 넘으면 통과한다** 는 규칙이다. 그러니 경계는 계산해서
       가져오고, 시험은 그 양쪽을 짚는다.
    """
    available = available_sps(drate) * SAFETY_MARGIN
    per_channel = 1000.0 / period_ms
    return int(available // per_channel)


def _load_channels(store, *, count, period_ms):
    """count 개 채널을 period_ms 주기로 켠 상태를 검증 없이 구성한다.

    ⚠️ 꺼진 채널의 주기는 건드리지 않는다. 건드리면 나중에 그 채널을 켤 때
    기본 주기(100ms)가 아니라 period_ms 로 켜져서, "채널 하나 추가" 의
    부하 증가량이 시험이 의도한 값과 달라진다.
    """
    for ch in range(7):
        enabled = ch < count
        _force(store, f"ain{ch}.enabled", enabled)
        if enabled:
            _force(store, f"ain{ch}.period_ms", period_ms)


# --------------------------------------------------------------- 계산 함수
def test_required_sps_counts_only_enabled_channels():
    store = default_store()                    # 기본은 ain0 만 enabled, 100ms
    assert required_sps(store) == pytest.approx(10.0)


def test_required_sps_sums_across_channels():
    store = default_store()
    _force(store, "ain1.enabled", True)
    _force(store, "ain1.period_ms", 50)
    assert required_sps(store) == pytest.approx(10.0 + 20.0)


def test_required_sps_ignores_disabled_channels():
    store = default_store()
    _force(store, "ain1.enabled", False)
    _force(store, "ain1.period_ms", 10)        # 꺼져 있으면 세지 않는다
    assert required_sps(store) == pytest.approx(10.0)


def test_available_sps_accounts_for_settling_time():
    """가용률은 DRATE 만이 아니라 채널 전환 정착시간에도 구속된다.

    정착시간이 0 이면 available == DRATE 여야 하지만, 실제로는 채널 전환
    비용이 있으므로 반드시 DRATE 보다 작다.
    """
    assert available_sps(1000) < 1000.0
    assert available_sps(2000) > available_sps(1000)   # 빠를수록 여유가 는다


# --------------------------------------------------------------- 판정
def test_default_config_is_within_capacity():
    check_capacity(default_store())            # 예외가 없어야 한다


def test_the_edge_drate_really_puts_the_boundary_inside_the_channel_count():
    """아래 경계 시험들이 딛고 서는 전제를 먼저 못박는다.

    🔴 이 시험이 없으면, 정착시간이 다시 바뀌어 경계가 채널 수 밖으로
       나갔을 때 아래 시험들이 조용히 무의미해진다 — 전부 통과하지만
       아무것도 확인하지 않는 상태가 된다. 실제로 그렇게 됐었다.

    경계가 1..6 안에 있어야 "하나 더 켜면 넘친다"(fits+1 ≤ 7)를 실제로
    구성할 수 있다.

    🔴 [개정 2026-08-19] 예전에는 `== 6` 이었다. SPI 왕복(155 µs)을 모델에
       넣으면서 같은 DRATE 1000 에서 경계가 5 로 내려갔다 — 시험이 지키려는
       것은 "경계가 6이다" 가 아니라 **"경계가 있고 양쪽을 짚을 수 있다"**
       이므로 범위로 바꾼다.
    """
    fits = _channels_that_fit(_EDGE_DRATE, 10)
    assert 1 <= fits < 7, (
        f"DRATE {_EDGE_DRATE}, 10 ms 에서 들어가는 채널이 {fits} 개다. "
        "경계가 1..6 안에 있어야 아래 시험들이 뜻을 가진다 — 정착시간이나 "
        "안전여유가 바뀌었으면 _EDGE_DRATE 를 다시 고른다"
    )


def test_channels_just_over_the_edge_exceed_capacity():
    store = default_store()
    _force(store, "adc.drate", _EDGE_DRATE)
    _load_channels(store, count=_channels_that_fit(_EDGE_DRATE, 10) + 1,
                   period_ms=10)
    with pytest.raises(ConfigError) as exc:
        check_capacity(store)
    assert exc.value.reason == Reason.CAPACITY


def test_channels_just_under_the_edge_are_within_capacity():
    """경계가 실제로 있다 — 한 채널 차이로 갈린다."""
    store = default_store()
    _force(store, "adc.drate", _EDGE_DRATE)
    _load_channels(store, count=_channels_that_fit(_EDGE_DRATE, 10),
                   period_ms=10)
    check_capacity(store)                      # 예외가 없어야 한다


def test_capacity_error_reports_required_and_available():
    """사용자가 무엇을 줄여야 하는지 알아야 하므로 두 값을 모두 담는다."""
    store = default_store()
    _force(store, "adc.drate", _EDGE_DRATE)
    _load_channels(store, count=_channels_that_fit(_EDGE_DRATE, 10) + 1,
                   period_ms=10)
    with pytest.raises(ConfigError) as exc:
        check_capacity(store)
    assert "요구" in exc.value.detail
    assert "가용" in exc.value.detail


#: 🔴 ADS1256.pdf, p.20, Table 13 "Settling Time vs Data Rate" (t18, ms).
#:    fCLKIN = 7.68 MHz — 이 보드의 크리스털과 같다.
DATASHEET_T18_MS = {
    30000: 0.21, 15000: 0.25, 7500: 0.31, 3750: 0.44, 2000: 0.68,
    1000: 1.18, 500: 2.18, 100: 10.18, 60: 16.84, 50: 20.18,
    30: 33.51, 25: 40.18, 15: 66.84, 10: 100.18, 5: 200.18,
}


def test_settling_time_matches_the_datasheet_table():
    """정착시간 계산이 데이터시트 표 그대로인가.

    표 전체가 `t18 = 1/DRATE + 0.18 ms` 에 맞는다. 이 값이 용량 판정
    전체의 바닥이므로, 누가 SETTLING_MS 를 다시 어림값으로 되돌리면
    여기서 걸린다.
    """
    for drate, t18_ms in DATASHEET_T18_MS.items():
        assert settling_ms(drate) == pytest.approx(t18_ms, abs=0.01), (
            f"DRATE {drate}: 데이터시트 {t18_ms} ms, "
            f"계산 {settling_ms(drate):.3f} ms"
        )


def test_the_spi_round_trip_is_counted_on_top_of_the_settling_time():
    """🔴 표본 하나의 비용은 t18 만이 아니다.

    채널을 바꾸려면 WREG MUX → SYNC → WAKEUP 을 보내고, 값을 가져오려면
    RDATA 와 3바이트를 더 읽어야 한다. 500 kHz SCLK 에서 155 µs 다
    (capacity.CHANNEL_SPI_MS 에 내역).

    60 SPS(t18 16.84 ms)에서는 1 % 도 안 되어 예전 모델이 무시했지만,
    채널당 10 ms 를 노리며 DRATE 를 1000 이상으로 올리면 예산의 12 % 를
    넘는다. 빼놓고 계산하면 "된다" 고 말해 놓고 실기기에서 주기를 못 맞춘다
    — 이 시험이 그 회귀를 막는다.

    🔴 기대값을 `CHANNEL_SPI_MS` 로 적지 않는다. 그러면 상수를 0 으로
       되돌려도 양변이 함께 0 이 되어 시험이 통과한다 — 처음 쓴 판이 실제로
       그랬고, 되돌림 검사에서 드러났다. 그래서 하드웨어 사실에서 다시
       계산해 맞춘다.
    """
    # firmware/stage1/bsp/mk_ads_io.c: SCLK = MK_SPI4_KERNEL_HZ(64 MHz) / 128
    sclk_hz = 64_000_000 / 128
    us_per_byte = 8 / sclk_hz * 1e6                     # = 16 µs
    # WREG MUX 3 + SYNC 1 + WAKEUP 1 + RDATA 1 + 데이터 3
    bytes_per_sample = 3 + 1 + 1 + 1 + 3
    # app/mk_ads1256.h: MK_ADS_T11_SYNC_US(4) + MK_ADS_T6_US(7)
    idle_us = 4 + 7
    expect_ms = (bytes_per_sample * us_per_byte + idle_us) / 1000.0

    assert CHANNEL_SPI_MS == pytest.approx(expect_ms, abs=0.005), (
        f"SPI 왕복 {CHANNEL_SPI_MS} ms 가 배선 사실과 안 맞는다 "
        f"(SCLK 500 kHz, {bytes_per_sample} B + {idle_us} µs "
        f"→ {expect_ms:.3f} ms)"
    )
    for drate in (1000, 2000, 7500):
        assert per_sample_ms(drate) == pytest.approx(
            DATASHEET_T18_MS[drate] + expect_ms, abs=0.01
        )
    # 느린 쪽에서는 여전히 무시할 만하다 — 모델이 과하게 비관적이지 않다.
    assert expect_ms / DATASHEET_T18_MS[60] < 0.01


def test_seven_channels_at_ten_milliseconds_need_at_least_2000_sps():
    """🔴 이 작업의 결론을 숫자로 못박는다.

    7채널 × 10 ms = 700 SPS 를 요구한다. 카탈로그의 DRATE 중 그것을
    (안전여유까지 포함해) 감당하는 가장 느린 값이 2000 SPS 다 —
    1000 SPS 는 t18 1.18 ms + SPI 0.155 ms = 1.335 ms/표본이라 한 바퀴가
    9.34 ms 이고, 여유 없이 딱 붙어 실기기에서 지킬 수 없다.

    잡음은 그 대가다. Table 6(버퍼 오프, PGA=1)의 noise-free 분해능이
    60 SPS 21.2 비트 → 2000 SPS 18.5 비트로 떨어진다. 4~20 mA 환산으로는
    약 0.01 mA(16 mA 폭의 0.06 %)이고, 이 보드에서 실측된 60 SPS 편차
    1,449 카운트(≈0.0072 mA)와 같은 자릿수다 — ADS1256 자신의 잡음이
    아니라 바깥에서 들어오는 것이 이미 지배적이라는 뜻이다.
    """
    demand = 7 * (1000.0 / 10.0)
    assert available_sps(1000) * SAFETY_MARGIN < demand
    assert available_sps(2000) * SAFETY_MARGIN >= demand


def test_safety_margin_is_applied():
    """여유 계수가 실제로 판정을 좁힌다."""
    assert 0.0 < SAFETY_MARGIN <= 1.0


# --------------------------------------------------------------- 저장소 연동
def test_store_set_rejects_change_that_breaks_capacity():
    """저장소가 스스로 막는다. 개별 값은 범위 안이어도 조합이 불가하면 거부.

    가용에 꽉 차게 채운 상태에서 다음 채널을 10 ms 로 올리면 초과한다.
    채널을 켜는 것(+10 SPS, 주기 100 ms)까지는 아직 들어간다.
    """
    store = default_store()
    _force(store, "adc.drate", _EDGE_DRATE)
    fits = _channels_that_fit(_EDGE_DRATE, 10)
    _load_channels(store, count=fits, period_ms=10)

    nxt = f"ain{fits}"
    store.set(f"{nxt}.enabled", "true")        # +10 SPS (주기 100ms) — 통과
    with pytest.raises(ConfigError) as exc:
        store.set(f"{nxt}.period_ms", "10")    # +90 SPS — 초과
    assert exc.value.reason == Reason.CAPACITY


def test_rejected_change_is_rolled_back():
    """거부된 설정이 저장소에 남으면 안 된다."""
    store = default_store()
    _force(store, "adc.drate", _EDGE_DRATE)
    fits = _channels_that_fit(_EDGE_DRATE, 10)
    _load_channels(store, count=fits, period_ms=10)
    nxt = f"ain{fits}"
    store.set(f"{nxt}.enabled", "true")

    with pytest.raises(ConfigError):
        store.set(f"{nxt}.period_ms", "10")
    assert store.get(f"{nxt}.period_ms") == 100  # 이전 값 그대로


def test_lowering_drate_cannot_bypass_the_capacity_check():
    """🔴 drate 는 수요가 아니라 **공급**이다.

    `required_sps` 만 비교하면 drate 를 낮추는 변경이 "부하가 안 늘었다"로
    통과해 용량 검사가 통째로 무력화된다. 사용자가 가장 흔히 만지는 노브다.
    """
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)   # 700 SPS, 가용 533 → 이미 초과
    with pytest.raises(ConfigError) as exc:
        store.set("adc.drate", "30")               # 공급을 더 줄인다
    assert exc.value.reason == Reason.CAPACITY
    assert store.get("adc.drate") == 2000          # 롤백


def test_raising_drate_is_always_allowed():
    """공급을 늘리는 변경은 초과 상태에서도 통과해야 한다 — 빠져나갈 길."""
    store = default_store()
    _force(store, "adc.drate", 30)
    _load_channels(store, count=7, period_ms=10)   # 크게 초과 상태
    store.set("adc.drate", "7500")                 # 예외 없이 통과
    assert store.get("adc.drate") == 7500


def test_disabling_a_channel_is_never_capacity_rejected():
    """부하를 줄이는 변경은 막을 이유가 없다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)   # 이미 초과 상태
    store.set("ain0.enabled", "false")             # 예외 없이 통과해야 한다
    assert store.get("ain0.enabled") is False
