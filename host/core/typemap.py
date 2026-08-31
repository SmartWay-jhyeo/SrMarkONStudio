"""카탈로그 역매핑 — 클라우드 타입 문자열 → 어느 채널인가.

🔴 레코드에는 채널 번호가 없다 (사용자 결정 2026-08-30, HANDOFF_0831
   결정 2 보완 — `ch` 필드 대신 "PC 가 이미 보드 파라미터를 갖고 있다").
   GUI 는 접속하자마자 받는 카탈로그($CFG,LIST)에서 이 역매핑을 만들어
   스트림 트리·실시간 mA·영점 조정을 채널에 붙인다.

🔴 KIND_CLOUD 는 펌웨어 `mk_cloud.c` 의 I2C_KIND_TYPES 복제다 — 이중
   정의다(CLAUDE.md 의 screen.py ↔ mk_i2c.c 표와 같은 부류). I2C 종류를
   늘릴 때 셋을 함께 고칠 것: mk_cloud.c · 여기 · screen.py 의
   I2C_KIND_QUANTITIES. test_typemap 이 여기↔screen 을 대조한다.

중복(같은 문자열 두 채널, 동종 I2C 2대)은 가릴 수 없다 — resolve 가
전부를 돌려주고 duplicates() 가 경고의 근거를 준다. 펌웨어는 중복을
막지 않는다(사용자 결정 — GUI 가 카탈로그에서 알아채고 경고).
"""

import re
from dataclasses import dataclass

from host.core.config_schema import ConfigSchema

#: ain 채널 인덱스 ↔ 커넥터 번호 (데이터시트 §5.3 — J3 부터).
AIN_CONNECTOR_OFFSET = 3

#: I2C 종류 → 그 포트가 내는 (클라우드 타입, 화면 물리량) 짝.
#: 순서·값의 근거는 펌웨어 mk_cloud.c I2C_KIND_TYPES + I2C_CLOUD 표.
KIND_CLOUD: dict[int, tuple[tuple[str, str], ...]] = {
    1: (("light", "lux"),),                                # 조도 BH1750
    2: (("temp_air", "temp"), ("humidity", "humidity")),   # 온습도 AM2320
    3: (("temp_road", "temp_object"),),                    # 적외 MLX90614
    4: (("temp_air", "temp"),),                            # 방수 온도
}


@dataclass(frozen=True)
class TypeTarget:
    kind: str          #: "ain" | "i2c" | "din"
    connector: int     #: J번호 (ain: 채널+3, i2c·din: 번호 그대로)
    channel: int       #: ain 채널 인덱스(0..6); i2c·din 은 connector 와 동일
    quantity: str = ""  #: i2c 만 — 화면(screen.py)의 물리량 이름
    unit: str = ""      #: ain 만 — 값 필드의 이름(ainN.unit)


def _current(schema: ConfigSchema, key: str, default=None):
    item = schema.items.get(key)
    return getattr(item, "current", default) if item is not None else default


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "on", "yes")
    return bool(v)


class TypeMap:
    def __init__(self, mapping: dict[str, tuple[TypeTarget, ...]]) -> None:
        self._map = mapping

    @classmethod
    def from_schema(cls, schema: ConfigSchema) -> "TypeMap":
        buckets: dict[str, list[TypeTarget]] = {}

        def add(type_str: str, target: TypeTarget) -> None:
            buckets.setdefault(type_str, []).append(target)

        for key in schema.items:
            m = re.fullmatch(r"ain(\d+)\.cloud", key)
            if m:
                type_str = str(_current(schema, key, "") or "").strip()
                if not type_str:
                    continue          # 빈 값 = 미발행 (계약 §16.6)
                ch = int(m.group(1))
                unit = str(_current(schema, f"ain{ch}.unit", "") or "")
                add(type_str, TypeTarget(
                    kind="ain", connector=ch + AIN_CONNECTOR_OFFSET,
                    channel=ch, unit=unit))
                continue

            m = re.fullmatch(r"i2c(\d+)\.enabled", key)
            if m:
                if not _truthy(_current(schema, key, False)):
                    continue
                port = int(m.group(1))
                kind = int(_current(schema, f"i2c{port}.kind", 0) or 0)
                for type_str, quantity in KIND_CLOUD.get(kind, ()):
                    add(type_str, TypeTarget(
                        kind="i2c", connector=port, channel=port,
                        quantity=quantity))
                continue

            m = re.fullmatch(r"din(\d+)\.cloud", key)
            if m:
                type_str = str(_current(schema, key, "") or "").strip()
                if not type_str:
                    continue
                jack = int(m.group(1))
                add(type_str, TypeTarget(
                    kind="din", connector=jack, channel=jack))

        return cls({
            t: tuple(sorted(targets, key=lambda x: x.connector))
            for t, targets in buckets.items()
        })

    def resolve(self, type_str: str) -> tuple[TypeTarget, ...]:
        """이 타입 문자열이 어느 채널(들)에서 오는가. 모르면 빈 튜플 —
        레코드를 버릴 근거가 아니라 카드에 못 붙일 뿐이다."""
        return self._map.get(type_str, ())

    def duplicates(self) -> dict[str, list[int]]:
        """가릴 수 없는 타입들 — 같은 문자열이 두 채널 이상. GUI 경고의
        근거다("flow1 이 J3·J6 에 중복 — 스트림·영점이 채널을 못 가린다")."""
        return {
            t: [x.connector for x in targets]
            for t, targets in self._map.items()
            if len(targets) > 1
        }
