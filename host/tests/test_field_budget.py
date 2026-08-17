"""필드 선택이 전선에 얼마를 쓰는지 계산하는 층의 시험. Qt 없이 돈다."""

import json

import pytest

from host.gui.field_budget import (
    BITS_PER_BYTE_ON_WIRE,
    Budget,
    budget_message,
    capacity_bytes_per_s,
    compute_budget,
    format_bytes_per_s,
    measure_line,
    sample_record,
)


def _rec(**extra):
    rec = {"schema_ver": 3, "seq": 1, "t": 1772200855875, "type": "ain"}
    rec.update(extra)
    return rec


# ----------------------------------------------------------------- 용량

def test_capacity_accounts_for_start_and_stop_bits():
    """🔴 baud 를 8 로 나누면 25% 를 더 잡는다.

    8N1 은 한 바이트에 10 비트가 든다. 25% 는 정확히 여유가 없을 때
    사라지는 크기다.
    """
    assert capacity_bytes_per_s(115200) == 11520.0
    assert capacity_bytes_per_s(115200) != 115200 / 8
    assert BITS_PER_BYTE_ON_WIRE == 10


def test_capacity_scales_with_baud():
    assert capacity_bytes_per_s(2_000_000) == 200_000.0


# ----------------------------------------------------------------- 표본 줄

def test_sample_always_carries_the_fields_that_cannot_be_turned_off():
    """규격 §7.1 — `schema_ver` · `seq` · `t` · `type` 은 마스크로 못 끈다."""
    rec = sample_record([])
    assert set(rec) == {"schema_ver", "seq", "t", "type"}


def test_sample_carries_exactly_what_was_selected():
    rec = sample_record(["ma", "connector_id"])
    assert "ma" in rec and "connector_id" in rec
    assert "raw" not in rec and "unit" not in rec


def test_an_unknown_field_is_counted_not_dropped():
    """🔴 모르는 필드를 조용히 빼면 **적게** 잡는다.

    보드가 규격을 올려 필드를 추가하면 호스트는 그 이름을 모른다. 그때
    빼고 재면 화면은 여유가 있다고 말하는데 실제로는 없다 — 이 모듈이
    막으려는 실패가 정확히 그것이다. 모르면 넉넉히 잡는다.
    """
    known = measure_line(sample_record(["ma"]))
    unknown = measure_line(sample_record(["ma", "frobnicate"]))
    assert unknown > known


def test_more_float_digits_make_a_longer_line():
    """🔴 자릿수가 전선을 먹는다. `tx.float_digits` 를 올리면 그만큼 늘어난다."""
    short = measure_line(sample_record(["ma", "value"], float_digits=2))
    long = measure_line(sample_record(["ma", "value"], float_digits=6))
    assert long > short


def test_sample_is_not_optimistic_about_width():
    """🔴 넉넉한 값으로 잰다. 좁은 표본으로 재면 실제보다 작게 나오고,
       작게 나온 만큼이 정확히 여유가 없을 때 문제가 된다."""
    rec = sample_record(["raw", "connector_id"])
    assert rec["raw"] >= 8_000_000        # 24비트 ADC 의 큰 쪽
    assert rec["connector_id"] >= 9       # J9 — 커넥터 번호의 큰 쪽


# ----------------------------------------------------------------- 줄 길이

def test_measure_line_uses_the_wire_format():
    """🔴 보드가 쓰는 압축 형식으로 재야 한다.

    json.dumps 기본값은 `", "` 와 `": "` 로 공백을 넣어 실제보다 크게
    나온다. 크게 나오는 쪽이 안전해 보이지만, 그러면 사용자가 켤 수 있는
    필드를 못 켜게 막는다.
    """
    rec = _rec(raw=8388608, ma=12.0041)
    compact = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    assert measure_line(rec) == len(compact.encode()) + 1   # + 줄바꿈


def test_measure_line_counts_the_newline():
    """NDJSON 은 줄 하나가 레코드 하나다. 줄바꿈도 전선을 지나간다."""
    rec = _rec()
    body = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    assert measure_line(rec) == len(body) + 1


def test_measure_line_counts_bytes_not_characters():
    """전선을 지나는 것은 바이트다.

    전선 값은 ASCII 뿐이지만(규격 §3), 어쩌다 non-ASCII 가 들어오면 글자
    수로 재는 구현은 실제보다 작게 답한다 — 작게 답하는 쪽이 위험하다.
    """
    rec = _rec(unit="℃")
    body = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    assert measure_line(rec) == len(body.encode("utf-8")) + 1
    assert measure_line(rec) > len(body) + 1


def test_more_fields_means_longer_line():
    small = measure_line(_rec(raw=1))
    big = measure_line(_rec(raw=1, ma=12.0, value=3.4, unit="bar",
                            status=0, connector_id=3))
    assert big > small


# ----------------------------------------------------------------- 사용량

def test_bytes_per_s_multiplies_channels_and_period():
    b = compute_budget(_rec(raw=1), channels_enabled=7, period_ms=100,
                       baud=115200)
    assert b.lines_per_s == pytest.approx(70.0)
    assert b.bytes_per_s == pytest.approx(b.line_bytes * 70.0)


def test_zero_channels_uses_nothing():
    b = compute_budget(_rec(), channels_enabled=0, period_ms=100, baud=115200)
    assert b.bytes_per_s == 0.0
    assert b.ratio == 0.0


def test_zero_period_does_not_claim_infinity():
    """🔴 화면에서 주기를 지우는 동안 잠깐 0 이 된다.

    그때 무한대를 외치면 사용자는 자기가 무언가를 망가뜨린 줄 안다.
    """
    b = compute_budget(_rec(), channels_enabled=7, period_ms=0, baud=115200)
    assert b.bytes_per_s == 0.0
    b = compute_budget(_rec(), channels_enabled=7, period_ms=-5, baud=115200)
    assert b.bytes_per_s == 0.0


def test_headroom():
    b = compute_budget(_rec(raw=1), channels_enabled=1, period_ms=1000,
                       baud=115200)
    assert b.headroom_bytes_per_s == pytest.approx(
        b.capacity_bytes_per_s - b.bytes_per_s
    )


# ----------------------------------------------------------------- 판정

def _budget(bps: float, cap: float) -> Budget:
    return Budget(line_bytes=100, lines_per_s=bps / 100,
                  bytes_per_s=bps, capacity_bytes_per_s=cap)


def test_over_capacity_at_exactly_100_percent():
    """정확히 꽉 찬 것은 이미 못 보내는 것이다 — 프레이밍 여유도 없다."""
    assert _budget(1000, 1000).over_capacity is True
    assert _budget(999, 1000).over_capacity is False


def test_tight_band():
    assert _budget(699, 1000).tight is False
    assert _budget(700, 1000).tight is True
    assert _budget(999, 1000).tight is True
    assert _budget(1000, 1000).tight is False       # 이미 초과라 tight 가 아니다


def test_message_says_what_to_reduce():
    """🔴 대역폭 초과 만 띄우면 무엇을 끄거나 늦춰야 하는지 모른다.

    규격 §5.1 이 CAPACITY 거부에 요구값과 가용값을 함께 담으라고 한 것과
    같은 이유다.
    """
    level, text = budget_message(_budget(20000, 11520))
    assert level == "fault"
    assert "필드를 줄이거나 주기를 늘려야" in text
    assert "20.0 kB/s" in text        # 요구
    assert "11.5 kB/s" in text        # 가용


def test_warn_message_mentions_the_other_traffic():
    """명령·응답·하트비트가 같은 선을 쓴다는 것이 여유를 두는 이유다."""
    level, text = budget_message(_budget(9000, 11520))
    assert level == "warn"
    assert "명령" in text


def test_ok_message_is_quiet():
    level, text = budget_message(_budget(1000, 11520))
    assert level == "ok"
    assert "초과" not in text
    assert "여유가 없" not in text


def test_zero_capacity_is_not_a_crash():
    b = _budget(100, 0)
    assert b.ratio == float("inf")
    assert b.over_capacity is True
    level, _ = budget_message(b)
    assert level == "fault"


# ----------------------------------------------------------------- 문구

@pytest.mark.parametrize("value,want", [
    (0.0, "0 B/s"),
    (999.0, "999 B/s"),
    (1000.0, "1.0 kB/s"),
    (11520.0, "11.5 kB/s"),
    (999_999.0, "1000.0 kB/s"),
    (1_000_000.0, "1.00 MB/s"),
])
def test_format_bytes_per_s(value, want):
    assert format_bytes_per_s(value) == want


# ----------------------------------------------------------------- 실제 설정

def test_default_telemetry_fits_115200():
    """기본 설정(7채널 100 ms, 기본 필드)이 115200 에 들어가는지.

    들어가지 않으면 기본값 자체가 잘못된 것이다 — 사용자가 아무것도 바꾸지
    않았는데 유실이 나면 안 된다.
    """
    from tools.simulator.config_store import default_store
    from tools.simulator.telemetry import build_ain_record

    store = default_store()
    rec = build_ain_record(store, channel=0, seq=1, t_ms=1772200855875,
                           raw=8388608, capture_counter=123456789)
    b = compute_budget(rec, channels_enabled=7,
                       period_ms=int(store.get("tx.period_ms")), baud=115200)
    assert not b.over_capacity, budget_message(b)[1]


def test_all_fields_on_is_measurable():
    """전부 켰을 때가 얼마인지 실제로 잴 수 있어야 한다.

    사용자가 고르기 전에 결과를 보는 것이 이 화면의 요점이다.
    """
    from tools.simulator.config_store import FIELD_BITS, default_store
    from tools.simulator.telemetry import build_ain_record

    store = default_store()
    store.set("tx.fields", str((1 << len(FIELD_BITS)) - 1))
    rec = build_ain_record(store, channel=0, seq=1, t_ms=1772200855875,
                           raw=8388608, capture_counter=123456789)
    b = compute_budget(rec, channels_enabled=7, period_ms=100, baud=115200)
    assert b.line_bytes > 0
    assert b.bytes_per_s > 0


def _catalog_bytes_and_budget(baud: int):
    from host.core.framing import build_command
    from tools.simulator.config_store import default_store
    from tools.simulator.device_sim import DeviceSim
    from tools.simulator.telemetry import build_ain_record

    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))
    catalog = sum(len(ln.encode("utf-8")) + 1
                  for ln in sim.feed(build_command("CFG", "LIST")))
    store = default_store()
    rec = build_ain_record(store, channel=0, seq=1, t_ms=1772200855875,
                           raw=8388608, capture_counter=123456789)
    return catalog, compute_budget(
        rec, channels_enabled=7,
        period_ms=int(store.get("tx.period_ms")), baud=baud,
    )


def test_settings_screen_opens_quickly_at_the_default_baud():
    """🔴 설정 화면이 실용적인 시간 안에 열려야 한다.

    규격 §6.3 이 "수집과 전송은 두 모드에서 모두 계속된다"고 못박고 있으므로
    `$CFG,LIST` 카탈로그는 텔레메트리가 쓰고 남은 대역폭으로 흘러야 한다.

    115200 에서는 여유가 초당 600 바이트뿐이라 **16.7 초**가 걸렸다 —
    사용자 요구의 핵심("USB 를 연결했을 때만 GUI 로 설정하고 제어한다")이
    기본 설정에서 성립하지 않았다. 921600 으로 올려 해결했고 실기기에서
    확인했다 (`docs/measurements/2026-08-14_baud_921600.md`).

    이 시험은 그 해결이 되돌아가지 않게 붙든다.
    """
    from host.core.limits import DEFAULT_BAUD

    catalog, b = _catalog_bytes_and_budget(DEFAULT_BAUD)
    assert catalog > 5000, "카탈로그가 갑자기 작아졌다 — 계산을 다시 보라"
    spare = b.headroom_bytes_per_s
    assert spare > 0, "텔레메트리만으로 이미 링크가 찬다"
    seconds = catalog / spare
    assert seconds < 1.0, (
        f"설정 화면 열기가 {seconds:.1f}초다. board_service 의 $CFG,LIST "
        f"타임아웃 안에 들어와야 한다."
    )


def test_the_old_baud_is_why_we_raised_it():
    """왜 올렸는지를 계산으로 남긴다.

    115200 으로 되돌리자는 이야기가 나올 때 이 숫자가 근거가 된다.
    """
    catalog, b = _catalog_bytes_and_budget(115200)
    assert b.ratio > 0.9, f"115200 에서 {b.ratio*100:.1f}% 를 쓴다"
    assert catalog / b.headroom_bytes_per_s > 10.0


def test_imports_no_qt():
    """🔴 이 층은 PyQt6 를 import 하지 않는다."""
    import inspect

    import host.gui.field_budget as mod

    src = inspect.getsource(mod)
    assert "PyQt" not in src
    assert "QtWidgets" not in src
