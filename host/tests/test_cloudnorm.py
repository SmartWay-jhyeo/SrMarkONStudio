"""정규화 어댑터 — 클라우드 레코드를 내부 v3형으로 (계획 2 Task 3).

화면·저장·조회 전부가 (type, connector_id) 로 키잉돼 있다(2026-08-31
전수 조사). 어댑터가 경계에서 한 번 변환하면 그 전부가 무변경으로 산다.
"""

import json

from host.core.cloudnorm import normalize
from host.core.typemap import TypeMap
from host.tests import cloud_vectors as V
from host.tests.test_typemap import _field_wiring


def _tmap() -> TypeMap:
    return TypeMap.from_schema(_field_wiring())


def _one(line: str) -> dict:
    out = normalize(json.loads(line), _tmap())
    assert len(out) == 1, out
    return out[0]


def test_ain_vector_becomes_internal_ain():
    rec = _one(V.FLOW1)
    assert rec["type"] == "ain"
    assert rec["connector_id"] == 3          # ain0 = J3
    assert rec["value"] == 1.6               # lpm 키에서
    assert rec["unit"] == "lpm"
    assert rec["ma"] == 4.260
    assert rec["raw"] == 857683
    assert rec["t"] == 968563
    assert rec["cloud_type"] == "flow1"      # 원문 타입은 표시용으로 보존


def test_i2c_vectors_become_internal_i2c():
    rec = _one(V.TEMP_AIR)
    assert (rec["type"], rec["connector_id"]) == ("i2c", 12)
    assert (rec["quantity"], rec["value"]) == ("temp", 27.0)
    rec = _one(V.HUMIDITY)
    assert (rec["quantity"], rec["value"]) == ("humidity", 34.0)


def test_temp_road_ambient_synthesizes_second_sensor():
    """v3 에서 주변 온도는 제 레코드였다 — 계약에서는 temp_road 의 선택
    필드(ambient_degc)다. 화면 카드가 계속 살도록 둘로 편다."""
    line = ('{"schema_ver":1,"device_id":"1","t":100,"type":"temp_road",'
            '"time_source":"device_clock","degc":42.5,"ambient_degc":24.0}')
    schema_extra = _field_wiring()
    # J13 을 적외 온도로 켠 스키마
    from host.core.config_schema import ConfigItem
    schema_extra.items["i2c13.enabled"] = ConfigItem(
        key="i2c13.enabled", group="x", vtype="str", default="", current=True)
    schema_extra.items["i2c13.kind"] = ConfigItem(
        key="i2c13.kind", group="x", vtype="str", default="", current=3)
    out = normalize(json.loads(line), TypeMap.from_schema(schema_extra))
    assert [(r["quantity"], r["value"]) for r in out] == [
        ("temp_object", 42.5), ("temp_ambient", 24.0)]
    assert all(r["connector_id"] == 13 for r in out)


def test_din_vector_becomes_internal_din():
    rec = _one(V.VALVE)
    assert (rec["type"], rec["connector_id"], rec["state"]) == ("din", 20, 1)


def test_gnss_vector_becomes_internal_gnss():
    rec = _one(V.GNSS)
    assert rec["type"] == "gnss"
    assert rec["lat"] == 37.40668094
    assert rec["lon"] == 126.72278794
    assert rec["fix_t"] == 1787737031800     # 계약의 t 가 곧 fix 시각
    assert rec["sats"] == 21
    assert rec["fix"] == 1
    assert rec["hdop"] == 1.2
    assert rec["alt"] == 32.238


def test_imu_and_unknown_pass_through():
    rec = _one(V.IMU)
    assert rec["type"] == "imu" and rec["ax"] == 0.034

    unknown = ('{"schema_ver":1,"device_id":"1","t":5,"type":"mystery",'
               '"time_source":"device_clock","x":1}')
    (rec,) = normalize(json.loads(unknown), _tmap())
    assert rec["type"] == "mystery", "미해석은 버리지 않고 그대로 통과"


def test_v3_records_pass_through_unchanged():
    """전환기 — 굽기 전 실보드는 v3 를 말한다. 어댑터는 이미 내부형인
    레코드를 건드리지 않는다."""
    v3 = {"schema_ver": 3, "seq": 1, "t": 0, "type": "ain",
          "connector_id": 3, "ma": 12.0, "value": 8.0, "status": 0}
    assert normalize(dict(v3), _tmap()) == [v3]
    ctl = {"schema_ver": 3, "seq": 0, "t": 0, "type": "cfg_item"}
    assert normalize(dict(ctl), _tmap()) == [ctl]


def test_duplicate_type_is_marked_ambiguous():
    from host.core.config_schema import ConfigItem, ConfigSchema

    items = {
        k: ConfigItem(key=k, group="x", vtype="str", default="", current=v)
        for k, v in {"ain0.cloud": "flow1", "ain0.unit": "lpm",
                     "ain3.cloud": "flow1", "ain3.unit": "lpm"}.items()
    }
    tmap = TypeMap.from_schema(ConfigSchema(items=items))
    (rec,) = normalize(json.loads(V.FLOW1), tmap)
    assert rec["connector_id"] == 3, "첫 타깃으로 정규화"
    assert rec.get("ambiguous") is True, "가릴 수 없음을 표시"
