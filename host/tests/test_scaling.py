"""전류 ↔ 물리량 환산. 규격 §7.2.1 이 정한 계약이다."""

from host.core.scaling import (
    LOOP_MAX_MA,
    LOOP_MIN_MA,
    range_of,
    value_at,
    zero_scale_for,
)


def test_loop_ends_match_the_spec():
    """규격 §7.2.1 — 루프의 양 끝은 4 mA 와 20 mA 로 고정이다."""
    assert (LOOP_MIN_MA, LOOP_MAX_MA) == (4.0, 20.0)


def test_value_follows_the_spec_formula():
    """`value = (ma - zero) * scale`"""
    assert value_at(12.0, zero=4.0, scale=9.375) == 75.0


def test_a_zero_to_one_fifty_sensor():
    """0~150 bar — 4 mA 에서 0, 20 mA 에서 150."""
    zero, scale = zero_scale_for(0.0, 150.0)
    assert (zero, scale) == (4.0, 9.375)
    assert value_at(LOOP_MIN_MA, zero, scale) == 0.0
    assert value_at(LOOP_MAX_MA, zero, scale) == 150.0


def test_a_zero_to_sixty_sensor():
    _, scale = zero_scale_for(0.0, 60.0)
    assert scale == 3.75


def test_range_and_settings_are_the_same_fact():
    """🔴 왕복해도 그대로여야 한다.

    화면은 범위를 보여 주고 보드는 영점·스케일을 들고 있다. 둘이 왕복에서
    어긋나면, 설정을 저장했다 다시 열 때마다 값이 조금씩 흘러간다.
    """
    for low, high in ((0.0, 150.0), (0.0, 60.0), (10.0, 100.0), (-40.0, 125.0)):
        zero, scale = zero_scale_for(low, high)
        assert range_of(zero, scale) == (low, high)


def test_a_sensor_that_does_not_start_at_zero():
    """🔴 4 mA 에서 0 이 아닌 센서도 있다 (예: 4 mA = 10 bar).

    영점을 4 로 못 박으면 이런 센서를 표현할 수 없다. 규격의 식은
    `zero` 가 4 밖으로 나가는 것을 허용한다.
    """
    zero, scale = zero_scale_for(10.0, 100.0)
    assert value_at(4.0, zero, scale) == 10.0
    assert value_at(20.0, zero, scale) == 100.0
    assert zero < 4.0


def test_a_reverse_acting_sensor():
    """전류가 오를수록 물리량이 내려가는 센서. 스케일이 음수가 된다."""
    zero, scale = zero_scale_for(150.0, 0.0)
    assert scale < 0
    assert value_at(4.0, zero, scale) == 150.0
    assert value_at(20.0, zero, scale) == 0.0


def test_a_flat_range_cannot_be_expressed():
    """🔴 양 끝이 같으면 기울기가 없다 — 어떤 영점·스케일로도 표현 못 한다.

    0 으로 나누다 죽는 대신 "못 한다" 를 돌려준다. 화면이 그것을 사용자에게
    말해야 하고, 조용히 1.0 같은 값을 지어내면 안 된다.
    """
    assert zero_scale_for(50.0, 50.0) is None


def test_a_zero_scale_has_no_range():
    """보드가 스케일 0 을 들고 있으면 범위를 되짚을 수 없다."""
    assert range_of(4.0, 0.0) is None


def test_imports_no_qt():
    import inspect

    import host.core.scaling as mod

    assert "PyQt" not in inspect.getsource(mod)
