"""I2C 센서 레코드 → 화면 상태. Qt 없이 돈다 (규격 §7.5)."""

from host.gui.screen import build_sensors, quantity_label, sensor_status_text


def rec(cid=10, quantity="temp", value=23.4, unit="°C", status=0, t=1000):
    out = {"schema_ver": 3, "seq": 1, "t": t, "type": "i2c",
           "connector_id": cid, "quantity": quantity, "unit": unit,
           "status": status}
    if value is not None:
        out["value"] = value
    return out


# ---- 정체 -----------------------------------------------------------------


def test_one_card_per_connector_and_quantity():
    """🔴 정체는 (커넥터, 양) 짝이다.

    온습도 센서 하나가 `temp` 와 `humidity` 를 함께 낸다. 커넥터만으로
    묶으면 둘이 같은 카드에 덮어써져 값이 번갈아 튀는 것처럼 보인다.
    """
    out = build_sensors(
        [rec(10, "temp", 23.4), rec(10, "humidity", 61.2)],
        reachable=True)
    assert [(s.connector, s.quantity) for s in out] == [
        ("J10", "temp"), ("J10", "humidity")]


def test_ports_keep_their_own_cards():
    out = build_sensors([rec(10, "temp"), rec(13, "temp")], reachable=True)
    assert [s.connector for s in out] == ["J10", "J13"]


def test_order_is_stable_across_updates():
    """🔴 순서가 흔들리면 카드가 자리를 바꾼다. 값을 읽는 중에 카드가
       움직이면 무엇을 보고 있었는지 잃는다."""
    first = build_sensors([rec(10, "temp"), rec(10, "humidity")],
                          reachable=True)
    second = build_sensors([rec(10, "humidity", 60.0)],
                           reachable=True, previous=first)
    assert [(s.connector, s.quantity) for s in second] == [
        ("J10", "temp"), ("J10", "humidity")]


# ---- 값 -------------------------------------------------------------------


def test_value_and_unit_come_from_the_record():
    out = build_sensors([rec(11, "lux", 812.0, "lx")], reachable=True)
    assert out[0].value == 812.0
    assert out[0].unit == "lx"


def test_a_quiet_port_keeps_its_last_value():
    """🔴 이번에 안 온 센서를 지우지 않는다 — 채널 장애 격리와 같은 이유다.
       하나가 조용할 때 화면이 통째로 비는 것처럼 보이면 안 된다."""
    first = build_sensors([rec(10, "temp", 23.4)], reachable=True)
    second = build_sensors([], reachable=True, previous=first)
    assert second[0].value == 23.4


def test_history_accumulates_per_card():
    out = build_sensors([rec(10, "temp", 20.0, t=1000)], reachable=True)
    out = build_sensors([rec(10, "temp", 21.0, t=1100)],
                        reachable=True, previous=out)
    assert out[0].trace[-2:] == (20.0, 21.0)
    assert out[0].trace_t[-2:] == (1000, 1100)


# ---- 오류 -----------------------------------------------------------------


def test_a_faulty_reading_has_no_value():
    """🔴 규격 §7.5 — status 가 0 이 아니면 값 자리가 비어 온다. 옛 값을
       끌어다 채우면 죽은 센서가 살아 있는 것처럼 보인다."""
    out = build_sensors([rec(10, "temp", 23.4)], reachable=True)
    out = build_sensors([rec(10, "temp", None, status=1)],
                        reachable=True, previous=out)
    assert out[0].value is None
    assert out[0].status == 1
    assert out[0].broken


def test_a_gap_is_recorded_in_the_trace():
    """🔴 이력에 `None` 을 남긴다. 직전 값을 복사해 채우면 선이 매끈해지고
       화면이 오지 않은 값을 온 것처럼 그린다."""
    out = build_sensors([rec(10, "temp", 23.4, t=1000)], reachable=True)
    out = build_sensors([rec(10, "temp", None, status=1, t=1100)],
                        reachable=True, previous=out)
    assert out[0].trace[-1] is None


def test_link_loss_clears_values_but_keeps_history():
    first = build_sensors([rec(10, "temp", 23.4)], reachable=True)
    second = build_sensors([], reachable=False, previous=first)
    assert second[0].value is None
    assert second[0].trace  # 이력은 남는다


# ---- 이름표 ---------------------------------------------------------------


def test_known_quantities_get_korean_labels():
    assert quantity_label("temp") == "온도"
    assert quantity_label("humidity") == "습도"
    assert quantity_label("lux") == "조도"


def test_unknown_quantity_shows_its_key():
    """🔴 모르는 양에 이름을 지어내지 않는다. 키를 그대로 보여 주면
       사용자가 펌웨어와 대조할 수 있다."""
    assert quantity_label("dust_pm25") == "dust_pm25"


# ---- 버릴 것 --------------------------------------------------------------


def test_records_without_a_quantity_are_dropped():
    """양이 없으면 무엇을 잰 값인지 알 수 없다. 지어내지 않고 버린다."""
    bad = {"schema_ver": 3, "type": "i2c", "connector_id": 10, "value": 1.0}
    assert build_sensors([bad], reachable=True) == ()


def test_other_record_types_are_ignored():
    ain = {"type": "ain", "connector_id": 3, "ma": 12.0}
    assert build_sensors([ain], reachable=True) == ()


def test_unit_falls_back_to_the_spec_table():
    """🔴 `unit` 은 마스크 기본값이 off 다. 그래도 화면은 단위를 알아야
       한다 — 규격 §7.5.1 이 양마다 정해 두었다."""
    from host.gui.screen import quantity_unit

    bare = {"type": "i2c", "connector_id": 10, "quantity": "humidity",
            "value": 55.0}
    out = build_sensors([bare], reachable=True)
    assert out[0].unit == "%RH"
    assert quantity_unit("temp") == "°C"


def test_unknown_quantity_has_no_unit():
    """아무 단위나 붙이면 화면이 틀린 물리량을 말한다 — 없는 편이 낫다."""
    from host.gui.screen import quantity_unit

    assert quantity_unit("dust_pm25") == ""


def test_unsupported_kind_is_not_the_same_as_broken():
    """🔴 `status=3` 은 고장이 아니다.

    센서가 죽은 것과 펌웨어에 그 종류의 드라이버가 없는 것은 사용자가 할
    일이 다르다 — 앞은 배선을 보고, 뒤는 기다리거나 종류를 바꾼다. 같은
    빨간 글씨로 보이면 배선을 뜯게 된다.
    """
    rec = {"type": "i2c", "connector_id": 11, "quantity": "temp",
           "value": None, "status": 3, "t": 1000}
    (s,) = build_sensors([rec], reachable=True)

    assert s.unsupported is True
    assert s.broken is False


def test_no_response_is_broken_but_not_unsupported():
    rec = {"type": "i2c", "connector_id": 10, "quantity": "lux",
           "value": None, "status": 1, "t": 1000}
    (s,) = build_sensors([rec], reachable=True)

    assert s.broken is True
    assert s.unsupported is False


# ---- 시드 (아날로그의 empty_channels() 와 같은 자리) -----------------------
#
# 🔴 아날로그는 J3~J9 일곱 장을 항상 깐다. I2C 도 같은 이유로 J10~J15
#    여섯 자리를 항상 깐다 — 안 꽂은 포트가 화면에서 사라지면 그것이
#    "미연결" 인지 "설정을 못 읽었다" 인지 구분할 수 없다(설계 원칙 3).


def test_six_ports_show_up_even_with_no_records_at_all():
    out = build_sensors([], reachable=True, ports={})
    assert {s.connector for s in out} == {
        "J10", "J11", "J12", "J13", "J14", "J15"}
    assert len(out) == 6


def test_a_port_left_out_of_the_dict_still_gets_a_seat():
    """카탈로그를 아직 못 읽었어도(설정 dict 가 비었어도) 여섯 자리는
    그대로 있어야 한다 — `ports={}` 도 여섯 장을 깐다."""
    out = build_sensors([], reachable=True, ports={10: (1, True)})
    assert len(out) == 6


def test_a_humidity_temp_port_seeds_two_cards():
    """🔴 종류 2(온습도) 는 카드 두 장이다 — 규격 §7.5.1, 사용자 확정
    2026-08-17."""
    out = build_sensors([], reachable=True, ports={10: (2, True)})
    j10 = {s.quantity for s in out if s.connector == "J10"}
    assert j10 == {"temp", "humidity"}
    assert len(out) == 7                 # 나머지 다섯 포트는 한 장씩


def test_seeded_card_carries_the_label_and_unit_from_the_spec_table():
    out = build_sensors([], reachable=True, ports={10: (1, True)})
    (lux,) = [s for s in out if s.connector == "J10"]
    assert lux.label == "조도"
    assert lux.unit == "lx"


def test_changing_kind_swaps_the_cards():
    """🔴 종류를 바꾸면 옛 카드가 남아 있으면 안 된다 — 보드가 다시는
    내지 않을 양이 화면에 계속 떠 있게 된다."""
    first = build_sensors([], reachable=True, ports={10: (2, True)})
    second = build_sensors([], reachable=True, ports={10: (1, True)},
                           previous=first)
    j10 = {s.quantity for s in second if s.connector == "J10"}
    assert j10 == {"lux"}


def test_kind_none_port_has_no_quantity_yet():
    (s,) = [s for s in build_sensors([], reachable=True, ports={})
            if s.connector == "J10"]
    assert s.quantity == ""
    assert s.no_kind is True


def test_disabled_port_is_marked_off():
    (s,) = [s for s in build_sensors([], reachable=True,
                                     ports={10: (1, False)})
            if s.connector == "J10"]
    assert s.enabled is False


def test_enabled_port_defaults_to_on():
    (s,) = [s for s in build_sensors([], reachable=True,
                                     ports={10: (1, True)})
            if s.connector == "J10"]
    assert s.enabled is True


def test_a_record_fills_its_card_without_touching_the_others():
    """🔴 채널 장애 격리 — 아날로그가 지키는 규칙과 같다."""
    out = build_sensors(
        [rec(10, "temp", 23.4)], reachable=True,
        ports={10: (2, True), 11: (1, True)})
    j10_temp = next(s for s in out if (s.connector, s.quantity) ==
                    ("J10", "temp"))
    j10_hum = next(s for s in out if (s.connector, s.quantity) ==
                   ("J10", "humidity"))
    j11_lux = next(s for s in out if s.connector == "J11")
    assert j10_temp.value == 23.4
    assert j10_hum.value is None
    assert j11_lux.value is None


def test_link_loss_clears_value_but_keeps_history_for_seeded_cards():
    first = build_sensors([rec(10, "lux", 812.0, "lx")], reachable=True,
                          ports={10: (1, True)})
    second = build_sensors([], reachable=False, previous=first,
                           ports={10: (1, True)})
    (s,) = [s for s in second if s.connector == "J10"]
    assert s.value is None
    assert s.trace  # 이력은 남는다


# ---- 카드 문구 (`sensor_status_text`) --------------------------------------
#
# 🔴 이미 쓰는 낱말을 재사용한다 — `host/gui/tare.py` 의 "채널 꺼짐" ·
#    "값 없음" 과 같다.


def test_kind_none_says_no_kind_chosen():
    (s,) = [s for s in build_sensors([], reachable=True, ports={})
            if s.connector == "J10"]
    assert sensor_status_text(s) == "종류 없음"


def test_disabled_says_off_even_before_the_no_value_check():
    (s,) = [s for s in build_sensors([], reachable=True,
                                     ports={10: (1, False)})
            if s.connector == "J10"]
    assert sensor_status_text(s) == "채널 꺼짐"


def test_enabled_with_no_value_yet_says_no_value():
    (s,) = [s for s in build_sensors([], reachable=True,
                                     ports={10: (1, True)})
            if s.connector == "J10"]
    assert sensor_status_text(s) == "값 없음"


def test_a_value_in_hand_has_no_status_text():
    out = build_sensors([rec(10, "lux", 812.0, "lx")], reachable=True,
                        ports={10: (1, True)})
    assert sensor_status_text(out[0]) == ""
