"""시뮬레이터의 `t` 시간축 변환 — 펌웨어 mk_timeax/mk_telem 과 같은 규칙을
Python 쪽에서도 지키는지 본다(브리프 §3 — "시뮬레이터도 같은 규칙을 따라야
한다", crosscheck 가 구조를 대조한다).

규칙 (규격 §7.1.2, firmware/stage1/tests/test_telem.c 와 같은 세 가지):

  1) device_clock 이면 `t` 는 여전히 획득 시각(부팅 ms) 그대로다.
  2) gnss_pps/gnss_nmea 로 오르면 `t` 는 획득 시각을 UTC epoch_ms 로 바꾼
     값이다 — 송신 시각이 아니다.
  3) 등급이 오르내려도 `t` 는 뒤로 가지 않는다.

시뮬레이터에는 실제 GNSS 가 없어 "UTC" 는 지어낸 값이다 — device_sim.py 의
`GNSS_DEMO_EPOCH_MS` 참고. 진짜 UTC 인 척하지 않는다(설계 지시 —
"가짜 fix 를 진짜처럼 내지 마라").
"""
import json

from host.core.framing import build_command
from tools.simulator.config_store import default_store
from tools.simulator.device_sim import DeviceSim, GNSS_DEMO_EPOCH_MS


def _records(lines: list[str], rtype: str) -> list[dict]:
    return [json.loads(ln) for ln in lines
            if ln.startswith("{") and f'"type":"{rtype}"' in ln]


def _sim_with_ain0(period_ms: str = "10") -> DeviceSim:
    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))     # CONFIG 로 들어가야 SET 이 먹는다
    sim.feed(build_command("CFG", "SET", "ain0.enabled", "1"))
    sim.feed(build_command("CFG", "SET", "ain0.period_ms", period_ms))
    sim.feed(build_command("CFG", "SET", "tx.period_ms", "10"))  # min=10
    return sim


def test_t_stays_boot_ms_while_gnss_is_off():
    sim = _sim_with_ain0()
    recs = _records(sim.tick(10), "ain")
    assert recs, "ain 레코드가 나와야 시험이 성립한다"
    for rec in recs:
        assert rec["time_source"] == "device_clock"
        assert rec["t"] < 1_000_000, "device_clock 이면 t 는 부팅 ms 그대로다"


def test_t_becomes_a_large_epoch_once_gnss_enabled():
    sim = _sim_with_ain0()
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "1"))

    recs = _records(sim.tick(10), "ain")
    assert recs
    for rec in recs:
        # gnss.enabled 직후는 gnss_nmea(워밍업) 단계지만 규격 §7.1.2 상
        # device_clock 이 아니면 이미 t 는 UTC 다.
        assert rec["time_source"] != "device_clock"
        assert rec["t"] > 1_000_000_000_000, (
            "gnss_nmea 로만 올라도 t 는 이미 UTC 스러운 큰 값이어야 한다"
        )


def test_t_uses_acquisition_time_not_send_time():
    """수집을 사실상 한 번만 일으키고(큰 ain0.period_ms), 송신은 여러 번
    일으켜(작은 tx.period_ms) t 가 매번 같은지 본다 — 송신 시각을 실었다면
    두 송신의 now_ms(60, 120)가 다르므로 t 도 달라져야 한다."""
    sim = _sim_with_ain0(period_ms="60000")      # 최댓값 — 사실상 한 번만 수집
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "1"))
    sim.feed(build_command("CFG", "SET", "tx.period_ms", "50"))

    sim.tick(20)                    # GNSS anchor 성립 + 수집(둘 다 t=20)
    recs1 = _records(sim.tick(60), "ain")     # 1차 송신(주기 50 도달)
    recs2 = _records(sim.tick(120), "ain")    # 2차 송신 — 새 수집 없음

    assert recs1 and recs2
    assert recs1[0]["t"] == GNSS_DEMO_EPOCH_MS, (
        "anchor 가 선 바로 그 순간(dev_ms=20)에 수집됐으므로 변환값이 "
        "anchor epoch 그대로다"
    )
    assert recs2[0]["t"] == recs1[0]["t"], (
        "두 번째 송신(now_ms=120)도 첫 송신(now_ms=60)과 t 가 같다 — "
        "송신 시각이 아니라 획득 시각(20)이 그대로 반복된다"
    )


def test_t_never_goes_backward_when_gnss_is_disabled_after_being_enabled():
    sim = _sim_with_ain0()
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "1"))

    recs_locked = _records(sim.tick(20), "ain")
    assert recs_locked
    t_locked = recs_locked[-1]["t"]
    assert t_locked > 1_000_000_000_000

    # GNSS 를 끈다 — 시뮬레이터는 신선도 감쇠 없이 곧장 device_clock 으로
    # 내린다(_gnss_time_state 참고).
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "0"))
    recs_after = _records(sim.tick(30), "ain")
    assert recs_after
    for rec in recs_after:
        assert rec["time_source"] == "device_clock"
        assert rec["t"] >= t_locked, (
            "GNSS 가 꺼져 device_clock 으로 내려가도 t 는 이미 내보낸 값보다 "
            "뒤로 가지 않는다 — 보정이 없으면 부팅 ms(수십)로 뚝 떨어진다"
        )


def test_din_and_i2c_follow_the_same_conversion_as_ain():
    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "gnss.enabled", "1"))
    sim.feed(build_command("CFG", "SET", "i2c10.enabled", "1"))
    sim.feed(build_command("CFG", "SET", "i2c10.kind", "1"))     # lux
    sim.feed(build_command("CFG", "SET", "i2c10.period_ms", "10"))

    # din 은 DIN_DEMO_PERIOD_MS(9000) 마다 데모 토글을 낸다 — 한 구간 이상
    # 돌려 강제로 상태 변화를 겪게 한다.
    lines: list[str] = []
    for t in range(0, 9200, 20):
        lines += sim.tick(t)

    din_recs = _records(lines, "din")
    i2c_recs = _records(lines, "i2c")
    assert din_recs, "din 레코드가 나와야 한다"
    assert i2c_recs, "i2c 레코드가 나와야 한다"
    for rec in din_recs + i2c_recs:
        assert rec["time_source"] != "device_clock"
        assert rec["t"] > 1_000_000_000_000, (
            "din·i2c 도 ain 과 같은 규칙 — gnss 등급이 오르면 t 가 UTC 로 "
            "바뀐다"
        )


def test_a_slower_channels_repeated_stale_sample_keeps_its_own_t():
    """🔴 [되돌림으로 찾은 회귀] 빠른 채널(ain)이 큰 t_ms 로 먼저 나간 뒤,
    느린 포트(i2c)가 옛(작은) t_ms 표본을 반복하면 그 표본의 t 가
    호출마다 달라지면 안 된다 — device_sim.py `_convert_t()` 의
    되돌림 검사 주석과 firmware/stage1/tests/test_timeax.c 의
    test_monotonic_does_not_clamp_an_older_channels_stale_sample 과 같은
    회귀를 여기서도 못 박는다."""
    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))
    sim.feed(build_command("CFG", "SET", "ain0.enabled", "1"))
    sim.feed(build_command("CFG", "SET", "ain0.period_ms", "10"))
    sim.feed(build_command("CFG", "SET", "i2c10.enabled", "1"))
    sim.feed(build_command("CFG", "SET", "i2c10.kind", "1"))     # lux
    sim.feed(build_command("CFG", "SET", "i2c10.period_ms", "10000"))
    sim.feed(build_command("CFG", "SET", "tx.period_ms", "10"))

    first = _records(sim.tick(100), "i2c")
    assert first
    t1 = first[0]["t"]

    # ain0(주기 10ms)는 계속 새로 수집되며 t_ms 가 커지지만, i2c10(주기
    # 10000ms)은 재수집되지 않아 같은 표본을 반복한다.
    second = _records(sim.tick(200), "i2c")
    assert second
    t2 = second[0]["t"]

    assert t2 == t1, (
        "같은 i2c 표본이 반복되는 동안 다른(더 빠른) 채널이 앞서 나가도 "
        "이 표본의 t 는 바뀌지 않는다"
    )


def test_time_source_cannot_be_masked_off():
    """tx.fields 를 전부 꺼도 time_source 는 실린다 — t 의 뜻을 구분하는
    유일한 필드라 마스크 밖에 있다(config_store.py FIELD_BITS 참고)."""
    sim = _sim_with_ain0()
    sim.feed(build_command("CFG", "SET", "tx.fields", "0"))

    recs = _records(sim.tick(50), "ain")
    assert recs
    for rec in recs:
        assert "time_source" in rec, "tx.fields 를 전부 꺼도 time_source 는 실린다"
