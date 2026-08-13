import pytest

from host.core.errors import ConfigError, Reason
from tools.simulator.config_store import default_store


def test_default_store_has_spec_items():
    """스펙 §6.1 의 항목이 전부 있어야 한다."""
    store = default_store()
    for key in ("dev.id", "tx.fields", "tx.period_ms", "tx.float_digits",
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


def test_pwr_5v_is_readonly_and_true():
    """쿨링 팬이 5V 레일 직결이라 끌 수 없다 (데이터시트 §5.14)."""
    store = default_store()
    assert store.get("pwr.5v") is True
    assert store.items["pwr.5v"].readonly is True


def test_pwr_5v_off_raises_interlock_not_readonly():
    """읽기 전용이지만 사용자에게는 '안전상 거부'로 알려야 한다."""
    store = default_store()
    with pytest.raises(ConfigError) as exc:
        store.set("pwr.5v", "false")
    assert exc.value.reason == Reason.INTERLOCK
    assert "팬" in exc.value.detail


def test_pwr_5v_set_true_is_accepted_as_noop():
    """이미 참인 값을 참으로 두는 것은 거부할 이유가 없다."""
    store = default_store()
    store.set("pwr.5v", "true")
    assert store.get("pwr.5v") is True


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


def test_load_refuses_stored_interlock_bypass(tmp_path):
    """🔴 저장 파일이 pwr.5v=false 를 담고 있어도 복원하지 않는다.

    Flash 손상이나 손편집으로 인터록이 뚫리면 쿨링 팬이 꺼진 채 부팅한다.
    저장 매체는 신뢰 대상이 아니다.
    """
    import json

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"pwr.5v": False}), encoding="utf-8")
    store = default_store(path)
    store.load()
    assert store.get("pwr.5v") is True
    assert "pwr.5v" in store.rejected_keys


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
    """기본 on 비트: time_source(1), raw(3), ma(4), value(5), status(7), connector_id(9)"""
    store = default_store()
    assert store.field_mask == 0b1010111010


def test_catalog_lines_end_with_cfg_end_matching_count():
    from host.core.config_schema import parse_catalog

    store = default_store()
    lines = list(store.catalog_lines())
    schema = parse_catalog(lines)          # count 불일치면 여기서 예외
    assert len(schema.items) == len(store.items)
