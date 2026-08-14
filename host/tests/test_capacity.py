import pytest

from host.core.errors import ConfigError, Reason
from tools.simulator.capacity import (
    SAFETY_MARGIN,
    available_sps,
    check_capacity,
    required_sps,
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


def test_the_edge_drate_really_puts_the_boundary_between_6_and_7():
    """아래 경계 시험들이 딛고 서는 전제를 먼저 못박는다.

    🔴 이 시험이 없으면, 정착시간이 다시 바뀌어 경계가 채널 하나 폭을
       벗어났을 때 아래 시험들이 조용히 무의미해진다 — 전부 통과하지만
       아무것도 확인하지 않는 상태가 된다. 실제로 그렇게 됐었다.
    """
    assert _channels_that_fit(_EDGE_DRATE, 10) == 6, (
        "경계가 6채널과 7채널 사이에 있어야 한다. 정착시간이나 안전여유가 "
        "바뀌었으면 _EDGE_DRATE 를 다시 고른다"
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


def test_settling_time_matches_the_datasheet_table():
    """🔴 ADS1256.pdf, p.20, Table 13 "Settling Time vs Data Rate".

    표 전체가 `t18 = 1/DRATE + 0.18 ms` 에 맞고, available_sps() 가 계산하는
    것이 바로 그 t18 의 역수다. 이 값이 용량 판정 전체의 바닥이므로,
    누가 SETTLING_MS 를 다시 어림값으로 되돌리면 여기서 걸린다.
    """
    table_ms = {
        30000: 0.21, 15000: 0.25, 7500: 0.31, 3750: 0.44, 2000: 0.68,
        1000: 1.18, 500: 2.18, 100: 10.18, 60: 16.84, 50: 20.18,
        30: 33.51, 25: 40.18, 15: 66.84, 10: 100.18, 5: 200.18,
    }
    for drate, t18_ms in table_ms.items():
        got_ms = 1000.0 / available_sps(drate)
        assert got_ms == pytest.approx(t18_ms, abs=0.01), (
            f"DRATE {drate}: 데이터시트 {t18_ms} ms, 계산 {got_ms:.3f} ms"
        )


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
