import pytest

from host.gui.widgets.loop_gauge import (
    LOOP_MAX_MA,
    LOOP_MIN_MA,
    peak_hold,
    read_loop,
)


def test_four_ma_is_zero_not_absent():
    """🔴 4 mA 는 '0' 이지 '신호 없음' 이 아니다."""
    r = read_loop(4.0)
    assert r.fraction == 0.0
    assert r.broken is False


def test_twenty_ma_is_full_scale():
    r = read_loop(20.0)
    assert r.fraction == 1.0
    assert r.over is False


def test_midpoint():
    assert read_loop(12.0).fraction == pytest.approx(0.5)


def test_below_four_ma_is_a_broken_loop_not_a_measurement():
    """🔴 4–20 mA 규격이 존재하는 이유가 이것이다.

    0–20 mA 였다면 '0 을 읽었다' 와 '선이 끊어졌다' 를 구분할 수 없다.
    살아 있는 0 점을 4 mA 로 띄워 두어 그 아래는 고장이 된다.
    """
    r = read_loop(1.2)
    assert r.broken is True
    assert r.label == "루프 단선"


def test_broken_loop_reports_no_fraction():
    """단선은 측정값이 아니므로 바를 그리지 않는다."""
    assert read_loop(0.0).fraction is None


def test_slightly_below_four_is_still_broken():
    """3.9 mA 도 규격 밖이다. 경계에서 관대하게 굴지 않는다."""
    assert read_loop(3.9).broken is True


def test_above_twenty_is_over_range_not_broken():
    """과입력은 단선과 다른 고장이다."""
    r = read_loop(22.0)
    assert r.over is True
    assert r.broken is False
    assert r.label == "과입력"


def test_over_range_has_no_fraction_so_it_cannot_be_drawn_as_normal():
    """🔴 `fraction` 은 믿을 수 있는 측정일 때만 값이 있다.

    과입력에 `fraction=1.0` 을 주면 정상 만재(20.00 mA)와 데이터 형태가
    같아진다. 위젯이 `over` 를 확인하는 것을 성실함에 맡기게 되고,
    깜빡하면 고장이 건강한 만재로 보인다. 단선은 이미 이 방식으로
    보호되고 있으므로 과입력도 같은 불변식을 따른다.
    """
    r = read_loop(25.0)
    assert r.fraction is None
    assert r.bar_fraction == 1.0      # 꽉 채워 그리려면 over 를 본 뒤에


def test_full_scale_and_over_range_are_distinguishable_in_data():
    full, over = read_loop(20.0), read_loop(20.1)
    assert full.fraction == 1.0 and full.over is False
    assert over.fraction is None and over.over is True


def test_non_finite_current_is_not_a_reading():
    """🔴 NaN 은 두 범위 비교를 모두 False 로 통과한다.

    막지 않으면 '정상' 분기로 들어가 label 이 'nan mA' 가 되고,
    확신에 찬 그럴듯한 측정값처럼 보인다. 단선의 구조적 방어를 우회한다.
    """
    for bad in (float("nan"), float("inf"), float("-inf")):
        r = read_loop(bad)
        assert r.fraction is None
        assert r.broken is True
        assert r.label == "값 없음"


def test_normal_reading_labels_with_two_decimals():
    assert read_loop(12.345).label == "12.35 mA"


# ------------------------------------------------------------ 피크 홀드

def test_peak_hold_reports_the_recent_maximum():
    """🔴 100 ms 마다 값이 바뀌면 순간 최대값은 사람 눈에 안 보인다.

    계기가 오래 전에 푼 문제다 — 피크 미터는 최고값을 잠깐 붙들어 둔다.
    표식 하나가 바에서 가장 값싸고 가장 계기다운 장치다.
    """
    assert peak_hold([10.0, 18.0, 11.0], window=3) == 18.0


def test_peak_hold_forgets_what_left_the_window():
    """🔴 붙들어 두되 **놓아준다.** 안 놓으면 표식이 하루 종일 20 에 붙어
       있고, 그때부터 아무 정보도 아니다."""
    assert peak_hold([19.0, 5.0, 6.0, 7.0], window=3) == 7.0


def test_peak_hold_ignores_gaps():
    """값이 오지 않은 자리는 값이 아니다."""
    assert peak_hold([9.0, None, 12.0, None], window=4) == 12.0


def test_peak_hold_ignores_non_finite():
    """🔴 NaN 은 비교를 전부 통과해 최대값 자리를 차지한다 — read_loop 가
       막아 둔 것과 같은 함정이다."""
    assert peak_hold([9.0, float("nan"), 12.0], window=3) == 12.0
    assert peak_hold([float("inf"), 9.0], window=2) == 9.0


def test_peak_hold_is_none_when_nothing_is_known():
    assert peak_hold([], window=5) is None
    assert peak_hold([None, None], window=5) is None


def test_loop_constants_match_the_datasheet():
    """데이터시트 §5.3 — 120Ω 션트, 4 mA = 0.48 V, 20 mA = 2.40 V."""
    assert LOOP_MIN_MA == 4.0
    assert LOOP_MAX_MA == 20.0
