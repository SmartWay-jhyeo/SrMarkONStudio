import pytest

from host.gui.theme import Color
from host.gui.widgets.status_chip import Level, Verification, chip_style


def test_verified_state_is_filled():
    s = chip_style(Level.OK, Verification.VERIFIED)
    assert s.filled is True
    assert s.fill == Color.VERIFIED


def test_commanded_state_is_outlined_not_filled():
    """🔴 피드백 회로가 없는 전원 레일이 여기 해당한다.

    명령이 나갔다는 것과 실제로 켜졌다는 것은 다르다. 채워서 보여주면
    사용자가 확인된 것으로 오해한다 (스펙 §2.5).
    """
    s = chip_style(Level.OK, Verification.COMMANDED)
    assert s.filled is False
    assert s.border == Color.VERIFIED


def test_unknown_state_uses_gray_regardless_of_level():
    """확인 불가는 심각도를 주장하지 않는다."""
    for lvl in (Level.OK, Level.WARN, Level.FAULT):
        s = chip_style(lvl, Verification.UNKNOWN)
        assert s.border == Color.UNKNOWN


def test_fault_is_filled_even_when_commanded():
    """오류는 확인이 필요 없다 — 이미 일어난 일이다."""
    s = chip_style(Level.FAULT, Verification.COMMANDED)
    assert s.filled is True
    assert s.fill == Color.FAULT


def test_every_level_maps_to_a_distinct_color():
    seen = {chip_style(l, Verification.VERIFIED).fill for l in Level}
    assert len(seen) == len(list(Level))


def test_text_color_is_legible_on_the_chip():
    """채운 칩 위의 글자가 읽혀야 한다."""
    from host.gui.theme import contrast_ratio

    for lvl in Level:
        s = chip_style(lvl, Verification.VERIFIED)
        assert contrast_ratio(s.text, s.fill) >= 4.0, lvl


def test_commanded_label_differs_from_verified_label():
    """같은 상태라도 문구가 달라야 한다."""
    from host.gui.widgets.status_chip import rail_label

    assert rail_label(True, Verification.COMMANDED) == "ON 명령됨"
    assert rail_label(True, Verification.VERIFIED) == "정상 ON"
    assert rail_label(False, Verification.COMMANDED) == "OFF 명령됨"
