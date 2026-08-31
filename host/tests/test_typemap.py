"""TypeMap — 카탈로그 역매핑 (HANDOFF_0831 결정 2 보완).

레코드에는 채널 번호가 없다(ch 필드 대신 카탈로그 역매핑 — 사용자 결정
2026-08-30). GUI 가 이미 들고 있는 카탈로그에서 "타입 문자열 → 어느
채널인가"를 만든다. 중복(같은 문자열 두 채널, 동종 I2C 2대)은 가릴 수
없음을 감지해 경고의 근거가 된다.
"""

from host.core.config_schema import ConfigItem, ConfigSchema
from host.core.typemap import TypeMap


def _schema(values: dict) -> ConfigSchema:
    items = {
        key: ConfigItem(key=key, group="x", vtype="str", default="", current=v)
        for key, v in values.items()
    }
    return ConfigSchema(items=items)


def _field_wiring() -> ConfigSchema:
    """2026-08-26 최종 시험 배선 + 2026-08-31 실보드 확정(온습도 J12)."""
    return _schema({
        "ain0.cloud": "flow1", "ain0.unit": "lpm",
        "ain1.cloud": "flow2", "ain1.unit": "lpm",
        "ain2.cloud": "pressure_paint", "ain2.unit": "bar",
        "ain3.cloud": "",                       # 빈 값 = 미발행 = 미등록
        "i2c12.enabled": True, "i2c12.kind": 2,  # AM2320 온습도
        "i2c13.enabled": False, "i2c13.kind": 0,
        "din20.cloud": "valve",
        "din18.cloud": "",
    })


def test_resolves_field_wiring():
    tmap = TypeMap.from_schema(_field_wiring())

    (t,) = tmap.resolve("flow1")
    assert (t.kind, t.connector, t.channel, t.unit) == ("ain", 3, 0, "lpm")
    (t,) = tmap.resolve("pressure_paint")
    assert (t.kind, t.connector, t.unit) == ("ain", 5, "bar")

    (t,) = tmap.resolve("temp_air")
    assert (t.kind, t.connector, t.quantity) == ("i2c", 12, "temp")
    (t,) = tmap.resolve("humidity")
    assert (t.kind, t.connector, t.quantity) == ("i2c", 12, "humidity")

    (t,) = tmap.resolve("valve")
    assert (t.kind, t.connector) == ("din", 20)

    assert tmap.resolve("nope") == ()
    assert tmap.resolve("") == ()
    assert tmap.duplicates() == {}


def test_disabled_and_empty_ports_are_not_mapped():
    tmap = TypeMap.from_schema(_field_wiring())
    # i2c13 은 꺼져 있다 — temp_air 가 12 하나로만 풀려야 한다
    assert [t.connector for t in tmap.resolve("temp_air")] == [12]
    # din18 은 빈 문자열 — valve 는 20 하나
    assert [t.connector for t in tmap.resolve("valve")] == [20]


def test_duplicate_user_strings_are_detected():
    schema = _schema({
        "ain0.cloud": "flow1", "ain0.unit": "lpm",
        "ain3.cloud": "flow1", "ain3.unit": "lpm",   # 실수로 같은 이름
    })
    tmap = TypeMap.from_schema(schema)
    assert [t.connector for t in tmap.resolve("flow1")] == [3, 6]
    assert tmap.duplicates() == {"flow1": [3, 6]}


def test_same_kind_i2c_twice_is_detected():
    """동종 센서 2대는 이름 자체가 없어 가릴 수 없다 — 감지까지가 한계
    (HANDOFF_0831 결정 2 보완의 '남는 한계 하나')."""
    schema = _schema({
        "i2c12.enabled": True, "i2c12.kind": 2,
        "i2c13.enabled": True, "i2c13.kind": 2,
    })
    tmap = TypeMap.from_schema(schema)
    assert [t.connector for t in tmap.resolve("temp_air")] == [12, 13]
    assert tmap.duplicates()["temp_air"] == [12, 13]
    assert tmap.duplicates()["humidity"] == [12, 13]


def test_duplicate_warning_names_types_and_connectors():
    """경고문이 무엇이 어디에 겹쳤는지 말해야 사람이 고칠 수 있다 —
    2026-08-31 온습도 진단에서 이 경고가 없어 주소 중복을 늦게 찾았다."""
    from host.gui.settings_form import cloud_duplicate_warning

    assert cloud_duplicate_warning({}) == ""
    msg = cloud_duplicate_warning({"temp_air": [12, 13], "flow1": [3, 6]})
    assert "temp_air" in msg and "J12" in msg and "J13" in msg
    assert "flow1" in msg and "J3" in msg and "J6" in msg
    assert "가릴 수 없다" in msg


def test_kind_table_matches_screen_quantities():
    """🔴 이중 정의 감시 — typemap 의 종류→타입 표는 펌웨어 mk_cloud.c
    I2C_KIND_TYPES 의 복제다. 화면의 종류→물리량 표(screen.py)와 어긋나면
    센서 카드가 값을 못 찾는다."""
    from host.core.typemap import KIND_CLOUD
    from host.gui.screen import I2C_KIND_QUANTITIES

    for kind, entries in KIND_CLOUD.items():
        quantities = [q for _, q in entries]
        for q in quantities:
            assert q in I2C_KIND_QUANTITIES.get(kind, ()), (
                f"kind {kind}: {q} 가 screen.I2C_KIND_QUANTITIES 에 없다")
