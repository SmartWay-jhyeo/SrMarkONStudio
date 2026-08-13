import pytest

from host.gui.widgets.loop_gauge import (
    LOOP_MAX_MA,
    LOOP_MIN_MA,
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


def test_over_range_fraction_is_clamped():
    assert read_loop(25.0).fraction == 1.0


def test_normal_reading_labels_with_two_decimals():
    assert read_loop(12.345).label == "12.35 mA"


def test_loop_constants_match_the_datasheet():
    """데이터시트 §5.3 — 120Ω 션트, 4 mA = 0.48 V, 20 mA = 2.40 V."""
    assert LOOP_MIN_MA == 4.0
    assert LOOP_MAX_MA == 20.0
