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
    """🔴 [개정 2026-08-31, HANDOFF_0831 결정 2] 머리가 Cloud 계약이다 —
    공통 필드(schema_ver·seq·device_id·t·type·time_source) + ain 의
    값 필드(이름 = 단위 문자열 표본 "L/min"). connector_id 는 전선에서
    사라졌다 — 채널 대응은 카탈로그 역매핑(typemap)의 몫이다."""
    rec = sample_record([])
    assert set(rec) == {"schema_ver", "seq", "device_id", "t", "type",
                        "time_source", "L/min"}
    assert rec["schema_ver"] == 1
    assert rec["seq"] == 4294967295, "tx.seq 기본 켜짐 — 최장 자릿수로 잰다"


def test_sample_carries_exactly_what_was_selected():
    rec = sample_record(["ma"])
    assert "ma" in rec
    assert "raw" not in rec
    assert "unit" not in rec, "계약 전선에 unit 필드는 없다 — 이름이 단위다"


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


def test_sample_record_locks_i2c_value_field():
    """🔴 [개정 2026-08-31] 계약 i2c 레코드의 잠긴 것은 값 필드(degc 류)
    하나다 — quantity·connector_id 는 전선에서 사라졌다(역매핑의 몫)."""
    rec = sample_record([], record_type="i2c")
    assert rec["type"] == "temp_road", "종류 유도 타입 중 최장 표본"
    assert "degc" in rec
    assert "quantity" not in rec and "connector_id" not in rec


def test_sample_record_locks_din_state():
    """계약 din 상태 레코드 — state 만 잠긴다. 타입은 사용자 문자열 상한
    (15바이트) 표본이다."""
    rec = sample_record([], record_type="din")
    assert len(rec["type"]) == 15
    assert "state" in rec and "connector_id" not in rec


def test_sample_record_defaults_to_ain_and_has_no_extra_locked_fields():
    """옛 호출부(record_type 생략)는 ain 표본이다 — 타입은 사용자 문자열
    상한(15바이트), 값 필드는 단위 이름(최장 7바이트) 하나."""
    rec = sample_record([])
    assert len(rec["type"]) == 15
    assert "degc" not in rec and "state" not in rec
    assert "L/min" in rec


def test_sample_is_not_optimistic_about_width():
    """🔴 넉넉한 값으로 잰다. 좁은 표본으로 재면 실제보다 작게 나오고,
       작게 나온 만큼이 정확히 여유가 없을 때 문제가 된다."""
    rec = sample_record(["raw"])
    assert rec["raw"] >= 8_000_000        # 24비트 ADC 의 큰 쪽
    assert rec["seq"] == 4294967295       # uint32 의 끝 — 열 자리


def test_sample_matches_captured_lines_closely():
    """실캡쳐(2026-08-29)와 크게 어긋나면 표본이 거짓말하는 것이다 —
    넉넉하게(같거나 크게), 그러나 두 배씩 부풀리지는 않게."""
    import json

    from host.tests import cloud_vectors as V

    sample = measure_line(sample_record(["ma", "raw"]))
    real = len(V.FLOW1.encode("utf-8")) + 2   # 캡쳐에는 seq 가 없다
    assert real <= sample <= real * 1.5, (sample, real)

    rec = json.loads(V.GNSS)
    sample = measure_line(sample_record(["alt"], record_type="gnss"))
    real = len(V.GNSS.encode("utf-8")) + 2
    assert real * 0.8 <= sample <= real * 1.5, (sample, real)


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


# ------------------------------------------------------- 카탈로그를 물린 계산
#
# 🔴 [정리, 2026-08-20] 시뮬레이터를 지우면서 **제품 카탈로그의 실제 숫자를
#    주장하던 시험 셋을 걷어냈다** — `test_default_telemetry_fits_115200`,
#    `test_settings_screen_opens_quickly_at_the_default_baud`,
#    `test_the_old_baud_is_why_we_raised_it`.
#
#    셋 다 "기본 설정이 115200 에 들어간다" 류의 **보드에 관한 사실**을 말했다.
#    입력이 얼린 스냅샷(`catalog_snapshot.jsonl`)으로 바뀐 지금 그 주장은
#    거짓말이 된다 — 펌웨어가 기본값을 바꿔도 계속 통과한다. 없는 안전망보다
#    있다고 믿는 안전망이 나쁘다. 그 숫자는 실기기 측정
#    (`docs/measurements/2026-08-14_baud_921600.md`)이 근거다.
#
#    아래 하나는 남는다 — 주장하는 것이 보드가 아니라 **계산이 도는가** 다.


def test_all_fields_on_is_measurable():
    """전부 켰을 때가 얼마인지 실제로 잴 수 있어야 한다.

    사용자가 고르기 전에 결과를 보는 것이 이 화면의 요점이다.

    🔴 "전부" 를 `1 << len(FIELD_BITS)` 로 만들면 안 된다 — 비트 **번호**와
    비트 **개수**는 다르고(1번은 비어 있다, 규격 §7.2), 게다가 마스크마다
    자기 종류의 비트만 켤 수 있다. 카탈로그가 알려 주는 상한을 그대로 쓴다
    — 비트를 늘려도 이 시험이 따라온다.
    """
    from host.tests.fake_board import build_ain_record, fake_store

    store = fake_store()
    store.set("tx.fields_ain", str(int(store.items["tx.fields_ain"].maximum)))
    rec = build_ain_record(store, channel=0, seq=1, t_ms=1772200855875,
                           raw=8388608, capture_counter=123456789)
    b = compute_budget(rec, channels_enabled=7, period_ms=100, baud=115200)
    assert b.line_bytes > 0
    assert b.bytes_per_s > 0


# 🔴 `test_imports_no_qt` 는 여기서 걷어냈다. 파일마다 손으로 복사한
#    문자열 검사였고, 그러다 보니 정작 `screen.py`·`theme.py` 에는
#    없었다. 지금은 `test_layer_boundaries.py` 가 층 전체를 AST 로 훑는다.


# ------------------------------------------------------- 한 줄 상한 (보드가 버림)

def test_all_ain_fields_overflow_the_record_limit_and_the_message_says_dropped():
    """전부 켜면 한 줄이 보드 상한을 넘고, 화면이 "통째로 버려진다"고
    말해야 한다.

    🔴 실기기에서 겪었다 (2026-08-21). ain 필드를 전부 켜자 ain 레코드가
       **조용히 전부 사라졌다** — 펌웨어의 모든 송신부(mk_telem.c)가 상한을
       넘는 줄을 반쪽 JSON 방지를 위해 통째로 버리는데, 화면은 대역폭만
       경고하고 줄 상한은 말하지 않았다. 젯슨과 GUI 양쪽에서 아날로그가
       멈춘 것으로 나타났고 원인을 GDB 로 링을 덤프해서야 찾았다.
    """
    from host.tests.fake_board import build_ain_record, fake_store

    store = fake_store()
    store.set("tx.fields_ain", str(int(store.items["tx.fields_ain"].maximum)))
    rec = build_ain_record(store, channel=0, seq=1, t_ms=1772200855875,
                           raw=8388608, capture_counter=123456789)
    b = compute_budget(rec, channels_enabled=7, period_ms=100, baud=921600)
    assert b.record_dropped
    level, msg = budget_message(b)
    assert level == "fault"
    assert "버려진다" in msg


def test_a_modest_selection_is_not_reported_dropped():
    """평범한 조합은 상한 안이고, 버림 경고가 나오면 안 된다 — 늑대야
    소리가 잦으면 진짜 늑대를 놓친다."""
    rec = _rec(connector_id=3, ma=19.9999, value=1234.5678, unit="L/min",
               status=0, time_source="device_clock")
    b = compute_budget(rec, channels_enabled=7, period_ms=100, baud=921600)
    assert not b.record_dropped
    level, msg = budget_message(b)
    assert level == "ok"
    assert "버려진다" not in msg


def test_record_limit_matches_the_firmware_drop_rule():
    """상한 값이 펌웨어의 버림 조건에서 파생된 그대로인지.

    mk_telem.c 의 모든 송신부가 `char body[MK_LINE_MAX + 8]` 에 짓고
    `len + 2u > sizeof body` 면 버린다 — 즉 JSON 은 MK_LINE_MAX + 6 까지만
    전선에 나간다. 펌웨어 쪽 패턴이 바뀌면 이 시험이 파생을 다시 보라고
    말한다.
    """
    import re
    from pathlib import Path

    from host.core.limits import MAX_PAYLOAD_BYTES, TELEM_RECORD_JSON_MAX

    assert TELEM_RECORD_JSON_MAX == MAX_PAYLOAD_BYTES + 6

    src = (Path(__file__).resolve().parents[2]
           / "firmware" / "stage1" / "app" / "mk_telem.c"
           ).read_text(encoding="utf-8")
    bodies = re.findall(r"char body\[MK_LINE_MAX \+ (\d+)\]", src)
    assert bodies and all(n == "8" for n in bodies), (
        "mk_telem.c 의 body 크기가 바뀌었다 — TELEM_RECORD_JSON_MAX 파생을 "
        "다시 확인할 것")
    assert "len + 2u > sizeof body" in src, (
        "mk_telem.c 의 버림 조건이 바뀌었다 — TELEM_RECORD_JSON_MAX 파생을 "
        "다시 확인할 것")
