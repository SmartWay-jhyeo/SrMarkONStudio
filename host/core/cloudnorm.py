"""클라우드 레코드 → 내부 v3형 정규화 (HANDOFF_0831 결정 2, 계획 2).

🔴 왜 어댑터인가 — 화면(screen)·스트림·저장(store)·조회(query)·대시보드
   전부가 (type, connector_id) 로 레코드를 채널에 붙인다(2026-08-31 전수
   조사). 그 수십 곳을 계약 어휘로 다시 쓰는 대신, 경계(board_service
   수신부) 한 곳에서 계약 레코드를 내부형으로 변환한다. 원문 타입은
   `cloud_type` 으로 보존해 표시가 잃는 것이 없다.

🔴 전환기 호환 — 굽기 전 실보드는 v3 를 말한다. 이미 내부형인 레코드
   (connector_id 가 있거나 제어 타입)는 그대로 통과하므로, GUI 는 옛
   펌웨어와 새 펌웨어를 동시에 읽는다.

버리지 않는다 — 해석 못 하는 사용자 문자열도 그대로 통과시킨다. 화면
카드에 못 붙을 뿐, 스트림 원문·저장(raw)은 산다(설계 원칙 3 의 결).
"""

from host.core.typemap import TypeMap

#: 이미 내부형인 타입 — v3 텔레메트리와 제어 응답. 건드리지 않는다.
_INTERNAL_TYPES = frozenset({
    "ain", "i2c", "din", "gnss_raw",
    "id", "stat", "cfg_value", "cfg_item", "cfg_field", "cfg_end",
    "corrupt", "link_down", "link_up",
})

#: 계약 i2c 타입의 값 필드 이름 — mk_cloud.c 의 I2C_CLOUD 표와 나란하다.
_I2C_VALUE_FIELD = {"temp_air": "degc", "humidity": "pct",
                    "temp_road": "degc", "light": "lux"}

#: 공통 필드 — 정규화된 레코드에 그대로 옮긴다.
_CARRY = ("schema_ver", "seq", "t", "time_source", "device_id")


def _base(rec: dict, kind: str, connector: int) -> dict:
    out = {k: rec[k] for k in _CARRY if k in rec}
    out["type"] = kind
    out["connector_id"] = connector
    out["cloud_type"] = rec.get("type")
    return out


def normalize(rec: dict, tmap: TypeMap) -> list[dict]:
    """계약 레코드 하나를 내부형 0~n 개로. 해석 불가·이미 내부형은 그대로."""
    rtype = rec.get("type")
    if rtype in _INTERNAL_TYPES or "connector_id" in rec:
        return [rec]

    # ── 고정 타입 (TypeMap 밖) ──────────────────────────────────────────
    if rtype == "gnss" and "lat_e8" in rec:
        out = {k: rec[k] for k in _CARRY if k in rec}
        out["type"] = "gnss"
        out["lat"] = rec["lat_e8"] / 1e8
        out["lon"] = rec["lon_e8"] / 1e8
        # 계약의 t 는 fix 의 측정 시각(RMC 유래 UTC) 그 자체다.
        out["fix_t"] = rec.get("t")
        if "sat" in rec:
            out["sats"] = rec["sat"]
        if "fix" in rec:
            out["fix"] = rec["fix"]
        if "hdop_x100" in rec:
            out["hdop"] = rec["hdop_x100"] / 100.0
        for k in ("alt", "speed", "course", "valve", "diff_age",
                  "station_id", "cs"):
            if k in rec:
                out[k] = rec[k]
        return [out]

    if rtype in ("imu", "device_capability"):
        return [rec]

    targets = tmap.resolve(rtype) if isinstance(rtype, str) else ()
    if not targets:
        return [rec]                 # 모르는 문자열 — 버리지 않는다
    target = targets[0]
    ambiguous = len(targets) > 1

    if target.kind == "ain":
        out = _base(rec, "ain", target.connector)
        value_key = target.unit or "value"
        if value_key in rec:
            out["value"] = rec[value_key]
        out["unit"] = target.unit
        for k in ("ma", "raw", "valve"):
            if k in rec:
                out[k] = rec[k]
        if ambiguous:
            out["ambiguous"] = True
        return [out]

    if target.kind == "i2c":
        value_key = _I2C_VALUE_FIELD.get(rtype, "")
        out = _base(rec, "i2c", target.connector)
        out["quantity"] = target.quantity
        if value_key in rec:
            out["value"] = rec[value_key]
        if ambiguous:
            out["ambiguous"] = True
        results = [out]
        # 🔴 주변 온도는 v3 에서 제 레코드였다(J12 MLX 실증 화면) —
        #    계약에서는 temp_road 의 선택 필드라, 화면 카드가 계속 살게
        #    둘로 편다. dewpoint 는 v3 물리량이 아니었으므로 펴지 않는다.
        if rtype == "temp_road" and "ambient_degc" in rec:
            amb = _base(rec, "i2c", target.connector)
            amb["quantity"] = "temp_ambient"
            amb["value"] = rec["ambient_degc"]
            results.append(amb)
        return results

    if target.kind == "din":
        out = _base(rec, "din", target.connector)
        if "state" in rec:
            out["state"] = rec["state"]
        if ambiguous:
            out["ambiguous"] = True
        return [out]

    return [rec]
