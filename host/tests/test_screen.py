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
    TRACE_LEN,
    Identity,
    ScreenState,
    build_channels,
    build_rails,
    build_screen,
    empty_channels,
    summarize,
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

def _ain(connector_id: int, ma: float, status: int = 0,
         t: int = 0) -> dict:
    return {"type": "ain", "connector_id": connector_id, "ma": ma,
            "status": status, "t": t}


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


# ------------------------------------------------------------------ 트레이스

def test_trace_collects_what_arrived():
    """값이 올 때마다 이력에 쌓인다 — 화면의 시간축이 여기서 나온다."""
    a = build_channels([_ain(3, 12.0)], reachable=True)
    b = build_channels([_ain(3, 13.0)], reachable=True, previous=a)
    c = build_channels([_ain(3, 14.0)], reachable=True, previous=b)
    assert c[0].trace == (12.0, 13.0, 14.0)


def test_trace_is_bounded():
    """🔴 무한히 자라지 않는다. 하루를 켜 두면 메모리가 그만큼 먹힌다."""
    chans = build_channels([_ain(3, 4.0)], reachable=True)
    for i in range(TRACE_LEN + 50):
        chans = build_channels([_ain(3, float(i % 16 + 4))],
                               reachable=True, previous=chans)
    assert len(chans[0].trace) == TRACE_LEN


def test_every_record_lands_in_the_trace():
    """🔴 한 스텝에 두 개가 오면 **둘 다** 남는다.

    워커는 100 ms 마다 도는데 텔레메트리 주기도 100 ms 라, 한 스텝에
    레코드가 0 개일 때도 2 개일 때도 있다. 스텝당 하나만 담으면 2 개가
    온 스텝에서 절반이 조용히 버려지고, 화면은 보드가 실제보다 느리게
    보내는 것처럼 그린다 — 요약 패널의 `표본 간격` 이 그것을 잡아냈다.
    """
    chans = build_channels(
        [_ain(3, 12.0, t=100), _ain(3, 12.5, t=200)], reachable=True)
    assert chans[0].trace == (12.0, 12.5)
    assert chans[0].trace_t == (100, 200)


def test_a_silent_channel_simply_stops_growing():
    """🔴 말이 없던 채널에 구멍을 **넣지 않는다.**

    채널마다 수집 주기가 다를 수 있고(`ainN.period_ms`), 그때 조용한 쪽에
    구멍을 채우면 오지도 않은 시각을 화면이 지어내게 된다. 시각을 맞추는
    일은 인덱스가 아니라 보드가 찍은 `t` 가 한다 — 그래서 길이가 서로
    달라도 상관없다.
    """
    a = build_channels([_ain(3, 12.0, t=100), _ain(4, 8.0, t=100)],
                       reachable=True)
    b = build_channels([_ain(3, 13.0, t=200)], reachable=True, previous=a)
    assert b[0].trace == (12.0, 13.0)
    assert b[1].trace == (8.0,)


def test_a_step_without_telemetry_does_not_advance_the_trace():
    """🔴 워커는 100 ms 마다 도는데 텔레메트리는 그보다 늦게 올 수 있다.

    빈 스텝마다 칸을 밀면 트레이스가 구멍투성이가 된다 — 실제로는
    아무 일도 없었는데 화면이 "값이 안 왔다" 를 반복해 말하는 셈이다.
    """
    a = build_channels([_ain(3, 12.0)], reachable=True)
    b = build_channels([], reachable=True, previous=a)
    c = build_channels([{"type": "stat"}], reachable=True, previous=b)
    assert c[0].trace == (12.0,)


def test_losing_the_link_breaks_the_line_but_keeps_the_past():
    """🔴 연결이 끊겨도 **지나간 일**은 지우지 않는다.

    현재값은 지운다(그것을 계속 띄우면 지금 값으로 읽힌다). 이력은
    과거라서 남겨도 거짓말이 아니고, 오히려 "끊기기 직전에 이랬다" 가
    사용자에게 필요한 정보다. 대신 끊긴 자리에 구멍을 넣어 선을 끊는다.
    """
    live = build_channels([_ain(3, 12.0)], reachable=True)
    dead = build_channels([], reachable=False, previous=live)
    assert dead[0].ma is None
    assert dead[0].trace == (12.0, None)


def test_a_long_outage_leaves_only_one_gap():
    """🔴 끊긴 채로 두어도 이력이 `None` 으로 덮이지 않는다.

    워커는 끊긴 동안에도 초당 열 번 돈다. 스텝마다 구멍을 넣으면
    1분 뒤에는 트레이스가 통째로 비어 마지막으로 알던 값이 사라진다.
    """
    chans = build_channels([_ain(3, 12.0)], reachable=True)
    for _ in range(50):
        chans = build_channels([], reachable=False, previous=chans)
    assert chans[0].trace == (12.0, None)


def test_trace_carries_the_time_the_board_stamped():
    """🔴 화면이 시각을 지어내지 않는다 (설계 원칙 2).

    커서가 "−3.0초" 라고 말하려면 그 3 초가 어디선가 와야 한다. 호스트가
    주기를 100 ms 로 **가정해** 곱하면, 주기를 바꾸는 순간 화면이 조용히
    틀린 시각을 말한다 — 실제로 그렇게 짰다가 200 ms 로 돌려 보고 알았다.

    보드가 찍은 `t` 를 그대로 들고 다닌다. 규격 §7.1 에 따라 이 필드는
    마스크와 무관하게 항상 실린다.
    """
    a = build_channels([_ain(3, 12.0, t=1000)], reachable=True)
    b = build_channels([_ain(3, 13.0, t=1200)], reachable=True, previous=a)
    assert b[0].trace_t == (1000, 1200)


def test_value_and_time_stay_the_same_length():
    """🔴 시각과 값이 나란히 간다. 한쪽만 밀리면 커서가 엉뚱한 값을 읽는다."""
    chans = build_channels([_ain(3, 12.0, t=100)], reachable=True)
    for i in range(1, 6):
        chans = build_channels([_ain(3, 12.0 + i, t=100 + i * 100)],
                               reachable=True, previous=chans)
    chans = build_channels([], reachable=False, previous=chans)
    for ch in chans:
        assert len(ch.trace) == len(ch.trace_t)


def test_reconnecting_continues_the_same_trace():
    live = build_channels([_ain(3, 12.0)], reachable=True)
    dead = build_channels([], reachable=False, previous=live)
    back = build_channels([_ain(3, 15.0)], reachable=True, previous=dead)
    assert back[0].trace == (12.0, None, 15.0)


# ------------------------------------------------------------------ 범위

def test_channel_carries_the_configured_range():
    """게이지가 `0 – 150 bar` 눈금을 그리려면 상태가 범위를 들고 있어야 한다."""
    chans = build_channels([_ain(3, 12.0)], reachable=True,
                           ranges={0: (0.0, 150.0)})
    assert chans[0].span == (0.0, 150.0)
    assert chans[1].span is None


def test_the_configured_unit_fills_in_when_telemetry_omits_it():
    """🔴 `unit` 필드는 기본 마스크에서 꺼져 있다. 그래도 화면은 `150` 이
    아니라 `150 bar` 라고 말해야 한다."""
    chans = build_channels([_ain(3, 12.0)], reachable=True,
                           units={0: "bar"})
    assert chans[0].unit == "bar"


def test_the_board_wins_when_it_does_send_a_unit():
    """🔴 보드가 보낸 것이 우선이다. 설정은 화면이 들고 있는 사본일 뿐이고,
    적용하지 않은 편집이 섞여 있을 수 있다."""
    rec = _ain(3, 12.0)
    rec["unit"] = "kPa"
    chans = build_channels([rec], reachable=True, units={0: "bar"})
    assert chans[0].unit == "kPa"


def test_the_range_is_configuration_not_telemetry():
    """🔴 레코드가 안 와도 범위는 그대로다.

    범위는 사용자가 설정한 것이지 보드가 보내는 값이 아니다. 값이 잠깐
    끊겼다고 눈금이 사라지면 화면이 깜빡인다.
    """
    chans = build_channels([], reachable=True, ranges={0: (0.0, 60.0)})
    assert chans[0].span == (0.0, 60.0)

    dead = build_channels([], reachable=False, previous=chans,
                          ranges={0: (0.0, 60.0)})
    assert dead[0].span == (0.0, 60.0)


# ------------------------------------------------------------------ 요약

def test_summary_counts_what_is_actually_arriving():
    chans = build_channels(
        [_ain(3, 12.0), _ain(4, 2.0), _ain(5, 21.5)], reachable=True)
    s = summarize(chans)
    assert s.live == 3
    assert s.broken == 1          # J4 — 4 mA 아래
    assert s.over == 1            # J5 — 20 mA 위
    assert s.total == AIN_COUNT


def test_summary_does_not_count_silent_channels_as_broken():
    """🔴 센서 미연결은 정상 상태다 (설계 원칙 3).

    값이 안 오는 것과 루프가 끊긴 것은 다른 사실이다. 비활성 커넥터를
    고장으로 세면 화면이 늘 빨갛고, 그러면 진짜 고장이 묻힌다.
    """
    s = summarize(build_channels([_ain(3, 12.0)], reachable=True))
    assert s.live == 1
    assert s.broken == 0


def test_summary_measures_the_real_sample_interval():
    """🔴 주기를 설정에서 읽지 않고 **온 것에서** 잰다.

    설정값은 요청이고 이것은 실제다. 둘이 다르면 그 차이가 곧 사용자가
    알아야 할 사실이다 — 보드가 따라오지 못하고 있다는 뜻이니까.
    """
    chans = build_channels([_ain(3, 12.0, t=1000)], reachable=True)
    for i in range(1, 5):
        chans = build_channels([_ain(3, 12.0, t=1000 + i * 200)],
                               reachable=True, previous=chans)
    assert summarize(chans).interval_ms == 200.0


def test_summary_has_no_interval_before_two_samples():
    assert summarize(build_channels([_ain(3, 12.0, t=5)],
                                    reachable=True)).interval_ms is None


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
