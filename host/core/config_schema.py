"""$CFG,LIST 응답을 스키마 객체로 바꾸고 값을 검증한다.

호스트는 설정 항목을 하드코딩하지 않는다. 보드가 내려준 카탈로그만으로
화면을 구성하고 값을 검사한다.

규격: protocol/specification.md §7.3
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from host.core.errors import ConfigError, Reason
from host.core.records import parse_record

_INT_TYPES = ("u8", "u16", "u32")
_TRUE_WORDS = ("true", "1", "on", "yes")
_FALSE_WORDS = ("false", "0", "off", "no")


@dataclass(frozen=True)
class ConfigItem:
    key: str
    group: str
    vtype: str
    default: object
    current: object
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    readonly: bool = False
    label: str = ""
    note: str = ""
    choices: tuple = ()


@dataclass(frozen=True)
class FieldBit:
    """NDJSON 필드 마스크의 비트 하나."""

    bit: int
    name: str
    default: bool
    label: str = ""


@dataclass
class ConfigSchema:
    items: dict[str, ConfigItem] = field(default_factory=dict)
    fields: dict[int, FieldBit] = field(default_factory=dict)
    _group_order: list[str] = field(default_factory=list)

    def groups(self) -> list[str]:
        """항목이 처음 나타난 순서대로 그룹 이름을 반환한다."""
        return list(self._group_order)

    def validate(self, key: str, raw: str) -> object:
        """문자열 값을 검사해 파싱된 값을 반환한다.

        검사 순서는 규격 §5 를 따른다 — 키 존재 → 읽기 전용 → 타입·범위.
        인터록은 보드가 판정하므로 호스트에서는 검사하지 않는다.

        Raises:
            ConfigError: reason 이 UNKNOWN_KEY / READONLY / RANGE 중 하나.
        """
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        if item.readonly:
            raise ConfigError(Reason.READONLY, item.note or key)
        return _coerce(item, raw)


def _coerce(item: ConfigItem, raw: str) -> object:
    """vtype 에 맞게 문자열을 변환하고 범위를 검사한다."""
    if item.vtype == "bool":
        low = raw.strip().lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ConfigError(Reason.RANGE, f"불리언이 아님: {raw!r}")

    if item.vtype == "str":
        # 🔴 사용자가 넣는 값은 ASCII 만 통과시킨다.
        # 단위는 'degC'·'kPa'·'LPM' 처럼 쓴다. '℃' 같은 기호를 허용하면
        # 펌웨어의 고정폭 버퍼(str<=7 은 바이트 기준이다)와 호스트의 문자
        # 기준 길이가 어긋나고, 저장 파일 인코딩까지 따라 흔들린다.
        if not raw.isascii():
            bad = [c for c in raw if not c.isascii()]
            raise ConfigError(Reason.RANGE, f"ASCII 만 허용: {bad!r}")
        if item.maximum is not None and len(raw) > int(item.maximum):
            raise ConfigError(
                Reason.RANGE, f"최대 {int(item.maximum)}자, 받음 {len(raw)}자"
            )
        return raw

    if item.vtype == "enum":
        try:
            value: object = int(raw)
        except ValueError:
            value = raw
        if value not in item.choices:
            raise ConfigError(Reason.RANGE, f"허용값 {list(item.choices)}")
        return value

    if item.vtype in _INT_TYPES:
        try:
            # 🔴 base 0 을 쓰면 안 된다. int("08", 0) 은 ValueError 다 —
            # 파이썬이 앞의 0 을 8진수 접두사로 읽기 때문이다. 사용자가
            # 고정폭 습관으로 "08" 을 넣는 건 지극히 자연스럽고, 그게
            # "정수가 아님" 으로 거부되면 원인을 짐작할 수도 없다.
            value = int(raw, 10)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"정수가 아님: {raw!r}") from None
    elif item.vtype == "f32":
        try:
            value = float(raw)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"실수가 아님: {raw!r}") from None
        # 🔴 보드 저장소(config_store)와 같은 규칙이어야 한다.
        # NaN 은 아래 범위 검사를 그대로 뚫는다 — nan < min 도 nan > max 도
        # False 다. 범위가 아예 없는 항목(ain*.zero/scale)은 inf 도 통과한다.
        if not math.isfinite(value):
            raise ConfigError(Reason.RANGE, f"유한한 실수가 아님: {raw!r}")
    else:
        raise ConfigError(Reason.RANGE, f"알 수 없는 타입: {item.vtype}")

    if item.minimum is not None and value < item.minimum:
        raise ConfigError(Reason.RANGE, f"최소 {item.minimum}, 받음 {value}")
    if item.maximum is not None and value > item.maximum:
        raise ConfigError(Reason.RANGE, f"최대 {item.maximum}, 받음 {value}")
    return value


def parse_catalog(lines: Iterable[str]) -> ConfigSchema:
    """$CFG,LIST 응답 줄들을 ConfigSchema 로 모은다.

    Raises:
        ConfigError: cfg_end 의 count 가 실제 항목 수와 다른 경우
            (전송이 중간에 잘린 것으로 본다).
    """
    schema = ConfigSchema()
    declared: int | None = None

    for line in lines:
        rec = parse_record(line)
        rtype = rec["type"]

        if rtype == "cfg_item":
            group = rec.get("grp", "")
            if group not in schema._group_order:
                schema._group_order.append(group)
            schema.items[rec["key"]] = ConfigItem(
                key=rec["key"],
                group=group,
                vtype=rec["vtype"],
                default=rec.get("default"),
                current=rec.get("cur"),
                minimum=rec.get("min"),
                maximum=rec.get("max"),
                unit=rec.get("unit", ""),
                readonly=bool(rec.get("ro", False)),
                label=rec.get("label", ""),
                note=rec.get("note", ""),
                choices=tuple(rec.get("choices", ())),
            )

        elif rtype == "cfg_field":
            schema.fields[rec["bit"]] = FieldBit(
                bit=rec["bit"],
                name=rec["name"],
                default=bool(rec.get("default", False)),
                label=rec.get("label", ""),
            )

        elif rtype == "cfg_end":
            declared = rec["count"]

    # 🔴 종료 줄이 아예 안 온 경우도 절단이다.
    #
    # 오히려 이쪽이 더 흔한 형태다 — cfg_end 는 마지막에 보내므로, 전송이
    # 중간에 끊기면 "개수가 틀린 cfg_end" 가 아니라 "cfg_end 자체가 없음" 이
    # 된다. 이걸 통과시키면 GUI 가 설정 몇 개가 빠진 화면을 아무 경고 없이
    # 정상인 것처럼 그린다.
    if declared is None:
        raise ConfigError(Reason.RANGE, "카탈로그에 cfg_end 가 없음 (전송 절단)")

    total = len(schema.items) + len(schema.fields)
    if declared != total:
        raise ConfigError(
            Reason.RANGE,
            f"카탈로그 개수 불일치: 선언 {declared}, 수신 {total}",
        )
    return schema
