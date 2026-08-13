from host.core.config_schema import parse_catalog
from host.core.framing import build_command, parse_line
from host.core.records import parse_record
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import HB_TIMEOUT_MS, DeviceSim, Mode


def _sim() -> DeviceSim:
    return DeviceSim(default_store())


def _sack(lines: list[str]):
    """응답 줄들 중 첫 $SACK 를 Command 로 반환한다."""
    for line in lines:
        if line.startswith("$SACK"):
            return parse_line(line)
    raise AssertionError(f"$SACK 없음: {lines}")


# ------------------------------------------------------------------ 모드
def test_boots_in_run_mode():
    assert _sim().mode == Mode.RUN


def test_hb_switches_to_config():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert sim.mode == Mode.CONFIG


def test_config_reverts_to_run_after_hb_timeout():
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.tick(HB_TIMEOUT_MS + 1)
    assert sim.mode == Mode.RUN


def test_hb_before_timeout_keeps_config():
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.tick(HB_TIMEOUT_MS - 1)
    assert sim.mode == Mode.CONFIG


def test_mode_boundary_is_strictly_greater_than():
    """🔴 경계는 > 이지 >= 가 아니다 (규격 §6.2).

    정확히 3000 ms 경과한 순간은 아직 CONFIG 다. 경계를 명시하지 않으면
    펌웨어와 호스트가 1 ms 차이로 다른 모드를 표시하고, 그 차이가
    ERR,MODE 로 나타나 재현 안 되는 버그가 된다.
    """
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.tick(HB_TIMEOUT_MS)              # 정확히 경계
    assert sim.mode == Mode.CONFIG
    sim.tick(HB_TIMEOUT_MS + 1)          # 한 틱 넘김
    assert sim.mode == Mode.RUN


def test_hb_emits_no_sack():
    """정상 하트비트에는 응답하지 않는다 (규격 §4)."""
    assert _sim().feed(build_command("HB")) == []


def test_corrupt_hb_does_not_enter_config_mode():
    """🔴 회귀 시험 — 체크섬이 깨진 \\$HB 는 모드를 바꾸면 안 된다.

    Q2 host_link.c:183-187 은 체크섬 검증 전에 host_hb_last_ms 를 갱신한다.
    Q2 에서는 단순 링크 생존 신호라 무해했지만, 우리 설계에서 \\$HB 는
    CONFIG 모드를 여는 열쇠다. 그대로 이식하면 이 시험이 실패한다.
    """
    sim = _sim()
    sim.feed("$HB*FF\r\n")                  # 올바른 체크섬은 0A
    assert sim.mode == Mode.RUN


def test_corrupt_hb_cannot_unlock_config_writes():
    """깨진 하트비트 뒤의 설정 변경은 여전히 거부돼야 한다."""
    sim = _sim()
    sim.feed("$HB*FF\r\n")
    cmd = _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "250")))
    assert cmd.args == ("CFG", "ERR", "MODE")
    assert sim.store.get("tx.period_ms") == 100


# --------------------------------------------------------------- 체크섬
def test_bad_checksum_returns_checksum_error():
    sim = _sim()
    cmd = _sack(sim.feed("$CFG,GET,tx.period_ms*00\r\n"))
    assert cmd.args == ("CFG", "ERR", "CHECKSUM")


def test_unparseable_line_is_dropped_silently():
    """verb 를 못 읽을 만큼 깨진 줄은 조용히 버린다. 링크는 유지."""
    assert _sim().feed("garbage without dollar\r\n") == []


def test_corrupt_line_with_control_char_does_not_kill_the_loop():
    """🔴 깨진 줄에 제어문자가 있어도 feed() 가 예외를 밖으로 내지 않는다.

    건져낸 verb 는 곧바로 build_line() 으로 들어가는데 거기서 제어문자가
    거부된다. 잡음으로 낀 TAB 하나면 충분하고, strip() 은 양끝만 지운다.
    serial_server 와 LoopbackTransport 가 feed() 를 맨몸으로 부르므로
    시뮬레이터 프로세스가 통째로 죽는다.

    규격 §3: "verb 를 읽을 수 없을 만큼 깨졌으면 조용히 버린다 (링크는 유지)".
    """
    sim = _sim()
    for bad in ("$C\x00FG,X*00\r\n", "$A\tB*00\r\n", "$X\x1bY*00\r\n"):
        assert sim.feed(bad) == [], bad
    assert sim.mode == Mode.RUN          # 링크가 살아 있다


def test_oversized_verb_is_not_echoed_back():
    """🔴 공격자가 정한 길이의 문자열을 응답에 되싣지 않는다.

    `"$" + "A"*4000 + "*00"` 은 4000개의 XOR 이 0x00 이라 **체크섬이 맞는
    정상 줄**로 파싱된다. 깨진 줄 경로가 아니라 "모르는 명령" 경로로 들어오고,
    방어가 없으면 UNKNOWN_KEY 응답에 4000자가 그대로 실린다.

    파이썬에서는 그저 긴 문자열이지만 C 로 옮기면 고정 버퍼 오버플로다.
    모르는 명령에 응답하는 것 자체는 맞으므로, verb 만 잘라서 돌려준다.
    """
    out = _sim().feed("$" + "A" * 4000 + "*00\r\n")
    assert len(out) == 1
    assert len(out[0]) < 40                      # 4000자가 되울리지 않는다
    assert parse_line(out[0]).args == ("?", "ERR", "UNKNOWN_KEY")


# ----------------------------------------------------------------- 조회
def test_cfg_list_is_allowed_in_run_mode():
    sim = _sim()
    assert sim.mode == Mode.RUN
    lines = sim.feed(build_command("CFG", "LIST"))
    catalog = [ln for ln in lines if ln.startswith("{")]
    schema = parse_catalog(catalog)
    assert "tx.period_ms" in schema.items
    assert _sack(lines).args == ("CFG", "OK")


def test_cfg_get_returns_current_value():
    sim = _sim()
    lines = sim.feed(build_command("CFG", "GET", "tx.period_ms"))
    rec = parse_record(next(ln for ln in lines if ln.startswith("{")))
    assert rec["type"] == "cfg_value"
    assert rec["key"] == "tx.period_ms"
    assert rec["cur"] == 100


def test_cfg_get_unknown_key():
    sim = _sim()
    assert _sack(sim.feed(build_command("CFG", "GET", "nope"))).args == (
        "CFG", "ERR", "UNKNOWN_KEY",
    )


def test_id_returns_firmware_info():
    sim = _sim()
    lines = sim.feed(build_command("ID"))
    rec = parse_record(next(ln for ln in lines if ln.startswith("{")))
    assert rec["type"] == "id"
    assert rec["fw"] == "0.1.0"


def test_stat_reports_mode_and_rails():
    sim = _sim()
    lines = sim.feed(build_command("STAT"))
    rec = parse_record(next(ln for ln in lines if ln.startswith("{")))
    assert rec["mode"] == Mode.RUN
    assert rec["rails"]["v5"] is True


def test_stat_declares_the_time_base():
    """🔴 `t` 의 기준점을 호스트가 알 수 있어야 한다 (규격 §7.1.2).

    `t` 는 time_source 에 따라 UTC epoch 이거나 부팅 후 경과 ms 다.
    명령 응답 레코드에는 time_source 를 실을 필드 마스크가 없으므로
    $STAT 이 그 답을 주는 유일한 곳이다. 없으면 호스트가 t=0 을 보고
    1970년인지 방금 부팅한 것인지 구분할 수 없다.
    """
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["time_source"] == "device_clock"
    assert rec["time_quality"] == 0


def test_command_response_t_is_uptime_not_epoch():
    """부팅 직후 t=0 은 '1970년' 이 아니라 '부팅 후 0ms' 로 정확하다."""
    sim = _sim()
    rec0 = parse_record(
        next(ln for ln in sim.feed(build_command("ID")) if ln.startswith("{"))
    )
    assert rec0["t"] == 0
    sim.tick(500)
    rec1 = parse_record(
        next(ln for ln in sim.feed(build_command("ID")) if ln.startswith("{"))
    )
    assert rec1["t"] == 500


# ----------------------------------------------------------------- 변경
def test_cfg_set_rejected_in_run_mode():
    sim = _sim()
    cmd = _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "250")))
    assert cmd.args == ("CFG", "ERR", "MODE")


def test_cfg_set_accepted_in_config_mode():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "250"))).args == (
        "CFG", "OK",
    )
    assert sim.store.get("tx.period_ms") == 250


def test_cfg_set_out_of_range():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "tx.period_ms", "1"))).args == (
        "CFG", "ERR", "RANGE",
    )


def test_cfg_set_pwr_5v_off_is_interlocked():
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "pwr.5v", "false"))).args == (
        "CFG", "ERR", "INTERLOCK",
    )


def test_cfg_save_and_reset_require_config_mode():
    sim = _sim()
    assert _sack(sim.feed(build_command("CFG", "SAVE"))).args[-1] == "MODE"
    assert _sack(sim.feed(build_command("CFG", "RESET"))).args[-1] == "MODE"


# ----------------------------------------------------- 텔레메트리 / seq
def test_tick_emits_telemetry_for_enabled_channels_only():
    sim = _sim()
    lines = sim.tick(100)
    recs = [parse_record(ln) for ln in lines if ln.startswith("{")]
    ains = [r for r in recs if r["type"] == "ain"]
    assert len(ains) == 1                      # 기본은 ain0 만 enabled
    assert ains[0]["connector_id"] == 3


def test_enabling_second_channel_adds_record():
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "ain1.enabled", "true"))
    recs = [parse_record(ln) for ln in sim.tick(100) if ln.startswith("{")]
    assert {r["connector_id"] for r in recs if r["type"] == "ain"} == {3, 4}


def test_seq_increases_monotonically_across_ticks():
    sim = _sim()
    seqs = []
    for now in (100, 200, 300):
        seqs += [
            parse_record(ln)["seq"]
            for ln in sim.tick(now)
            if ln.startswith("{")
        ]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_tick_respects_period_setting():
    """주기가 안 됐으면 텔레메트리를 내지 않는다."""
    sim = _sim()
    sim.tick(100)
    assert [ln for ln in sim.tick(150) if ln.startswith("{")] == []
    assert [ln for ln in sim.tick(200) if ln.startswith("{")] != []


def test_tick_emits_hb_once_per_second():
    sim = _sim()
    assert any(ln.startswith("$HB") for ln in sim.tick(1000))
    assert not any(ln.startswith("$HB") for ln in sim.tick(1500))
