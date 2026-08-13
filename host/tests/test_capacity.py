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


def test_seven_channels_at_10ms_exceeds_capacity():
    """DRATE 2000 SPS 기준 가용 약 533 SPS 인데 7채널×100 SPS = 700 SPS."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)
    with pytest.raises(ConfigError) as exc:
        check_capacity(store)
    assert exc.value.reason == Reason.CAPACITY


def test_five_channels_at_10ms_is_within_capacity():
    """같은 조건에서 5채널 = 500 SPS 는 가용 안이다. 경계가 실제로 있다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=5, period_ms=10)
    check_capacity(store)                      # 예외가 없어야 한다


def test_capacity_error_reports_required_and_available():
    """사용자가 무엇을 줄여야 하는지 알아야 하므로 두 값을 모두 담는다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)
    with pytest.raises(ConfigError) as exc:
        check_capacity(store)
    assert "요구" in exc.value.detail
    assert "가용" in exc.value.detail


def test_safety_margin_is_applied():
    """여유 계수가 실제로 판정을 좁힌다."""
    assert 0.0 < SAFETY_MARGIN <= 1.0


# --------------------------------------------------------------- 저장소 연동
def test_store_set_rejects_change_that_breaks_capacity():
    """저장소가 스스로 막는다. 개별 값은 범위 안이어도 조합이 불가하면 거부.

    5채널×10ms = 500 SPS 로 가용(약 533) 안에 있는 상태에서, 6번째 채널을
    10ms 로 올리면 600 SPS 가 되어 초과한다.
    """
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=5, period_ms=10)

    store.set("ain5.enabled", "true")          # +10 SPS (주기 100ms) — 통과
    with pytest.raises(ConfigError) as exc:
        store.set("ain5.period_ms", "10")      # +90 SPS — 초과
    assert exc.value.reason == Reason.CAPACITY


def test_rejected_change_is_rolled_back():
    """거부된 설정이 저장소에 남으면 안 된다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=5, period_ms=10)
    store.set("ain5.enabled", "true")

    with pytest.raises(ConfigError):
        store.set("ain5.period_ms", "10")
    assert store.get("ain5.period_ms") == 100  # 이전 값 그대로


def test_disabling_a_channel_is_never_capacity_rejected():
    """부하를 줄이는 변경은 막을 이유가 없다."""
    store = default_store()
    _force(store, "adc.drate", 2000)
    _load_channels(store, count=7, period_ms=10)   # 이미 초과 상태
    store.set("ain0.enabled", "false")             # 예외 없이 통과해야 한다
    assert store.get("ain0.enabled") is False
