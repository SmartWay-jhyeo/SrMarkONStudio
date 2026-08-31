"""링크 속도를 바꾸는 화면의 말과 그 배선 (규격 §4.2).

🔴 여기서 지키는 것은 문구가 아니라 **약속**이다.

   - 921600 보다 높은 값을 "빠르다" 고만 말하지 않는다. 아무도 시험한 적이
     없고(규격 §4.2.5), 그것을 안 말하면 사용자는 그냥 제일 큰 값을 고른다.
   - 실패했을 때 무슨 일이 있었는지 말한다. "실패" 세 글자로 끝내면 사람이
     할 수 있는 판단이 "전원을 뽑는다" 밖에 없다 — 10초만 기다리면 저절로
     살아나는데도.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from host.core.limits import DEFAULT_BAUD, LINK_BAUD_CHOICES, LINK_BAUD_KEY
from host.gui import link_baud


@dataclass(frozen=True)
class FakeResult:
    """`BoardService.change_baud` 가 돌려주는 것과 같은 모양."""

    ok: bool
    baud: int
    stage: str
    reason: str = ""
    error: str = ""
    recovered: bool = False


def test_every_catalog_choice_has_a_label():
    for baud in LINK_BAUD_CHOICES:
        opt = link_baud.option(baud)
        assert opt is not None, f"{baud} 에 꼬리표가 없다"
        assert str(baud)[:3] in opt.label.replace(",", "")


def test_only_the_default_is_marked_verified():
    """🔴 규격 §4.2.5 — 실기기에서 확인된 것은 기본값 하나뿐이다.

    이 시험은 "빨라 보이니 확인된 것으로 하자" 를 막는 자리다. 다른 값을
    확인됨으로 바꾸려면 실기기 근거를 먼저 만들어야 한다.
    """
    verified = [o.baud for o in link_baud.OPTIONS if o.verified]
    assert verified == [DEFAULT_BAUD]


def test_faster_choices_say_they_are_unverified():
    for opt in link_baud.OPTIONS:
        if opt.baud > DEFAULT_BAUD:
            assert "미확인" in opt.label
            assert "확인된 적이 없다" in opt.note


def test_the_choice_that_enables_ten_millisecond_sampling_is_called_out():
    """7채널 × 10 ms 가 이 작업의 목적이다 — 어느 값부터 되는지 말해 준다."""
    opt = link_baud.option(link_baud.TEN_MS_SEVEN_CHANNELS_BAUD)
    assert opt is not None
    assert "10 ms" in opt.label


def test_is_link_baud_matches_the_spec_key():
    assert link_baud.is_link_baud(LINK_BAUD_KEY)
    assert not link_baud.is_link_baud("gnss.baud")
    assert not link_baud.is_link_baud("adc.drate")


def test_confirm_text_says_what_happens_and_how_long_to_wait():
    """🔴 셋을 다 말해야 한다 — 무엇이 일어나는지, 실패하면 어떻게 되는지,
    얼마나 기다려야 하는지. 하나라도 빠지면 사용자는 전원을 뽑는다."""
    text = link_baud.confirm_text(921600, 1500000)
    assert "921,600" in text and "1,500,000" in text
    assert f"{link_baud.CONFIRM_S}초" in text
    assert "스스로 옛 속도로 돌아온다" in text
    assert "전원을 뽑지 말고" in text
    assert "저장되지 않" in text


def test_confirm_text_warns_harder_for_unverified_speeds():
    risky = link_baud.confirm_text(921600, 2000000)
    safe = link_baud.confirm_text(1500000, 921600)
    assert "확인된 적이 없다" in risky
    assert "2 % 유실" in risky
    assert "확인된 적이 없다" not in safe


def test_success_text_reminds_that_it_is_not_saved_yet():
    """🔴 확정과 저장은 다른 일이다. 여기서 그만두면 다음 부팅에 옛 속도다."""
    text, bad = link_baud.outcome_text(
        FakeResult(True, 1500000, "confirmed", recovered=True))
    assert not bad
    assert "1,500,000" in text and "저장" in text


def test_failure_text_says_the_board_came_back():
    text, bad = link_baud.outcome_text(
        FakeResult(False, 921600, "confirm", recovered=True))
    assert bad
    assert "말이 되지 않았다" in text
    assert "돌아왔다" in text


def test_failure_without_recovery_tells_the_user_to_touch_the_board():
    """🔴 이때만은 사람이 손을 대야 한다 — 그 사실을 흐리면 안 된다."""
    text, bad = link_baud.outcome_text(
        FakeResult(False, 921600, "confirm", recovered=False))
    assert bad
    assert "전원" in text


@pytest.mark.parametrize("stage,reason,expect", [
    ("set", "MODE", "RUN"),
    ("set", "RANGE", "낼 수 없다"),
    ("confirm", "", "낮은 값"),
    ("reopen", "", "다른 프로그램"),
])
def test_failure_hint_points_at_the_next_thing_to_try(stage, reason, expect):
    hint = link_baud.failure_hint(FakeResult(False, 921600, stage, reason=reason))
    assert expect in hint


def test_a_successful_change_has_no_hint():
    assert link_baud.failure_hint(FakeResult(True, 1500000, "confirmed")) == ""
