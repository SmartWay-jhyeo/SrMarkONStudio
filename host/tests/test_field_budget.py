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


def test_sample_record_locks_i2c_quantity_and_value():
    """🔴 [신규, 2026-08-19] i2c 는 quantity·value 를 마스크로 못 끈다
    (규격 §7.5) — 무엇을 골랐든 표본에 항상 실려야 실제 크기를 잰다."""
    rec = sample_record([], record_type="i2c")
    assert rec["type"] == "i2c"
    assert "quantity" in rec and "value" in rec


def test_sample_record_locks_din_connector_id_and_state():
    """🔴 [신규, 2026-08-19] din 은 connector_id·state 를 마스크로 못 끈다
    (규격 §7.6)."""
    rec = sample_record([], record_type="din")
    assert rec["type"] == "din"
    assert "connector_id" in rec and "state" in rec


def test_sample_record_defaults_to_ain_and_has_no_extra_locked_fields():
    """옛 호출부(record_type 생략)는 예전과 똑같이 ain 만 만든다."""
    rec = sample_record([])
    assert rec["type"] == "ain"
    assert "quantity" not in rec and "connector_id" not in rec


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
