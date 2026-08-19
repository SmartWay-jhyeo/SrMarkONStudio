import pytest

from host.core.errors import ConfigError, Reason
from tools.simulator.config_store import default_store


def test_default_store_has_spec_items():
    """스펙 §6.1 의 항목이 전부 있어야 한다."""
    store = default_store()
    for key in ("dev.id", "tx.fields_ain", "tx.fields_i2c", "tx.fields_din",
                "tx.period_ms", "tx.float_digits",
                "adc.pga", "adc.drate", "pwr.24v", "pwr.14v9", "pwr.5v",
                "pwr.seq_delay_ms"):
        assert key in store.items, f"{key} 누락"
    for ch in range(7):
        for suffix in ("enabled", "period_ms", "zero", "scale", "unit"):
            assert f"ain{ch}.{suffix}" in store.items


def test_set_and_get_roundtrip():
    store = default_store()
    store.set("tx.period_ms", "250")
    assert store.get("tx.period_ms") == 250


def test_set_marks_dirty_until_save():
    store = default_store()
    assert store.dirty is False
    store.set("tx.period_ms", "250")
    assert store.dirty is True


def test_set_out_of_range_raises_range():
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("tx.period_ms", "1")
    assert exc.value.reason == Reason.RANGE


def test_set_unknown_key():
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("no.such.key", "1")
    assert exc.value.reason == Reason.UNKNOWN_KEY


def test_pwr_5v_is_settable_and_defaults_on():
    """5V 도 끌 수 있다 (사용자 확정 2026-08-14).

    🔴 막지 않는 대신 무엇이 함께 멈추는지 `note` 가 말한다. 화면이 그
       문구를 그대로 띄우므로(규격 §7.3), 그것이 인터록을 푼 대신 남은
       유일한 안전장치다.
    """
    store = default_store()
    item = store.items["pwr.5v"]
    assert store.get("pwr.5v") is True, "기본값은 켜짐"
    assert item.readonly is False and item.interlocked is False
    for word in ("팬", "수집", "WS2812"):
        assert word in item.note, f"사유에 '{word}' 가 없다: {item.note!r}"


def test_pwr_5v_off_is_accepted():
    store = default_store()
    store.set("pwr.5v", "false")
    assert store.get("pwr.5v") is False


def test_interlock_is_reported_before_readonly(interlocked_store):
    """🔴 규격 §5.2 — 인터록이 읽기 전용보다 **앞**이다.

    둘 다 걸린 항목에서 READONLY 만 돌려주면 `note` 에 담긴 하드웨어 사유가
    사라진다. 사용자는 "왜 안 되는지" 대신 "안 된다" 만 듣게 된다.

    🔴 이 시험은 이제 제품 설정표에 기대지 않는다. 예전에는 `pwr.5v` 가
       그 예시였는데, 5V 를 끌 수 있게 하면서 인터록 항목이 하나도 남지
       않았다 — 그때 이 계약을 확인하는 시험이 통째로 사라질 뻔했다.
       검증 순서는 규격이 정한 계약이므로, 쓰는 항목이 없어도 확인한다.
    """
    with pytest.raises(ConfigError) as exc:
        interlocked_store.set("test.locked", "false")
    assert exc.value.reason == Reason.INTERLOCK
    assert "시험용" in exc.value.detail


def test_setting_an_interlocked_item_to_its_current_value_is_allowed(
        interlocked_store):
    """이미 참인 값을 참으로 두는 것은 거부할 이유가 없다.

    호스트가 전체 설정을 한꺼번에 되쓸 때 불필요한 거부가 나지 않게 한다.
    """
    interlocked_store.set("test.locked", "true")
    assert interlocked_store.get("test.locked") is True


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "cfg.json"
    store = default_store(path)
    store.set("tx.period_ms", "250")
    store.set("ain3.unit", "bar")
    store.save()
    assert store.dirty is False

    reloaded = default_store(path)
    reloaded.load()
    assert reloaded.get("tx.period_ms") == 250
    assert reloaded.get("ain3.unit") == "bar"


def test_load_missing_file_keeps_defaults(tmp_path):
    store = default_store(tmp_path / "absent.json")
    store.load()
    assert store.get("tx.period_ms") == 100


def test_load_corrupt_file_falls_back_to_defaults(tmp_path):
    """Flash 손상 시 공장 기본값으로 복구한다 (스펙 §6.2)."""
    path = tmp_path / "cfg.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 100
    assert store.load_failed is True


def test_load_refuses_stored_interlock_bypass(tmp_path, interlocked_items):
    """🔴 저장 파일이 인터록 항목을 뒤집어도 복원하지 않는다.

    Flash 손상이나 손편집으로 인터록이 뚫리면, 안전상 거부하기로 한 값이
    부팅 때 그대로 살아난다. 저장 매체는 신뢰 대상이 아니다.
    """
    import json

    from tools.simulator.config_store import ConfigStore

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"test.locked": False}), encoding="utf-8")
    store = ConfigStore(interlocked_items(), path)
    store.load()
    assert store.get("test.locked") is True
    assert "test.locked" in store.rejected_keys


def test_load_rejects_out_of_range_stored_value(tmp_path):
    """저장값도 범위 검사를 받는다. 거부되면 기본값을 유지한다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"tx.period_ms": 999999}), encoding="utf-8")
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 100
    assert "tx.period_ms" in store.rejected_keys


def test_load_rejects_nan_stored_value(tmp_path):
    """NaN 은 범위 검사를 뚫는다(nan<min 도 nan>max 도 False). 별도로 막는다."""
    path = tmp_path / "cfg.json"
    path.write_text('{"ain0.scale": NaN}', encoding="utf-8")   # json 모듈은 NaN 을 읽는다
    store = default_store(path)
    store.load()
    assert store.get("ain0.scale") == 1.0
    assert "ain0.scale" in store.rejected_keys


def test_set_accepts_zero_padded_integer():
    """🔴 '0250' 은 유효한 십진수다.

    int(raw, 0) 을 쓰면 파이썬이 앞의 0 을 8진수 접두사로 읽어 ValueError 가
    난다. GUI 의 고정폭 입력이나 사람의 습관으로 0 이 채워지는 건 흔하다.
    """
    store = default_store()
    store.set("tx.period_ms", "0250")
    assert store.get("tx.period_ms") == 250


def test_set_rejects_command_injection_via_newline():
    """🔴 값에 줄바꿈을 넣어 명령을 주입하는 공격을 막는다.

    `dev.id = "x\\r\\n$HB*0A"` 를 허용하면 조립된 줄이
    `$CFG,SET,dev.id,x\\r\\n$HB*0A*72\\r\\n` 가 된다. 가운데 `$HB*0A` 는
    체크섬까지 유효한 완결된 명령이라, 보드가 이를 진짜 하트비트로 받아
    CONFIG 모드를 유지한다. 설정값 하나로 명령을 심을 수 있다.
    """
    store = default_store()
    for payload in ("x\r\n$HB*0A", "x\n$HB*0A", "x\ry"):
        with pytest.raises(ConfigError) as exc:
            store.set("dev.id", payload)
        assert exc.value.reason == Reason.RANGE, payload
    assert store.get("dev.id") == "1"


def test_set_rejects_protocol_delimiters_in_value():
    """🔴 구분자가 값에 들어가면 호스트와 펌웨어의 해석이 갈린다.

    ',' 는 인자를 더 쪼개고, '*' 는 체크섬 구분자로, '$' 는 명령 시작으로
    오인된다.
    """
    store = default_store()
    for payload in ("a,b", "a*b", "a$b"):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.unit", payload)
        assert exc.value.reason == Reason.RANGE, payload


def test_set_rejects_nul_byte():
    """🔴 NUL 은 C 펌웨어에서 문자열을 중간에 끊는다.

    호스트는 'a\\0b' 를 3자로 보고 통과시키지만 펌웨어는 'a' 로 읽는다.
    검증한 값과 저장된 값이 달라진다.
    """
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("ain0.unit", "a\x00b")
    assert exc.value.reason == Reason.RANGE


def test_set_rejects_all_control_characters():
    """isascii() 만으로는 부족하다 — 제어문자는 전부 ASCII 다."""
    store = default_store()
    for code in (0x00, 0x07, 0x09, 0x0A, 0x0D, 0x1B, 0x7F):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.unit", f"a{chr(code)}b")
        assert exc.value.reason == Reason.RANGE, hex(code)


def test_set_rejects_non_ascii_string_value():
    """단위는 'degC'·'kPa' 처럼 쓴다.

    '℃' 를 허용하면 펌웨어의 바이트 기준 고정폭 버퍼와 호스트의 문자 기준
    길이가 어긋난다.
    """
    store = default_store()
    for bad in ("℃", "바", "Ω", "µm"):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.unit", bad)
        assert exc.value.reason == Reason.RANGE, bad
    assert store.get("ain0.unit") == ""          # 아무것도 반영되지 않았다


def test_set_accepts_ascii_unit():
    store = default_store()
    for good in ("bar", "degC", "kPa", "LPM", "%"):
        store.set("ain0.unit", good)
        assert store.get("ain0.unit") == good


def test_str_length_limit_is_byte_safe_because_ascii_only():
    """ASCII 만 통과하므로 문자 수 == 바이트 수다. 펌웨어 버퍼와 일치한다."""
    store = default_store()
    store.set("ain0.unit", "1234567")            # 최대 7자
    with pytest.raises(ConfigError) as exc:
        store.set("ain0.unit", "12345678")
    assert exc.value.reason == Reason.RANGE


def test_load_rejects_non_ascii_stored_value(tmp_path):
    """저장 파일에 non-ASCII 가 들어 있어도 복원하지 않는다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"ain0.unit": "℃"}, ensure_ascii=False), encoding="utf-8"
    )
    store = default_store(path)
    store.load()
    assert store.get("ain0.unit") == ""
    assert "ain0.unit" in store.rejected_keys


def test_set_rejects_nan_and_inf():
    """$CFG,SET 경로도 동일하게 막힌다."""
    store = default_store()
    for bad in ("nan", "inf", "-inf", "Infinity"):
        with pytest.raises(ConfigError) as exc:
            store.set("ain0.scale", bad)
        assert exc.value.reason == Reason.RANGE, bad


def test_load_restores_normal_values_untouched(tmp_path):
    """거부 로직이 정상값 복원을 방해하지 않는다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"tx.period_ms": 250, "ain2.enabled": True, "ain2.unit": "bar"}),
        encoding="utf-8",
    )
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 250
    assert store.get("ain2.enabled") is True
    assert store.get("ain2.unit") == "bar"
    assert store.rejected_keys == []


def test_load_rejects_individually_valid_but_infeasible_combination(tmp_path):
    """🔴 항목별로는 다 유효한데 조합이 불가능한 설정 파일.

    7채널 10ms(요구 700 SPS) + drate 2 SPS(가용 1.6 SPS). 항목 검사만
    하면 한 개도 안 걸리고 그대로 부팅한다. 큐가 영구히 넘치는데 아무
    신호도 없다 — `load()` 의 전제("저장 매체는 신뢰 대상이 아니다")를
    항목 단위로만 지킨 셈이다.

    부팅을 막지는 않는다. 기본값으로 되돌리고 표시만 남긴다.
    """
    import json

    cfg = {"adc.drate": 2}
    for ch in range(7):
        cfg[f"ain{ch}.enabled"] = True
        cfg[f"ain{ch}.period_ms"] = 10

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    store = default_store(path)
    store.load()

    assert "<combination>" in store.rejected_keys
    assert store.load_failed is True
    assert store.get("adc.drate") == 60            # 기본값으로 복귀
    assert store.get("ain1.enabled") is False


def test_load_accepts_a_feasible_combination(tmp_path):
    """조합 검사가 정상 설정을 막지 않는다."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"adc.drate": 1000, "ain1.enabled": True,
                    "ain1.period_ms": 200}),
        encoding="utf-8",
    )
    store = default_store(path)
    store.load()

    assert store.rejected_keys == []
    assert store.load_failed is False
    assert store.get("ain1.enabled") is True
    assert store.get("adc.drate") == 1000


def test_load_ignores_unknown_keys(tmp_path):
    """스키마가 내려간 경우에도 아는 항목만 복원한다 (전방 호환)."""
    import json

    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps({"tx.period_ms": 250, "gone.away": 1}), encoding="utf-8"
    )
    store = default_store(path)
    store.load()
    assert store.get("tx.period_ms") == 250


def test_reset_restores_defaults():
    store = default_store()
    store.set("tx.period_ms", "250")
    store.reset()
    assert store.get("tx.period_ms") == 100


def test_field_mask_default_matches_spec():
    """기본 on 비트: raw(3), ma(4), value(5), status(7), connector_id(9).

    🔴 [판단, 2026-08-19] time_source(옛 비트 1)는 더 이상 여기 없다 —
    tx.fields 마스크로 끌 수 없도록 FIELD_BITS 밖으로 옮겼다(항상 실린다).
    비트 1 자리는 비워 둔다."""
    store = default_store()
    assert store.field_mask("ain") == 0b1010111000


def test_field_mask_is_split_per_record_kind():
    """🔴 [신규, 2026-08-19] `tx.fields` 하나였던 것을 셋으로 나눴다 —
    `i2c`·`din` 은 `ain` 과 다른 비트 집합·기본값을 가진다(규격 §7.5·§7.6).

    `i2c` 는 status(7)·connector_id(9) 만 켤 수 있다 — quantity·value 는
    끌 수 없는 필드라 애초에 비트가 없다. `din` 은 기본으로 아무 비트도
    켜지지 않는다 — device_id·time_quality 뿐이고 둘 다 기본 off 다."""
    store = default_store()
    assert store.field_mask("i2c") == 0b1010000000     # status + connector_id
    assert store.field_mask("din") == 0

    ain_item = store.items["tx.fields_ain"]
    i2c_item = store.items["tx.fields_i2c"]
    din_item = store.items["tx.fields_din"]
    assert ain_item.maximum != i2c_item.maximum, "ain 은 raw 등 i2c 에 없는 비트도 켤 수 있다"
    # i2c 는 device_id·time_quality·status·connector_id 넷을 켤 수 있다
    # (기본은 그중 status·connector_id 만 켜짐 — 위 field_mask("i2c") 검사).
    assert i2c_item.maximum == 0b1010000101
    assert din_item.maximum == 0b0000000101             # device_id + time_quality 만


def test_ain_mask_change_does_not_affect_i2c_or_din():
    """🔴 [신규, 2026-08-19] 셋이 나뉜 핵심 계약 — 한쪽을 바꿔도 나머지는
    그대로다. 예전에는 tx.fields 하나였으므로 이 시험이 성립할 수 없었다."""
    store = default_store()
    before_i2c = store.field_mask("i2c")
    before_din = store.field_mask("din")
    store.set("tx.fields_ain", "0")
    assert store.field_mask("i2c") == before_i2c
    assert store.field_mask("din") == before_din


def test_catalog_lines_end_with_cfg_end_matching_count():
    from host.core.config_schema import parse_catalog

    store = default_store()
    lines = list(store.catalog_lines())
    schema = parse_catalog(lines)          # count 불일치면 여기서 예외
    assert len(schema.items) == len(store.items)


# ---- J18~J20 은 출력이 아니라 입력이다 (사용자 확정 2026-08-18) --------------
#
# 🔴 넷리스트 확인 결과 J18~J20 에는 옵토커플러가 붙고 보드는 그 신호를
#    **읽는다**. "켜라/꺼라" 가 성립하지 않으므로 sol.j18~sol.j20 은
#    카탈로그에서 아예 없앤다 — 대신 규격 §7.6 의 `din` 텔레메트리로 온다
#    (host/tests/test_dins.py, tools/simulator/device_sim.py).


def test_sol_j18_j19_j20_are_gone_from_the_catalog():
    """켤 수 없는 것을 카탈로그에 남겨 두면 화면에 안 먹는 토글이 뜬다."""
    store = default_store()
    for key in ("sol.j18", "sol.j19", "sol.j20"):
        assert key not in store.items, f"{key} 가 아직 카탈로그에 있다 — 입력이다"


def test_sol_debounce_ms_replaces_the_removed_outputs():
    """디바운스는 **펌웨어가** 쓰는 값이지만, 사용자가 현장에서 옵토 접점의
    떨림 정도를 보고 조정할 수 있어야 하므로 카탈로그 항목이다."""
    store = default_store()
    item = store.items["sol.debounce_ms"]
    assert item.vtype == "u16"
    assert item.default == 5
    assert item.minimum == 0
    assert item.maximum == 1000
    assert item.unit == "ms"
    assert item.out is False           # 출력이 아니다 — TEST 이탈에 안 걸린다


def test_catalog_item_count_is_one_hundred_and_one():
    """94 + 2 (`tx.fields` 하나가 `tx.fields_ain`·`tx.fields_i2c`·
    `tx.fields_din` 셋으로 나뉘며 순증가, 2026-08-19 개정 §7.2·§7.5·§7.6)
    + 1 (`lcd.enabled`, LCD 1차 작업 2026-08-19)
    + 1 (`lcd.period_ms`, LCD 2차 작업 2026-08-19)
    + 3 (`lcd.spi_khz`·`lcd.verify_ms`·`lcd.redraw_ms`, LCD 회복 작업
         2026-08-19 — 실기기에서 "노이즈 타면 픽셀이 다 깨진다" 가 나왔고
         원인이 아직 무작위라, 클럭·되읽기 주기·전면 갱신 주기를 사용자가
         현장에서 돌려 볼 수 있어야 했다) = 101.

    이 수가 흔들리면 십중팔구 항목을 늘리거나 줄인 것이다 — 실수인지
    의도인지 이 시험이 먼저 묻는다.
    """
    store = default_store()
    assert len(store.items) == 101


# ---- LCD 회복 (2026-08-19, 실기기 증상) --------------------------------------
#
# 🔴 사용자: "LCD는 가끔 리셋을 해줘야겠다. 노이즈 타면 픽셀이 다 깨지는데?"
#    그리고 "무작위로 깨지는 느낌이야" — 24V 스위칭·케이블 접촉과 무관하다.
#    원인을 아직 모르므로 클럭·되읽기 주기·전면 갱신 주기를 전부 설정 항목으로
#    뺐다. 사용자가 현장에서 돌려 보고 증상이 사라지는 조합을 찾는 것이
#    지금으로서는 유일한 진단 수단이다.


def test_lcd_spi_clock_is_selectable_and_defaults_to_eight_megahertz():
    """🔴 기본이 8 MHz 다 (사용자 결정 2026-08-19: "8mhz로 낮춰서 해보자").

    남은 유력 후보가 "핀 헤더 + 점퍼선에 16 MHz 가 빠른 것" 이라, 낮춰서
    증상이 사라지면 그것 자체가 신호 무결성 문제라는 진단이 된다.

    고를 수 있는 값은 분주비로 실제로 낼 수 있는 것뿐이다 — SPI2 커널
    클럭 64 MHz 를 2 의 거듭제곱으로 나눈 값이고, 32 MHz(64/2)는 ILI9488 의
    쓰기 상한 20 MHz 를 넘어 목록에 없다.
    """
    store = default_store()
    item = store.items["lcd.spi_khz"]
    assert item.vtype == "enum"
    assert item.default == 8000
    assert item.choices == (2000, 4000, 8000, 16000)
    assert item.unit == "kHz"


def test_lcd_recovery_periods_can_be_turned_off():
    """🔴 회복 장치를 **끌 수 있어야** 한다.

    끌 수 없는 회복은 그것 자체가 새로운 방해 요인이다 — 되읽기가 못 미더운
    판에서는 되읽기를, 전면 갱신이 눈에 거슬리는 현장에서는 전면 갱신을
    사용자가 멈출 수 있어야 한다. 그래서 둘 다 하한이 0(= 안 함)이다.
    """
    store = default_store()

    verify = store.items["lcd.verify_ms"]
    assert verify.vtype == "u16"
    assert verify.minimum == 0
    assert verify.default == 5000

    redraw = store.items["lcd.redraw_ms"]
    # u32 다 — u16 은 65.5초에서 끝나는데, 증상이 드물면 분 단위로 늘려야 한다.
    assert redraw.vtype == "u32"
    assert redraw.minimum == 0
    assert redraw.maximum == 3600000
    assert redraw.default == 60000
