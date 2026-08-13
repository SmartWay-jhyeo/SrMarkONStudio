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


def test_set_rejects_non_ascii_string_value():
    """🔴 사용자가 넣는 값은 ASCII 만 통과한다.

    단위는 'degC'·'kPa' 처럼 쓴다. '℃' 를 허용하면 펌웨어의 바이트 기준
    고정폭 버퍼와 호스트의 문자 기준 길이가 어긋난다.
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
