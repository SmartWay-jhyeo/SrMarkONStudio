from host.core.config_schema import parse_catalog
from host.core.framing import build_command, parse_line
from host.core.records import parse_record
from tools.simulator.config_store import default_store
from host.core.errors import Reason
from tools.simulator.device_sim import (
    GNSS_DEMO_WARMUP_MS,
    HB_TIMEOUT_MS,
    CtlMode,
    DeviceSim,
    Mode,
)


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
    방어가 없으면 거부 응답에 4000자가 그대로 실린다.

    파이썬에서는 그저 긴 문자열이지만 C 로 옮기면 고정 버퍼 오버플로다.
    모르는 명령에 응답하는 것 자체는 맞으므로, verb 만 잘라서 돌려준다.
    """
    out = _sim().feed("$" + "A" * 4000 + "*00\r\n")
    assert len(out) == 1
    assert len(out[0]) < 40                      # 4000자가 되울리지 않는다
    assert parse_line(out[0]).args == ("?", "ERR", "UNSUPPORTED")


def test_unknown_verb_is_unsupported_not_unknown_key():
    """모르는 **명령**에는 UNSUPPORTED 다.

    🔴 UNKNOWN_KEY 는 "존재하지 않는 **설정 키**" 라는 뜻이다(규격 §5).
       명령이 없는 것과 설정 키가 없는 것은 사용자가 할 일이 다르다 —
       전자는 펌웨어를 올려야 하고 후자는 키 이름을 고쳐야 한다.
    """
    sim = _sim()
    assert _sack(sim.feed(build_command("NOPE"))).args == (
        "NOPE", "ERR", "UNSUPPORTED",
    )


def test_broken_heartbeat_gets_no_sack():
    """🔴 $HB 는 체크섬이 틀려도 $SACK 를 보내지 않는다 (규격 §3, §6.1).

    $HB 는 1 Hz 로 온다. 링크가 나빠져 계속 깨지면 초당 하나씩 $SACK 가
    쌓여 이미 나쁜 링크를 더 나쁘게 만든다. Q2 에서 2% 유실이 실측된
    링크다. 게다가 알릴 내용은 이미 전달된다 — $HB 가 계속 깨지면
    3000 ms 뒤 RUN 으로 떨어지고, 그것이 $SACK 한 줄보다 확실한 신호다.

    다른 명령은 그대로 $SACK 를 받는다. 그 구분이 이 시험의 요점이다.
    """
    sim = _sim()
    assert sim.feed("$HB*FF\r\n") == []          # 체크섬 불일치
    assert sim.feed("$HB*xx\r\n") == []          # 16진수도 아님
    assert sim.mode == Mode.RUN                  # 시각도 밀리지 않았다

    # $HB 가 아닌 명령은 여전히 알려 준다.
    out = sim.feed("$ID*FF\r\n")
    assert parse_line(out[0]).args == ("ID", "ERR", "CHECKSUM")


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


def test_stat_reports_din_current_state():
    """🔴 규격 §7.6 — din 은 엣지가 안 오는 동안에도 화면이 지금 상태를
    말할 수 있어야 한다. `$STAT` 이 그 공백을 채운다."""
    sim = _sim()
    rec = parse_record(
        next(ln for ln in sim.feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert {d["connector_id"] for d in rec["din"]} == {18, 19, 20}
    assert all(d["state"] in (0, 1) for d in rec["din"])


def test_din_starts_off():
    """부팅 직후에는 신호가 없다고 본다 — 실기기에서도 사람이 아직 아무
    신호도 넣지 않은 상태가 기본이다."""
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert all(d["state"] == 0 for d in rec["din"])


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


def test_cfg_set_pwr_5v_off_is_accepted():
    """5V 도 끌 수 있다 (사용자 확정 2026-08-14).

    🔴 막지 않는 대신 카탈로그의 `note` 가 무엇이 함께 멈추는지 말한다 —
       쿨링 팬·아날로그 수집·WS2812. 그것이 인터록을 푼 대신 남은 유일한
       안전장치라, note 가 사라지면 사용자가 모르고 끄게 된다. */
    """
    sim = _sim()
    sim.feed(build_command("HB"))
    assert _sack(sim.feed(build_command("CFG", "SET", "pwr.5v", "false"))).args == (
        "CFG", "OK",
    )
    assert sim.store.get("pwr.5v") is False
    for word in ("팬", "수집", "WS2812"):
        assert word in sim.store.items["pwr.5v"].note


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


# ------------------------------------ 수집·송신 분리 (사용자 설계 2026-08-19)
#
#   "수집은 수집대로 하고 송신은 내가 원하는 간격에 맞춰서 하는거지.
#    변수 a 라는 곳에 수집된 값을 계속 넣고, 송신 할 때는 변수 a 를
#    사용하면 어쨋든 그 안에 있던 값이 날라갈거 아니야"
#
#   시뮬레이터도 펌웨어(mk_ads1256.c·mk_i2c.c)와 같은 구조를 지켜야 한다 —
#   안 그러면 대조가 갈리고 GUI 가 시뮬레이터에서만 다르게 보인다.

def test_repeats_the_last_ain_value_when_nothing_new_was_collected():
    """🔴 채널 수집 주기(ain0.period_ms)가 송신 주기보다 길면, 새 수집이
    없어도 송신 주기마다 마지막 값이 반복된다. `t` 는 **수집 시각** 그대로다
    — 송신 시각(200)으로 바뀌면 안 된다."""
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "ain0.period_ms", "10000"))

    first = [parse_record(ln) for ln in sim.tick(100) if ln.startswith("{")]
    ain_first = next(r for r in first if r["type"] == "ain")

    second = [parse_record(ln) for ln in sim.tick(200) if ln.startswith("{")]
    ain_second = next(r for r in second if r["type"] == "ain")

    assert ain_second["t"] == ain_first["t"], "t 는 송신 시각이 아니라 수집 시각 그대로"
    assert ain_second["raw"] == ain_first["raw"], "새 수집이 없으니 값도 그대로"


def test_only_the_latest_ain_sample_is_sent_when_collection_outpaces_tx():
    """🔴 수집(ain0.period_ms=10)이 송신(tx.period_ms=500)보다 훨씬 빠르면,
    그 사이 여러 번 수집돼도 송신은 마지막 값 한 줄만 낸다."""
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "ain0.period_ms", "10"))
    sim.feed(build_command("CFG", "SET", "tx.period_ms", "500"))

    lines: list[str] = []
    for now in range(0, 501, 10):
        lines = sim.tick(now)
    recs = [parse_record(ln) for ln in lines if ln.startswith("{")]
    ains = [r for r in recs if r["type"] == "ain"]
    assert len(ains) == 1, "채널당 한 줄 — 중간 표본은 조용히 버려진다"
    assert ains[0]["t"] == 500, "가장 최근에 수집된 시각"


def test_changing_tx_period_ms_changes_the_send_interval():
    """🔴 되돌림 관점 — tx.period_ms 를 바꾸면 송신 간격이 그대로 따라온다."""
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "tx.period_ms", "50"))
    sim.tick(0)
    assert [ln for ln in sim.tick(40) if ln.startswith("{")] == [], "50ms 전에는 안 보낸다"
    assert [ln for ln in sim.tick(50) if ln.startswith("{")] != [], "50ms 가 되면 보낸다"


def test_i2c_repeats_last_value_and_never_touched_ports_stay_silent():
    """🔴 i2c 도 ain 과 같은 구조 — 마지막 값이 반복되고(`t` 는 수집 시각
    그대로), 한 번도 켠 적 없는 다른 포트는 절대 안 나간다."""
    sim = _sim()
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "i2c10.kind", "1"))       # 조도(LUX)
    sim.feed(build_command("CFG", "SET", "i2c10.enabled", "true"))
    sim.feed(build_command("CFG", "SET", "i2c10.period_ms", "10000"))

    first = [parse_record(ln) for ln in sim.tick(100) if ln.startswith("{")]
    i2c_first = next(r for r in first if r["type"] == "i2c")

    second = [parse_record(ln) for ln in sim.tick(200) if ln.startswith("{")]
    i2c_second = next(r for r in second if r["type"] == "i2c")

    assert i2c_second["t"] == i2c_first["t"], "수집 시각 그대로 반복된다"
    assert i2c_second["value"] == i2c_first["value"], "새 수집이 없으니 값도 그대로"
    assert not any(
        r["type"] == "i2c" and r["connector_id"] != 10 for r in first + second
    ), "켠 적 없는 다른 포트는 값이 없으니 절대 나가지 않는다"


# ------------------------------------------------------------------- din
#
# 🔴 시뮬레이터에는 옵토가 없다 — 실기기는 사람이 신호선을 흔들어야
#    바뀐다. 여기서 도는 토글은 화면 확인용 **데모**일 뿐이라, ain 처럼
#    매 주기 나오면 안 되고 아주 드물게만 바뀌어야 한다(규격 §7.6).


def test_din_edges_are_rare_not_periodic():
    """짧은 구간(2초)에는 한 번도 안 바뀌어야 한다 — ain 과 다르다."""
    sim = _sim()
    recs = []
    for now in range(0, 2000, 100):
        recs += [parse_record(ln) for ln in sim.tick(now) if ln.startswith("{")]
    assert [r for r in recs if r["type"] == "din"] == []


def test_din_toggles_eventually_and_only_on_change():
    from tools.simulator.device_sim import DIN_CONNECTORS, DIN_DEMO_PERIOD_MS

    sim = _sim()
    recs = []
    for now in range(0, DIN_DEMO_PERIOD_MS * 2 + 1000, 100):
        recs += [parse_record(ln) for ln in sim.tick(now) if ln.startswith("{")]
    dins = [r for r in recs if r["type"] == "din"]
    assert dins, "충분히 오래 돌렸는데 한 번도 안 바뀌었다"
    assert {r["connector_id"] for r in dins} <= set(DIN_CONNECTORS)

    by_conn: dict[int, list[int]] = {}
    for r in dins:
        by_conn.setdefault(r["connector_id"], []).append(r["state"])
    for cid, states in by_conn.items():
        # 🔴 상태 변화에만 나온다 — 같은 값이 연달아 오면 "주기 송신"으로
        #    되돌아간 것이다.
        assert all(a != b for a, b in zip(states, states[1:])), (cid, states)


def test_stat_din_matches_the_last_edge_emitted():
    from tools.simulator.device_sim import DIN_DEMO_PERIOD_MS

    sim = _sim()
    last_state: dict[int, int] = {}
    for now in range(0, DIN_DEMO_PERIOD_MS + 500, 100):
        for ln in sim.tick(now):
            if not ln.startswith("{"):
                continue
            rec = parse_record(ln)
            if rec["type"] == "din":
                last_state[rec["connector_id"]] = rec["state"]

    stat = parse_record(
        next(ln for ln in sim.feed(build_command("STAT")) if ln.startswith("{"))
    )
    stat_state = {d["connector_id"]: d["state"] for d in stat["din"]}
    for cid, state in last_state.items():
        assert stat_state[cid] == state


# --------------------------------------------------------- 제어 모드 (§6.4)

def _config_sim() -> DeviceSim:
    """하트비트를 넣어 CONFIG 로 만든 시뮬레이터."""
    sim = _sim()
    sim.feed(build_command("HB"))
    return sim


def test_boots_in_active_control_mode():
    """🔴 부팅 기본값은 ACTIVE 다. 보드는 혼자서도 제 일을 해야 한다."""
    assert _sim().ctl_mode == CtlMode.ACTIVE


def test_test_mode_needs_someone_watching():
    """🔴 RUN 에서는 TEST 로 못 들어간다.

    사람이 보지 않는데 테스트일 수 없다. TEST 의 안전장치가 하트비트
    데드맨이므로, 하트비트 없이 TEST 에 들어가면 그 안전장치가 없다.
    """
    sim = _sim()                                   # RUN
    ack = _sack(sim.feed(build_command("MODE", "TEST")))
    assert ack.args[-1] == Reason.MODE
    assert sim.ctl_mode == CtlMode.ACTIVE


def test_entering_and_leaving_test_mode():
    sim = _config_sim()
    assert _sack(sim.feed(build_command("MODE", "TEST"))).args[-1] == "OK"
    assert sim.ctl_mode == CtlMode.TEST
    assert _sack(sim.feed(build_command("MODE", "ACTIVE"))).args[-1] == "OK"
    assert sim.ctl_mode == CtlMode.ACTIVE


def test_an_unknown_control_mode_is_rejected():
    sim = _config_sim()
    ack = _sack(sim.feed(build_command("MODE", "PROBING")))
    assert ack.args[-1] == Reason.RANGE
    assert sim.ctl_mode == CtlMode.ACTIVE


def test_stat_reports_both_axes():
    """🔴 두 축은 독립이다. 호스트가 둘 다 감시할 수 있어야 한다."""
    sim = _config_sim()
    sim.feed(build_command("MODE", "TEST"))
    rec = parse_record(next(ln for ln in sim.feed(build_command("STAT"))
                            if ln.startswith("{")))
    assert rec["mode"] == Mode.CONFIG
    assert rec["ctl_mode"] == CtlMode.TEST


def test_test_mode_does_not_save_outputs():
    """🔴 벤치에서 밸브를 한 번 열어 본 것이 플래시에 남으면 안 된다."""
    sim = _config_sim()
    sim.feed(build_command("MODE", "TEST"))
    sim.feed(build_command("CFG", "SET", "pwr.24v", "true"))
    sim.feed(build_command("CFG", "SAVE"))
    assert sim.store.saved_value("pwr.24v") is not True


def test_active_mode_saves_outputs():
    sim = _config_sim()
    sim.feed(build_command("CFG", "SET", "pwr.24v", "true"))
    sim.feed(build_command("CFG", "SAVE"))
    assert sim.store.saved_value("pwr.24v") is True


def test_losing_the_host_ends_the_test_and_drops_the_outputs():
    """🔴 하트비트가 이미 데드맨이다. 사람이 안 보면 테스트 출력은 꺼진다."""
    sim = _config_sim()
    sim.feed(build_command("MODE", "TEST"))
    sim.feed(build_command("CFG", "SET", "pwr.24v", "true"))
    assert sim.store.get("pwr.24v") is True

    sim.tick(HB_TIMEOUT_MS + 1)                    # 호스트가 사라졌다

    assert sim.mode == Mode.RUN
    assert sim.ctl_mode == CtlMode.ACTIVE
    assert sim.store.get("pwr.24v") is False


def test_the_safe_state_is_the_default_not_zero():
    """🔴 안전 상태는 "전부 꺼짐" 이 아니라 **정의된 상태**다.

    `pwr.5v` 의 기본값은 켜짐이다 — 쿨링 팬과 ADS1256 의 아날로그 전원이
    거기 걸려 있다. 테스트를 끝냈다고 팬을 세우면 안 된다.
    """
    sim = _config_sim()
    sim.feed(build_command("MODE", "TEST"))
    sim.feed(build_command("CFG", "SET", "pwr.5v", "false"))
    sim.tick(HB_TIMEOUT_MS + 1)
    assert sim.store.get("pwr.5v") is True


def test_active_mode_keeps_outputs_when_the_host_leaves():
    """운전 중이면 호스트가 사라져도 출력은 그대로다 — 보드가 혼자 돈다."""
    sim = _config_sim()
    sim.feed(build_command("CFG", "SET", "pwr.24v", "true"))
    sim.tick(HB_TIMEOUT_MS + 1)
    assert sim.store.get("pwr.24v") is True


# --------------------------------------------------- $GNSS (규격 §4.1, Phase 3)


def test_gnss_command_requires_config_mode():
    """규격 §4.1 — $GNSS 는 CONFIG 전용이다."""
    sim = _sim()                                   # RUN
    ack = _sack(sim.feed(build_command("GNSS", "LOG GPRMC ONTIME 1")))
    assert ack.args == ("GNSS", "ERR", Reason.MODE)


def test_gnss_command_accepted_in_config_mode():
    sim = _config_sim()
    ack = _sack(sim.feed(build_command("GNSS", "LOG GPRMC ONTIME 1")))
    assert ack.args == ("GNSS", "OK")


def test_gnss_command_rejects_empty_text():
    sim = _config_sim()
    ack = _sack(sim.feed(build_command("GNSS")))
    assert ack.args == ("GNSS", "ERR", Reason.RANGE)


def test_gnss_command_preserves_embedded_comma():
    """🔴 규격 개정 — $GNSS 는 일반 인자 쪼개기(쉼표 분할)를 거치지
    않는다. `build_command("GNSS", "LOG", "GPRMC")` 는 전선 위에서
    "GNSS,LOG,GPRMC" 가 되는데, 쉼표가 있어도 한 덩어리 텍스트로 남아야
    한다."""
    sim = _config_sim()
    ack = _sack(sim.feed(build_command("GNSS", "LOG", "GPRMC")))
    assert ack.args == ("GNSS", "OK")
    assert sim._last_gnss_cmd == "LOG,GPRMC"


def test_gnss_command_rejects_control_characters():
    """🔴 콤마가 아닌 제어문자(탭)는 build_command 로는 못 만든다
    (host.core.framing 이 거부한다) — 원시 줄로 직접 넣어야 이 경로를
    시험할 수 있다. 체크섬은 손으로 맞췄다."""
    sim = _config_sim()
    ack = _sack(sim.feed("$GNSS,LOG\tA*29\r\n"))
    assert ack.args == ("GNSS", "ERR", Reason.RANGE)


def test_gnss_command_forwards_the_real_um981_pps_command():
    """🔴 실기기에서 막혔던 명령. 46/47자짜리 전체 파라미터를 요구한다
    (docs/datasheet/Unicore_N4_Commands.pdf p.21 §4.3 · p.22 Table 4-6).
    줄인 "CONFIG PPS ENABLE3"·"CONFIG PPS ENABLE2 GPS" 는 실기기에서
    "PARSING FAILD FIELD OUT OF RANGE, Too less field!" 로 거부됐다."""
    text = "CONFIG PPS ENABLE2 GPS POSITIVE 500000 1000 0 0"
    sim = _config_sim()
    ack = _sack(sim.feed(build_command("GNSS", text)))
    assert ack.args == ("GNSS", "OK")
    assert sim._last_gnss_cmd == text


def test_gnss_command_preserves_spaces_verbatim():
    """공백이 포함된 원문이 쪼개지지 않고 그대로 전달된다."""
    text = "LOG GPRMC ONTIME 1"
    sim = _config_sim()
    ack = _sack(sim.feed(build_command("GNSS", text)))
    assert ack.args == ("GNSS", "OK")
    assert sim._last_gnss_cmd == text


def test_gnss_command_accepts_exactly_96_bytes():
    from host.core.limits import MAX_GNSS_TEXT_BYTES

    sim = _config_sim()
    text = "A" * MAX_GNSS_TEXT_BYTES
    ack = _sack(sim.feed(build_command("GNSS", text)))
    assert ack.args == ("GNSS", "OK")
    assert sim._last_gnss_cmd == text


def test_gnss_command_over_96_bytes_is_rejected():
    """규격 §3.1/§4.1 — $GNSS 전용 상한은 96바이트(MAX_GNSS_TEXT_BYTES)다.

    🔴 `build_command` 는 이미 보내기 전에 막는다(host.core.framing) — 이
    시험은 그 관문을 `build_line` 으로 우회해 시뮬레이터 자신이 길이를
    검사하는지 본다. C 펌웨어는 고정 버퍼라 이 경우 파싱 단계에서 조용히
    버려지지만(mk_framing.c 의 mk_parse_line), 파이썬 문자열에는 그런
    하드웨어적 한계가 없으므로 시뮬레이터가 스스로 거부해야 한다 —
    조용히 받아들이면 안 된다."""
    from host.core.framing import build_line
    from host.core.limits import MAX_GNSS_TEXT_BYTES

    sim = _config_sim()
    ack = _sack(sim.feed(build_line("GNSS," + "0" * (MAX_GNSS_TEXT_BYTES + 1))))
    assert ack.args == ("GNSS", "ERR", Reason.RANGE)


# ------------------------------------------ $STAT 의 gnss 초기화 진단(§4.1.1)


def test_stat_gnss_init_fields_default_to_false():
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["gnss"]["init_sent"] is False
    assert rec["gnss"]["init_exhausted"] is False
    assert rec["gnss"]["sentence_seen"] is False


def test_stat_gnss_init_fields_reflect_enabling():
    """🔴 [단순화, 시뮬레이터] 실기기는 재시도·타임아웃이 있지만(규격
    §4.1.1) 시뮬레이터에는 진짜 모듈이 없다 — `gnss.enabled` 가 켜지면
    "보내고 즉시 받았다"로 흉내 낸다. `init_exhausted` 는 항상 거짓이다."""
    sim = _config_sim()
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "true"))
    sim.tick(0)
    rec = parse_record(
        next(ln for ln in sim.feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["gnss"]["init_sent"] is True
    assert rec["gnss"]["init_exhausted"] is False
    assert rec["gnss"]["sentence_seen"] is True


# ------------------------ $STAT 의 pps_raw_* vs pps_age_ms (규격 §7.4) ----
#
# 🔴 실기기(UM981, 실내·fix 없음)에서 PPS 는 1초 간격으로 계속 들어오는데
#    RMC 가 전부 무효(V)라 짝지어지지 않아 pps_age_ms 가 계속 null 이었다
#    — 배선·캡처는 정상인데 화면은 "PPS 가 안 온다"로 보였다. 펌웨어와
#    같은 구분을 시뮬레이터도 내야 GUI 가 시뮬레이터에서만 맞는 상황을
#    막는다(CLAUDE.md — "설정 항목은 보드에만" 원칙과 같은 결로, 여기서는
#    "시간축 진단은 펌웨어·시뮬레이터 둘 다").


def test_stat_gnss_pps_raw_fields_null_when_disabled():
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["gnss"]["pps_raw_age_ms"] is None
    assert rec["gnss"]["pps_raw_count"] == 0
    assert rec["gnss"]["pps_unpaired_reason"] is None


def test_stat_gnss_pps_raw_fields_during_warmup_report_no_pps():
    """🔴 [단순화, 시뮬레이터] 워밍업 동안은 RMC 는 오는데(등급 gnss_nmea)
    아직 원시 PPS 캡처가 없다고 흉내 낸다 — pps_unpaired_reason 이
    "no_pps" 인 것은 실기기의 MK_TIMEAX_PPS_UNPAIRED_NO_PPS 와 같은 뜻이다
    (mk_timeax.c: 유효한 RMC 는 왔지만 짝지을 원시 PPS 가 없다)."""
    sim = _config_sim()
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "true"))
    sim.tick(0)
    rec = parse_record(
        next(ln for ln in sim.feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["time_source"] == "gnss_nmea"
    assert rec["gnss"]["pps_age_ms"] is None
    assert rec["gnss"]["pps_raw_age_ms"] is None
    assert rec["gnss"]["pps_raw_count"] == 0
    assert rec["gnss"]["pps_unpaired_reason"] == "no_pps"


def test_stat_gnss_pps_raw_fields_present_when_locked():
    """🔴 되돌림 검사를 겸한다 — pps_age_ms 와 pps_raw_age_ms 를 다시 하나로
    합치면 이 시험과 워밍업 시험 중 하나는 반드시 깨진다(워밍업에서는
    전자가 null 인데 후자는 아니어야 하고, 여기서는 둘 다 값이 있어야
    한다)."""
    sim = _config_sim()
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "true"))
    sim.tick(0)                              # 여기서 워밍업 시작점(anchor)이 잡힌다
    sim.tick(GNSS_DEMO_WARMUP_MS + 500)       # 워밍업을 넘겼다 — 잠긴다
    rec = parse_record(
        next(ln for ln in sim.feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert rec["time_source"] == "gnss_pps"
    assert rec["gnss"]["pps_age_ms"] is not None
    assert rec["gnss"]["pps_raw_age_ms"] == rec["gnss"]["pps_age_ms"]
    assert rec["gnss"]["pps_raw_count"] >= 1
    assert rec["gnss"]["pps_unpaired_reason"] is None


# ------------------------ $STAT 의 lcd (규격 §7.4) -------------------------
#
# 🔴 실기기에서 "노이즈 타면 픽셀이 다 깨지는데?" 가 나왔고, 깨진 뒤 저절로
#    안 돌아오는 것이 문제였다(2026-08-19). 보드가 스스로 회복하게 만들었으니
#    **회복이 몇 번 일어났는지**가 밖에서 보여야 한다 — 그 수가 없으면 이
#    문제가 해결됐는지 덮였는지 아무도 모른다.
#
#    시뮬레이터에는 패널이 없다. 그래도 **모양은 내야** 한다 — GUI 가
#    시뮬레이터를 상대로 만들어지고 실물에서 돌기 때문이다. 값을 지어내지는
#    않는다(전부 0, readback 은 null).


def test_stat_carries_the_lcd_recovery_counters():
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    assert set(rec["lcd"]) == {
        "epoch", "reinit", "redraw", "verify_ok", "verify_fail",
        "rejected", "readback",
    }


def test_the_simulator_does_not_invent_lcd_recovery_numbers():
    """🔴 패널이 없으므로 전부 0 이고 readback 은 **null** 이다.

    readback 을 False 로 내면 화면이 "되읽기 안 됨" 이라고 단정하게 되고,
    사용자는 멀쩡한 배선을 뜯어 본다. "아직 안 물어봤다" 와 "못 믿는다" 는
    다르다 — pps_age_ms 의 null 과 같은 결이다(규격 §7.4).
    """
    rec = parse_record(
        next(ln for ln in _sim().feed(build_command("STAT")) if ln.startswith("{"))
    )
    lcd = rec["lcd"]
    assert lcd["readback"] is None
    for key in ("epoch", "reinit", "redraw", "verify_ok", "verify_fail",
                "rejected"):
        assert lcd[key] == 0, f"{key} 를 지어냈다"
