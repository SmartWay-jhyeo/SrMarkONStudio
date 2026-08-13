"""확인이 끊겼을 때 "마지막으로 알던 것" 을 지키는 시험. Qt 없이 돈다."""

import pytest

from host.gui.last_known import (
    ChipState,
    LastKnown,
    StateHistory,
    build_chip_state,
    format_age,
)
from host.gui.widgets.status_chip import Level, Verification, chip_style


# --------------------------------------------------------------- 이 모듈의 이유

def test_never_known_and_was_faulted_are_different():
    """🔴 이 시험이 이 모듈의 존재 이유다.

    `chip_style` 은 둘 다 회색 테두리를 낸다 — 확인 못 하는 것이 심각도를
    주장하면 안 되기 때문이고, 그 자체는 맞다. 그래서 화면이 두 상황을
    구분하려면 **다른 곳** 에 정보가 있어야 한다.

    24V 가 걸린 벤치에서 "고장이었는데 지금 연락이 안 된다" 와 "원래
    모른다" 가 같은 화면이면 안 된다. 앞의 것은 사람이 지금 가서 봐야 하고
    뒤의 것은 아직 아무 일도 없다.
    """
    h = StateHistory()

    # 한 번도 확인된 적 없는 항목
    never = build_chip_state(h, "pwr.24v", "24V", Level.IDLE,
                             Verification.UNKNOWN, now_s=100.0)

    # FAULT 였다가 통신이 끊긴 항목
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=88.0)
    lost = build_chip_state(h, "ain0", "J3", Level.IDLE,
                            Verification.UNKNOWN, now_s=100.0)

    # 칩 모양은 둘 다 같다 — 그것이 문제의 출발점이다.
    assert chip_style(never.level, never.verification) == \
           chip_style(lost.level, lost.verification)

    # 그런데 곁들이는 내용이 다르다.
    assert never.last_known.text is None
    assert lost.last_known.text == "마지막: 오류 (12초 전)"

    # 그리고 무엇을 해야 하는지가 다르다.
    assert never.needs_attention is False
    assert lost.needs_attention is True


def test_unknown_observation_does_not_overwrite_history():
    """🔴 UNKNOWN 은 관측이 아니라 관측의 부재다.

    기록하면 그것이 "마지막으로 알던 것" 이 되어 진짜 이력을 덮는다.
    그러면 FAULT 였다는 사실이 첫 번째 통신 끊김에 지워진다.
    """
    h = StateHistory()
    h.observe("pwr.24v", Level.FAULT, Verification.VERIFIED, at_s=10.0)
    h.observe("pwr.24v", Level.IDLE, Verification.UNKNOWN, at_s=11.0)
    h.observe("pwr.24v", Level.OK, Verification.UNKNOWN, at_s=12.0)

    lk = h.last_known("pwr.24v", now_s=13.0)
    assert lk.level is Level.FAULT
    assert lk.was_bad is True


def test_verified_now_has_no_last_known():
    """지금 확인되고 있으면 곁들일 것이 없다.

    화면이 이미 사실을 말하는데 옆에 과거를 붙이면 무엇이 지금인지 흐려진다.
    """
    h = StateHistory()
    h.observe("pwr.24v", Level.FAULT, Verification.VERIFIED, at_s=10.0)
    now = build_chip_state(h, "pwr.24v", "24V", Level.OK,
                           Verification.VERIFIED, now_s=20.0)
    assert now.last_known.text is None


def test_commanded_is_not_unknown():
    """COMMANDED 는 확인이 아니지만 관측이다.

    "ON 명령됨" 은 아는 사실이다. 명령을 보냈다는 것 자체가 관측이므로
    이력에 남는다 — 피드백이 없을 뿐이다.
    """
    h = StateHistory()
    h.observe("pwr.24v", Level.OK, Verification.COMMANDED, at_s=10.0)
    lk = h.last_known("pwr.24v", now_s=15.0)
    assert lk.text == "마지막: 정상 (5초 전)"
    assert lk.was_bad is False


# ------------------------------------------------------------------- 오래됨

def test_stale_observation_is_dropped():
    """🔴 오래된 관측을 계속 붙여 두면 그것이 현재처럼 읽힌다.

    5분 전 오류는 지금 무슨 일이 벌어지는지에 대해 아무것도 말해 주지
    않는다.
    """
    h = StateHistory(stale_after_s=300.0)
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=0.0)

    assert h.last_known("ain0", now_s=299.0).text is not None
    assert h.last_known("ain0", now_s=300.0).text is not None
    assert h.last_known("ain0", now_s=300.1).text is None


def test_stale_still_reports_age():
    """말할 내용은 없어도 얼마나 됐는지는 남긴다 — 진단에 쓸 수 있다."""
    h = StateHistory(stale_after_s=10.0)
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    lk = h.last_known("ain0", now_s=100.0)
    assert lk.text is None
    assert lk.age_s == pytest.approx(100.0)


def test_clock_going_backwards_does_not_produce_negative_age():
    """시계가 뒤로 가도 "-3초 전" 을 말하지 않는다."""
    h = StateHistory()
    h.observe("ain0", Level.WARN, Verification.VERIFIED, at_s=100.0)
    lk = h.last_known("ain0", now_s=97.0)
    assert lk.age_s == 0.0
    assert lk.text == "마지막: 경고 (0초 전)"


# ------------------------------------------------------------------- 문구

@pytest.mark.parametrize("seconds,want", [
    (0.0, "0초 전"),
    (0.9, "0초 전"),
    (12.4, "12초 전"),
    (59.9, "59초 전"),
    (60.0, "1분 전"),
    (119.0, "1분 전"),
    (3599.0, "59분 전"),
    (3600.0, "1시간 전"),
    (7200.0, "2시간 전"),
])
def test_format_age(seconds, want):
    assert format_age(seconds) == want


def test_format_age_does_not_claim_precision():
    """🔴 `12.3초 전` 은 정밀도를 주장한다.

    이 값은 확인이 끊긴 뒤로 흐른 시간이라 정밀할 수가 없다.
    """
    assert "." not in format_age(12.345)


def test_format_age_clamps_negative():
    assert format_age(-5.0) == "0초 전"


# ------------------------------------------------------------------- 주의 필요

def test_needs_attention_when_currently_bad():
    s = ChipState("k", "라벨", Level.FAULT, Verification.VERIFIED)
    assert s.needs_attention is True
    s = ChipState("k", "라벨", Level.WARN, Verification.COMMANDED)
    assert s.needs_attention is True


def test_needs_attention_false_when_fine():
    s = ChipState("k", "라벨", Level.OK, Verification.VERIFIED)
    assert s.needs_attention is False
    s = ChipState("k", "라벨", Level.IDLE, Verification.UNKNOWN)
    assert s.needs_attention is False


def test_needs_attention_survives_losing_contact():
    """🔴 확인이 끊겼다고 문제가 사라지지 않는다."""
    h = StateHistory()
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    lost = build_chip_state(h, "ain0", "J3", Level.IDLE,
                            Verification.UNKNOWN, now_s=5.0)
    assert lost.needs_attention is True


def test_attention_lapses_when_observation_goes_stale():
    """다만 영원히 붙들지는 않는다. 오래되면 말할 근거가 없다."""
    h = StateHistory(stale_after_s=10.0)
    h.observe("ain0", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    lost = build_chip_state(h, "ain0", "J3", Level.IDLE,
                            Verification.UNKNOWN, now_s=100.0)
    assert lost.needs_attention is False


# ------------------------------------------------------------------- 살림

def test_forget_and_clear():
    h = StateHistory()
    h.observe("a", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    h.observe("b", Level.OK, Verification.VERIFIED, at_s=0.0)

    h.forget("a")
    assert h.last_known("a", now_s=1.0).text is None
    assert h.last_known("b", now_s=1.0).text is not None

    h.clear()
    assert h.last_known("b", now_s=1.0).text is None


def test_forget_unknown_key_does_not_raise():
    StateHistory().forget("없는키")


def test_history_is_per_key():
    h = StateHistory()
    h.observe("pwr.24v", Level.FAULT, Verification.VERIFIED, at_s=0.0)
    assert h.last_known("pwr.5v", now_s=1.0).text is None


def test_imports_no_qt():
    """🔴 이 층은 PyQt6 를 import 하지 않는다.

    디스플레이 없이 시험이 돌아야 하고, 시각 언어와 상태 판정이 위젯보다
    먼저 굳어야 한다.
    """
    import inspect

    import host.gui.last_known as mod

    src = inspect.getsource(mod)
    assert "PyQt" not in src
    assert "QtWidgets" not in src
