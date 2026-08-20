"""`$STAT` 을 사람이 읽는 말로 옮기는 층 — Qt 없이 전부 시험한다.

🔴 **왜 이 시험이 있나**

   보드는 `$STAT` 으로 시간축 신뢰도(`clock.src`), PPS 가 실제로 오는지
   (`pps_raw_count`), 표본을 버렸는지(`queues[].drops`)를 말한다. 그런데
   화면이 그것을 안 읽으면 사람은 GDB 를 붙이는 수밖에 없다 — 실제로
   2026-08-19 에 "PPS 가 안 온다"고 배선을 의심하다 GDB 로 TIM8 CCR3 을
   직접 읽고서야 **펄스는 잘 오고 짝지을 유효 RMC 가 없었을 뿐**임을
   알았다(규격 §7.4).

   그래서 여기서 보는 것은 "필드를 읽었는가" 가 아니라 **"뜻으로 옮겼는가"**
   와 **"정상인 것을 경고로 만들지 않았는가"** 다. 후자가 없으면 화면이 늘
   빨갛고, 그러면 진짜 고장이 묻힌다(설계 원칙 3).
"""
from __future__ import annotations

import pytest

from host.gui.diagnostics import (
    UNKNOWN_TEXT,
    build_diagnostics,
)
from host.gui.widgets.status_chip import Verification


def _stat(**over) -> dict:
    """정상적으로 도는 보드 하나. 시험은 여기서 한 항목만 바꾼다."""
    base = {
        "type": "stat", "mode": "CONFIG", "ctl_mode": "ACTIVE",
        "fw": "0.1.0", "board_rev": "2.0",
        "time_source": "gnss_pps", "time_quality": 2,
        "uptime_ms": 123456,
        "clock": {"src": "hse_pll", "sysclk_hz": 550_000_000},
        "gnss": {"pps_age_ms": 842, "pps_raw_age_ms": 842, "pps_raw_count": 118,
                 "pps_unpaired_reason": None, "sats": 11,
                 "init_sent": True, "init_exhausted": False,
                 "sentence_seen": True},
        "rails": {"v24": False, "v14v9": False, "v5": True},
        "din": [{"connector_id": 18, "state": 0},
                {"connector_id": 19, "state": 0},
                {"connector_id": 20, "state": 1}],
        "queues": [{"ch": 0, "depth": 0, "peak": 3, "drops": 0}],
        "lcd": {"epoch": 3, "reinit": 0, "redraw": 41, "verify_ok": 128,
                "verify_fail": 0, "rejected": 0, "readback": True},
    }
    base.update(over)
    return base


def _read(stat, key):
    d = build_diagnostics(stat)
    r = d.reading(key)
    assert r is not None, f"{key} 항목이 없다 — 있는 것: {[x.key for x in d.readings]}"
    return r


# ---------------------------------------------------------------- 클럭

def test_healthy_board_shows_no_warning_at_all():
    """🔴 되돌림 방지 — 멀쩡한 보드가 경고를 내면 나머지 시험이 다 무의미하다."""
    d = build_diagnostics(_stat())
    assert d.warnings == (), [w.key for w in d.warnings]


def test_crystal_clock_is_not_a_warning():
    r = _read(_stat(), "clock.src")
    assert r.warning is False
    assert "HSE" in r.value or "크리스털" in r.value


def test_internal_rc_is_a_warning_that_says_how_bad():
    """`hsi` 는 초 안쪽 보간이 ±1 % 로 흔들린다는 뜻이다(규격 §7.4).

    숫자를 화면이 말해야 한다 — "hsi" 라는 글자만 띄우면 그게 나쁜 것인지
    사람이 알 방법이 없다.
    """
    r = _read(_stat(clock={"src": "hsi", "sysclk_hz": 64_000_000}), "clock.src")
    assert r.warning is True
    assert "±1" in r.note
    assert "10 ms" in r.note


def test_clock_the_device_cannot_answer_is_unknown_not_a_warning():
    """발진기가 없는 보드는 `null` 을 낸다 — 고장이 아니다."""
    r = _read(_stat(clock={"src": None, "sysclk_hz": None}), "clock.src")
    assert r.warning is False
    assert r.verification is Verification.UNKNOWN
    assert r.value == UNKNOWN_TEXT


def test_clock_key_missing_entirely_is_unknown():
    s = _stat()
    del s["clock"]
    r = _read(s, "clock.src")
    assert r.value == UNKNOWN_TEXT
    assert r.warning is False


def test_sysclk_is_shown_in_megahertz():
    r = _read(_stat(clock={"src": "hsi", "sysclk_hz": 64_000_000}),
              "clock.sysclk_hz")
    assert "64" in r.value and "MHz" in r.value


# ---------------------------------------------------------------- 큐 / 유실

def test_dropped_samples_are_a_warning():
    """🔴 사용자가 가장 싫어하는 실패가 "표본을 버리는 것" 이다."""
    d = build_diagnostics(_stat(
        queues=[{"ch": 0, "depth": 4, "peak": 9, "drops": 7}]))
    r = d.reading("queues.drops")
    assert r.warning is True
    assert "7" in r.value
    assert r in d.warnings


def test_no_drops_is_not_a_warning():
    r = _read(_stat(), "queues.drops")
    assert r.warning is False


def test_no_enabled_channel_is_a_fact_not_unknown():
    """채널을 전부 꺼 둔 것은 정상이다 — "모름" 이 아니다."""
    r = _read(_stat(queues=[]), "queues.drops")
    assert r.value != UNKNOWN_TEXT
    assert r.warning is False


def test_missing_queues_field_is_unknown_not_zero():
    s = _stat()
    del s["queues"]
    r = _read(s, "queues.drops")
    assert r.value == UNKNOWN_TEXT
    assert r.warning is False


def test_queue_depth_is_shown_per_channel():
    r = _read(_stat(queues=[{"ch": 5, "depth": 2, "peak": 9, "drops": 0}]),
              "queue.5")
    assert "2" in r.value and "9" in r.value


# ---------------------------------------------------------------- GNSS / PPS

def test_paired_pps_is_normal():
    r = _read(_stat(), "gnss.pps_paired")
    assert r.warning is False
    assert "842" in r.value


def test_unpaired_pps_indoors_is_not_a_warning():
    """🔴 실기기 관측(2026-08-19): 실내라 RMC 가 계속 `V` 였다. PPS 는
    정확히 1초 간격으로 들어오고 있었고 배선은 멀쩡했다. 이것을 경고로
    칠하면 실내 벤치에서 화면이 영원히 빨갛다.
    """
    d = build_diagnostics(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": 300, "pps_raw_count": 118,
        "pps_unpaired_reason": "no_valid_nmea", "sats": 0,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}))
    assert d.warnings == (), [w.key for w in d.warnings]


def test_no_valid_nmea_is_translated_into_what_it_actually_means():
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": 300, "pps_raw_count": 118,
        "pps_unpaired_reason": "no_valid_nmea", "sats": 0,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}),
        "gnss.pps_unpaired")
    # "PPS 는 들어오는데 위성 고정이 없어 시간축에 못 쓴다"
    assert "PPS" in r.note and "고정" in r.note
    assert "raw" not in r.value.lower()      # 원시 JSON 을 그대로 뿌리지 않는다


def test_pps_pulses_are_reported_separately_from_pairing():
    """🔴 이 구분이 GDB 세션을 없애 준다 — "펄스가 오는가" 와 "시간축이
    그것을 받아들였는가" 는 다른 질문이다(규격 §7.4).
    """
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": 300, "pps_raw_count": 118,
        "pps_unpaired_reason": "no_valid_nmea", "sats": 0,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}),
        "gnss.pps_raw")
    assert "118" in r.value


def test_no_pps_with_pulses_arriving_is_a_warning():
    """유효한 RMC 가 왔고 펄스도 들어오는데 짝을 못 지었다 — 진짜 이상이다."""
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": 4000, "pps_raw_count": 40,
        "pps_unpaired_reason": "no_pps", "sats": 9,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}),
        "gnss.pps_unpaired")
    assert r.warning is True


def test_no_pps_without_any_pulse_is_not_a_warning():
    """🔴 PPS 선을 안 물린 것은 정상적인 사용 방식이다(설계 원칙 3).
    한 번도 안 온 펄스를 고장으로 부르면 그 배선을 쓰는 사람이 영원히
    경고를 본다.
    """
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": None, "pps_raw_count": 0,
        "pps_unpaired_reason": "no_pps", "sats": 9,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}),
        "gnss.pps_unpaired")
    assert r.warning is False
    assert "PPS" in r.note


def test_unpaired_reason_null_before_anything_happened_is_not_a_warning():
    d = build_diagnostics(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": None, "pps_raw_count": 0,
        "pps_unpaired_reason": None, "sats": None,
        "init_sent": False, "init_exhausted": False, "sentence_seen": False}))
    assert d.warnings == (), [w.key for w in d.warnings]


def test_unknown_unpaired_reason_is_not_invented():
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": 1, "pps_raw_count": 1,
        "pps_unpaired_reason": "sunspots", "sats": 1,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}),
        "gnss.pps_unpaired")
    assert r.verification is Verification.UNKNOWN
    assert "sunspots" in r.value        # 아는 척하지 않고 원문을 보여준다


def test_satellite_count_null_is_unknown_not_zero():
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": None, "pps_raw_count": 0,
        "pps_unpaired_reason": None, "sats": None,
        "init_sent": True, "init_exhausted": False, "sentence_seen": False}),
        "gnss.sats")
    assert r.value == UNKNOWN_TEXT
    assert r.warning is False


def test_zero_satellites_indoors_is_not_a_warning():
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": 10, "pps_raw_count": 5,
        "pps_unpaired_reason": "no_valid_nmea", "sats": 0,
        "init_sent": True, "init_exhausted": False, "sentence_seen": True}),
        "gnss.sats")
    assert r.warning is False


def test_gnss_turned_off_is_not_a_warning():
    """`gnss.enabled` 가 꺼져 있으면 보드는 `LOG` 를 보내지 않는다."""
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": None, "pps_raw_count": 0,
        "pps_unpaired_reason": None, "sats": None,
        "init_sent": False, "init_exhausted": False, "sentence_seen": False}),
        "gnss.init")
    assert r.warning is False


def test_exhausted_init_is_a_warning():
    """보드가 할 수 있는 것을 다 했는데 문장이 한 줄도 안 왔다(규격 §7.4)."""
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": None, "pps_raw_count": 0,
        "pps_unpaired_reason": None, "sats": None,
        "init_sent": True, "init_exhausted": True, "sentence_seen": False}),
        "gnss.init")
    assert r.warning is True
    assert "배선" in r.note


def test_init_sent_but_still_waiting_is_not_a_warning():
    r = _read(_stat(gnss={
        "pps_age_ms": None, "pps_raw_age_ms": None, "pps_raw_count": 0,
        "pps_unpaired_reason": None, "sats": None,
        "init_sent": True, "init_exhausted": False, "sentence_seen": False}),
        "gnss.init")
    assert r.warning is False


# ---------------------------------------------------------------- 화면(LCD)

def test_readback_false_is_explained_and_not_a_warning():
    """🔴 되읽기가 안 되는 3.5" 모듈은 흔하다 — 고장이 아니라 사양이다."""
    r = _read(_stat(lcd={"epoch": 1, "reinit": 0, "redraw": 0, "verify_ok": 0,
                         "verify_fail": 0, "rejected": 0, "readback": False}),
              "lcd.readback")
    assert r.warning is False
    assert "자가진단" in r.note


def test_readback_never_asked_is_unknown():
    r = _read(_stat(lcd={"epoch": 0, "reinit": 0, "redraw": 0, "verify_ok": 0,
                         "verify_fail": 0, "rejected": 0, "readback": None}),
              "lcd.readback")
    assert r.verification is Verification.UNKNOWN
    assert r.warning is False


def test_board_without_a_panel_raises_nothing():
    """화면이 안 붙은 보드는 전부 0 과 `readback: null` 이다."""
    d = build_diagnostics(_stat(
        lcd={"epoch": 0, "reinit": 0, "redraw": 0, "verify_ok": 0,
             "verify_fail": 0, "rejected": 0, "readback": None}))
    assert d.warnings == (), [w.key for w in d.warnings]


def test_reinit_is_a_warning_about_spi():
    r = _read(_stat(lcd={"epoch": 5, "reinit": 2, "redraw": 40,
                         "verify_ok": 100, "verify_fail": 2, "rejected": 0,
                         "readback": True}), "lcd.reinit")
    assert r.warning is True
    assert "spi" in r.note.lower()


def test_rejected_draw_is_a_warning():
    r = _read(_stat(lcd={"epoch": 1, "reinit": 0, "redraw": 1, "verify_ok": 1,
                         "verify_fail": 0, "rejected": 3, "readback": True}),
              "lcd.rejected")
    assert r.warning is True


def test_missing_lcd_field_is_unknown_not_zero():
    """🔴 항목이 빠졌을 때 "0 회" 라고 쓰면 화면이 없는 사실을 지어낸다."""
    s = _stat()
    del s["lcd"]
    r = _read(s, "lcd.reinit")
    assert r.value == UNKNOWN_TEXT
    assert r.warning is False


# ---------------------------------------------------------------- 입력·전원

def test_rails_are_commanded_never_verified():
    """설계 원칙 4 — 피드백 회로가 없으므로 "정상 ON" 이라고 쓰면 안 된다."""
    r = _read(_stat(), "rail.v5")
    assert r.verification is Verification.COMMANDED
    assert "명령" in r.value


def test_digital_input_is_measured():
    r = _read(_stat(), "din.20")
    assert r.verification is Verification.VERIFIED


def test_missing_din_is_unknown():
    s = _stat()
    del s["din"]
    r = _read(s, "din.20")
    assert r.value == UNKNOWN_TEXT


# ---------------------------------------------------------------- 모름 / 신선도

def test_no_stat_at_all_is_unknown_everywhere():
    """보드가 없거나 아직 못 읽었을 때. 🔴 0 도 정상도 아니고 "모름" 이다."""
    d = build_diagnostics(None)
    assert d.readings, "항목 자리는 남겨 둬야 사람이 무엇을 못 읽었는지 안다"
    for r in d.readings:
        assert r.value == UNKNOWN_TEXT, f"{r.key} 가 {r.value!r} 라고 말한다"
        assert r.verification is Verification.UNKNOWN


def test_no_stat_raises_no_warning():
    """🔴 모르는 것을 경고로 만들지 않는다 — 지어내는 것과 같다."""
    assert build_diagnostics(None).warnings == ()


def test_no_stat_is_not_fresh():
    assert build_diagnostics(None).fresh is False


def test_a_link_error_is_shown_as_itself():
    d = build_diagnostics(None, error="응답 없음: STAT")
    assert d.fresh is False
    assert "응답 없음" in d.headline


def test_a_fresh_stat_is_fresh():
    assert build_diagnostics(_stat(), age_s=0.2).fresh is True


def test_an_old_stat_is_no_longer_fresh():
    """읽은 지 오래된 값을 지금 값처럼 그리면 화면이 거짓말을 한다."""
    d = build_diagnostics(_stat(), age_s=30.0)
    assert d.fresh is False
    assert "30" in d.age_text


def test_headline_counts_the_warnings():
    d = build_diagnostics(_stat(clock={"src": "hsi", "sysclk_hz": 64_000_000}))
    assert "1" in d.headline


# ------------------------------------------------------------------ 스텁 보드

@pytest.fixture
def sim_stat():
    """스텁 보드가 실제로 내는 `$STAT` 을 그대로 받아온다.

    🔴 손으로 지은 사전이 아니라 **장치가 내는 것**으로 확인한다. 스텁은
       `clock` 을 둘 다 `null` 로 내므로(발진기가 없다), 없는 항목을 그대로
       다룰 수 있는지가 여기서 드러난다.
    """
    from host.tests.fake_board import fake_service

    return fake_service(clock=lambda: 0).fetch_stat()


def test_the_stub_board_produces_no_false_warning(sim_stat):
    d = build_diagnostics(sim_stat, age_s=0.0)
    assert d.warnings == (), [(w.key, w.value) for w in d.warnings]
    assert d.fresh is True


def test_the_stub_board_clock_reads_as_unknown(sim_stat):
    d = build_diagnostics(sim_stat)
    assert d.reading("clock.src").value == UNKNOWN_TEXT
    assert d.reading("clock.sysclk_hz").value == UNKNOWN_TEXT


def test_the_stub_board_queues_are_read(sim_stat):
    d = build_diagnostics(sim_stat)
    assert d.reading("queues.drops").value != UNKNOWN_TEXT


def test_every_reading_says_something_readable(sim_stat):
    """🔴 원시 JSON 을 그대로 뿌리지 않는다 — 항목마다 사람이 읽는 이름표와
    값이 있어야 한다."""
    d = build_diagnostics(sim_stat)
    for r in d.readings:
        assert r.label and r.value
        assert "{" not in r.value and "}" not in r.value


# ---- 호스트 링크 (규격 §4.2·§7.4) -------------------------------------------


def _stat_with_link(**over):
    link = {"baud": 921600, "confirmed": 921600, "pending": None,
            "remaining_ms": None, "applied": 0, "confirmed_count": 0,
            "reverted": 0}
    link.update(over)
    return {"type": "stat", "link": link}


def test_link_group_reports_the_current_speed():
    state = build_diagnostics(_stat_with_link())
    r = state.reading("link.baud")
    assert r is not None and "921,600" in r.value
    assert not r.warning


def test_a_pending_change_is_a_warning_and_explains_the_deadline():
    """🔴 확정 전에는 `$CFG,SAVE` 가 거부된다(규격 §4.2.2 규칙 5).

    그 사실을 여기 말고 알려 줄 곳이 없다 — 사용자는 "저장이 왜 안 되지"
    만 보게 된다.
    """
    state = build_diagnostics(_stat_with_link(
        baud=1500000, pending=1500000, remaining_ms=7200))
    r = state.reading("link.pending")
    assert r is not None
    assert "1,500,000" in r.value
    assert "7.2" in r.value                       # 남은 시간을 말한다
    assert r.warning
    assert "저장" in r.note and "돌아간다" in r.note


def test_no_pending_change_says_saving_is_safe():
    r = build_diagnostics(_stat_with_link()).reading("link.pending")
    assert r is not None and not r.warning
    assert "저장" in r.note


def test_a_revert_is_a_warning_because_it_is_a_measurement():
    """🔴 되돌아간 적이 있다 = 그 속도로는 이 배선이 안 된다는 실측이다.

    선행 프로젝트에서 미해결로 남은 대역폭 문제가 이 보드에서 나타나는
    방식이고(CLAUDE.md §1.2), 그것을 알려 주는 유일한 누적 기록이다.
    """
    r = build_diagnostics(_stat_with_link(reverted=2)).reading("link.reverted")
    assert r is not None and r.warning
    assert "2" in r.value


def test_an_old_firmware_without_a_link_object_is_unknown_not_zero():
    """🔴 모르는 것은 "모름" 이다 — 0 이나 기본값을 지어내지 않는다."""
    r = build_diagnostics({"type": "stat"}).reading("link.baud")
    assert r is not None and not r.known
    assert r.value == UNKNOWN_TEXT


# ---- 송신 링 (규격 §7.4, 2026-08-20) ----------------------------------------
#
# 🔴 이 묶음이 생긴 계기가 이 파일 머리말의 그것과 똑같다.
#
#    카탈로그(103줄 ≈ 25 KB)가 4,096 B 짜리 송신 링을 넘겨 43줄만 도착했고,
#    GUI 는 설정 폼을 아예 못 만들었다. 사용자에게는 "전원이 켜져 있는데
#    GUI 는 꺼졌다고 하고 토글도 안 먹는다" 로 보였다. 링의 계수기가 화면에
#    없어서 GDB 를 붙이고서야 원인을 알았다 — PPS 때와 같은 실수를 두 번
#    했다. 그래서 여기 둔다.


def _stat_with_tx(**over):
    tx = {"cap": 8192, "peak": 1200, "drops": 0, "dropped_bytes": 0,
          "ctl_drops": 0, "ctl_dropped_bytes": 0}
    tx.update(over)
    return {"type": "stat", "tx": tx}


def test_a_control_drop_is_a_warning_because_the_host_never_gets_an_answer():
    """🔴 이 묶음에서 가장 중요한 한 줄이다.

    명령 응답의 `seq` 는 항상 0 이라(규격 §5.2) 호스트는 이 수 말고는
    유실을 알아챌 방법이 없다. 텔레메트리 유실과 급이 다르다.
    """
    r = build_diagnostics(_stat_with_tx(ctl_drops=3)).reading("tx.ctl_drops")
    assert r is not None and r.warning
    assert "3" in r.value


def test_telemetry_drops_are_reported_but_not_confused_with_control_drops():
    """텔레메트리 유실은 흔한 상태다 — 제어 유실과 같은 칸에 뭉치지 않는다."""
    state = build_diagnostics(_stat_with_tx(drops=12, dropped_bytes=1620))
    telem = state.reading("tx.drops")
    ctl = state.reading("tx.ctl_drops")
    assert telem is not None and "12" in telem.value
    assert ctl is not None and not ctl.warning     # 제어는 멀쩡하다
    assert "없음" in ctl.value


def test_a_clean_ring_says_so_instead_of_staying_silent():
    state = build_diagnostics(_stat_with_tx())
    assert not state.reading("tx.ctl_drops").warning
    assert not state.reading("tx.drops").warning


def test_a_peak_at_the_brim_warns_even_with_zero_drops():
    """🔴 버린 줄이 0 이어도 수위가 링에 붙어 있으면 다음번엔 버린다.

    그 사실은 지나간 뒤에는 어디에도 안 남는다(규격 §7.4).
    """
    r = build_diagnostics(_stat_with_tx(peak=8100)).reading("tx.peak")
    assert r is not None and r.warning
    assert "%" in r.value


def test_a_device_without_a_send_ring_is_unknown_not_zero():
    """🔴 링이 없는 보드는 `tx` 가 null 이다.

    0 으로 읽어 "한 번도 안 찼다" 로 말하면 화면이 거짓 안심을 준다 —
    `clock` 의 null 과 같은 결이다.
    """
    r = build_diagnostics({"type": "stat", "tx": None}).reading("tx.ctl_drops")
    assert r is not None and not r.known
    assert r.value == UNKNOWN_TEXT
