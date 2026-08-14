"""화면 상태 — **Qt 없이** 시험한다.

🔴 이 파일이 존재하는 이유가 곧 `host/gui/screen.py` 가 존재하는 이유다.

   화면이 무엇을 보여야 하는지는 배치와 무관하다. 레일을 왼쪽에 세우든
   위에 눕히든, "연결이 끊기면 마지막으로 알던 값을 계속 말해야 한다" 는
   변하지 않는다. 그 판정을 Qt 밖에 두면 배치를 바꿔도 시험이 살아남는다.

   실제로 그랬다. 레일을 아래에서 왼쪽으로 옮기면서 위젯 이름이 전부
   바뀌었는데, 여기 있는 계약들은 하나도 안 바뀌었다.
"""

from __future__ import annotations

from host.gui.last_known import StateHistory
from host.gui.screen import (
    AIN_COUNT,
    CONNECTOR_OFFSET,
    Identity,
    ScreenState,
    build_channels,
    build_rails,
    build_screen,
    empty_channels,
)
from host.gui.widgets.status_chip import Level, Verification


# ------------------------------------------------------------------ 레일

def test_rails_are_never_verified():
    """🔴 전원 레일은 영원히 COMMANDED 다.

    피드백 회로가 없으므로 GPIO 를 올렸다는 것과 실제로 24V 가 나온다는
    것은 다른 사실이고, 보드는 후자를 모른다. 화면이 둘을 같은 초록으로
    그리면 사용자는 확인된 것으로 읽는다.
    """
    h = StateHistory()
    build_rails({"pwr.24v": True}, reachable=True, history=h, now_s=100.0)
    assert h._last["pwr.24v"].verification is Verification.COMMANDED
    assert h._last["pwr.24v"].verification is not Verification.VERIFIED


def test_commanded_is_none_when_unreachable():
    """🔴 `None` 은 "모른다" 이지 "꺼짐" 이 아니다.

    연결이 끊긴 동안 마지막 값을 켜진 것처럼 보여 주면 화면이 거짓말을
    하고, 꺼진 것처럼 보여 줘도 마찬가지다.
    """
    h = StateHistory()
    rails = build_rails({}, reachable=False, history=h, now_s=0.0)
    assert all(r.commanded is None for r in rails)


def test_losing_contact_keeps_what_we_last_knew():
    """🔴 확인이 끊겼다고 문제가 사라지지 않는다.

    24V 가 켜진 채 통신만 끊겼을 수 있다. 그때 화면이 "확인 불가" 만
    말하면 사용자는 안심한다.
    """
    h = StateHistory()
    build_rails({"pwr.24v": True}, reachable=True, history=h, now_s=0.0)
    rails = build_rails({}, reachable=False, history=h, now_s=12.0)
    v24 = next(r for r in rails if r.key == "pwr.24v")
    assert "마지막" in v24.last_known


def test_never_known_shows_nothing_extra():
    h = StateHistory()
    rails = build_rails({}, reachable=False, history=h, now_s=0.0)
    assert all(r.last_known == "" for r in rails)


def test_rail_order_is_the_power_up_order():
    """🔴 5V -> 14.9V -> 24V. 화면 순서가 기동 순서와 같아야 사용자가
       순차 기동을 눈으로 따라갈 수 있다."""
    h = StateHistory()
    rails = build_rails({}, reachable=True, history=h, now_s=0.0)
    assert [r.key for r in rails] == ["pwr.5v", "pwr.14v9", "pwr.24v"]


# ------------------------------------------------------------------ 채널

def _ain(connector_id: int, ma: float, status: int = 0) -> dict:
    return {"type": "ain", "connector_id": connector_id, "ma": ma,
            "status": status}


def test_channel_update_is_isolated():
    """🔴 채널 장애 격리 — 레코드가 오지 않은 채널은 건드리지 않는다.

    한 채널이 조용하다고 나머지를 지우면, 센서 하나가 빠졌을 때 화면이
    통째로 비는 것처럼 보인다.
    """
    first = build_channels([_ain(3, 12.0), _ain(6, 0.1)], reachable=True)
    assert first[0].ma == 12.0
    assert first[3].ma == 0.1
    assert first[1].ma is None

    # J3 만 다시 온다 — J6 은 그대로 남아야 한다.
    second = build_channels([_ain(3, 15.0)], reachable=True, previous=first)
    assert second[0].ma == 15.0
    assert second[3].ma == 0.1


def test_unknown_connector_is_ignored():
    """모르는 커넥터 번호가 와도 죽지 않는다."""
    chans = build_channels([_ain(99, 12.0), _ain(-5, 3.0)], reachable=True)
    assert all(c.ma is None for c in chans)
    assert len(chans) == AIN_COUNT


def test_unreachable_clears_every_channel():
    """🔴 연결이 끊기면 마지막 값을 계속 띄우지 않는다 — 지금 값으로 읽힌다."""
    live = build_channels([_ain(3, 12.0)], reachable=True)
    dead = build_channels([], reachable=False, previous=live)
    assert all(c.ma is None for c in dead)
    assert all(c.verification is Verification.UNKNOWN for c in dead)


def test_status_flag_becomes_a_warning():
    chans = build_channels([_ain(3, 12.0, status=1)], reachable=True)
    assert chans[0].level is Level.WARN


def test_connectors_are_named_from_the_offset():
    """AIN0 이 J3 이다 (데이터시트 §5.3)."""
    chans = empty_channels()
    assert chans[0].connector == f"J{CONNECTOR_OFFSET}"
    assert chans[-1].connector == f"J{CONNECTOR_OFFSET + AIN_COUNT - 1}"


# ------------------------------------------------------------------ 전체

def test_build_screen_carries_the_error_into_the_link():
    h = StateHistory()
    state = build_screen(
        ScreenState(channels=empty_channels()),
        identity=Identity(port="COM23"), mode="RUN", error="타임아웃",
        rail_values={}, records=[], history=h, now_s=0.0,
    )
    assert state.reachable is False
    assert state.link.bad is True
    assert "타임아웃" in state.link.text


def test_build_screen_is_pure_enough_to_repeat():
    """🔴 같은 입력으로 두 번 부르면 같은 화면이 나와야 한다.

    워커가 주기마다 부르므로, 호출마다 결과가 흔들리면 화면이 깜빡인다.
    (레일 이력만은 시각을 따라가므로 같은 `now_s` 를 준다.)
    """
    h = StateHistory()
    args = dict(identity=Identity(port="sim"), mode="CONFIG", error=None,
                rail_values={"pwr.5v": True}, records=[_ain(3, 12.0)],
                history=h, now_s=5.0)
    base = ScreenState(channels=empty_channels())
    a = build_screen(base, **args)
    b = build_screen(base, **args)
    assert a == b
